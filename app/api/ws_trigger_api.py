"""WebSocket 触发端点

外部客户端通过 ``ws://host/ws/trigger/{token}`` 连接，
以 JSON 指令驱动 Agent/Flow 执行，并实时接收流式事件。

支持远程工具注册：客户端注册的函数可被 Agent 在执行中反向调用。

并发模型：
- execute 指令按会话级并发控制：Agent 类型同一会话串行（正在执行时拒绝
  新请求），不同会话/新建会话/Flow 类型并发执行
- 并发执行的事件流会交错，每个事件顶层统一携带 call_id（调用记录ID），
  客户端按 call_id 路由事件
- resume 指令恢复等待人工输入的执行：Agent 传 session_id，Flow 传
  execution_id，input 为人工输入内容
- tool_approval 指令确认/拒绝待审批的工具调用（Agent 类型）
- cancel 指令取消正在执行的会话/执行记录
- 连接空闲超过 WS_TRIGGER_IDLE_TIMEOUT 秒未收到任何消息（含 ping）
  时服务端主动断开（关闭码 4408），客户端必须周期性发送 ping

认证方式：token 在 URL 路径中（与原 HTTP 网关的 token 机制一致）。
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, TypeVar

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from starlette.routing import WebSocketRoute

from app.agent_flow.ws_tool_context import (
    _current_ws_conn,
    register_ws_conn,
    unregister_ws_conn,
)
from app.config.database import AsyncSessionLocal
from app.models.flow import FlowType
from app.schemas.ws_command_schema import (
    WsCancelCommand,
    WsCommandView,
    WsCreateSessionCommand,
    WsDeleteMessageCommand,
    WsDeleteSessionCommand,
    WsExecuteCommand,
    WsGetMessagesCommand,
    WsListSessionsCommand,
    WsRegisterToolsCommand,
    WsResumeCommand,
    WsSwitchSessionCommand,
    WsToolApprovalCommand,
    WsToolResultCommand,
)
from app.services.flow_service import flow_service
from app.services.ws_gateway_service import ws_gateway_service

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=WsCommandView)


@dataclass
class WSConnection:
    """一个外部 WS 客户端连接的运行时状态"""

    websocket: WebSocket
    token: str
    gateway_id: int
    flow_id: int
    flow_type: Optional[str]
    gateway_name: str
    input_config: Optional[dict]
    registered_tools: list[dict] = field(default_factory=list)
    pending_calls: dict[str, asyncio.Future] = field(default_factory=dict)
    current_session_id: Optional[int] = None
    # 正在执行的 Agent 会话集合（会话级并发控制，check-then-act 无 await 原子）
    executing_sessions: set[int] = field(default_factory=set)
    tool_timeout: int = 120
    _execute_tasks: set = field(default_factory=set)


async def trigger_ws(websocket: WebSocket):
    """WebSocket 触发端点主函数

    1. token 鉴权 → 校验 gateway_config
    2. accept → 发送 connected 事件
    3. 后台 receiver 循环分发客户端指令
    """
    token = websocket.path_params.get("token", "")

    # ---- token 鉴权 ----
    async with AsyncSessionLocal() as db:
        gateway = await ws_gateway_service.get_by_token(db, token)
        if not gateway:
            await websocket.close(code=4404)
            return
        if not gateway.is_enabled:
            await websocket.close(code=4403)
            return

        flow = await flow_service.get_by_id(db, gateway.flow_id, raise_not_found=False)
        flow_type = flow.flow_type if flow else None

        conn = WSConnection(
            websocket=websocket,
            token=token,
            gateway_id=gateway.id,
            flow_id=gateway.flow_id,
            flow_type=flow_type,
            gateway_name=gateway.name,
            input_config=gateway.input_config,
        )

    # ---- 接受连接 ----
    await websocket.accept()
    # 仅智能体类型占用全局连接槽（供前端 SSE 注入远程工具，且单连接）；
    # 流程类型不注册工具，允许多个网关各自连接
    if conn.flow_type == FlowType.AGENT.value:
        if not register_ws_conn(conn.flow_id, conn):
            await websocket.close(code=4409)
            return
    await websocket.send_json(
        {
            "type": "connected",
            "data": {
                "gateway_id": conn.gateway_id,
                "gateway_name": conn.gateway_name,
                "flow_id": conn.flow_id,
                "flow_type": conn.flow_type,
                "upload_url": f"/api/ws-gateway/upload?token={conn.token}",
                "download_url_template": (
                    f"/api/ws-gateway/download/{{file_id}}?token={conn.token}"
                ),
            },
        }
    )
    logger.info(f"WS trigger 连接: gateway={conn.gateway_name}({conn.gateway_id})")

    # ---- 消息循环 ----
    try:
        await _message_receiver(conn)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"WS trigger 异常: {e}")
    finally:
        unregister_ws_conn(conn.flow_id, conn)
        for future in conn.pending_calls.values():
            if not future.done():
                future.cancel()
        conn.pending_calls.clear()
        for task in conn._execute_tasks:
            task.cancel()
        logger.info(f"WS trigger 断开: gateway_id={conn.gateway_id}")


async def _message_receiver(conn: WSConnection):
    """持续接收消息并分发

    快速指令（register_tools/tool_result/session 管理）同步处理；
    execute/resume/cancel 指令分发为独立 task（不阻塞接收）。
    空闲超过 idle_timeout 未收到任何消息（含 ping）时主动断开，
    防止半开连接永久占用 Agent 网关唯一连接槽。
    """
    from app.config.settings import settings

    idle_timeout = getattr(settings, "ws_trigger_idle_timeout", 0) or 0
    while True:
        if idle_timeout > 0:
            try:
                raw = await asyncio.wait_for(
                    conn.websocket.receive_text(), timeout=idle_timeout
                )
            except asyncio.TimeoutError:
                logger.warning(f"WS trigger 空闲超时断开: gateway_id={conn.gateway_id}")
                try:
                    await _send_error(conn, f"连接空闲超过 {idle_timeout} 秒，已断开")
                except Exception:
                    pass
                await conn.websocket.close(code=4408)
                return
        else:
            raw = await conn.websocket.receive_text()

        if raw == "ping":
            await conn.websocket.send_text("pong")
            continue

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await _send_error(conn, "消息格式错误：需要合法 JSON")
            continue

        action = data.get("action")
        if not action:
            await _send_error(conn, "缺少 action 字段")
            continue

        if action == "tool_result":
            cmd = await _parse_command(conn, WsToolResultCommand, data)
            if cmd:
                _resolve_tool_call(conn, cmd)
        elif action == "register_tools":
            cmd = await _parse_command(conn, WsRegisterToolsCommand, data)
            if cmd:
                await _handle_register_tools(conn, cmd)
        elif action == "unregister_tools":
            conn.registered_tools = []
            await conn.websocket.send_json({"type": "tools_unregistered", "data": {}})
        elif action == "execute":
            cmd = await _parse_command(conn, WsExecuteCommand, data)
            if cmd:
                task = asyncio.create_task(_handle_execute(conn, cmd))
                conn._execute_tasks.add(task)
                task.add_done_callback(conn._execute_tasks.discard)
        elif action == "resume":
            cmd = await _parse_command(conn, WsResumeCommand, data)
            if cmd:
                task = asyncio.create_task(_handle_resume(conn, cmd))
                conn._execute_tasks.add(task)
                task.add_done_callback(conn._execute_tasks.discard)
        elif action == "tool_approval":
            cmd = await _parse_command(conn, WsToolApprovalCommand, data)
            if cmd:
                await _handle_tool_approval(conn, cmd)
        elif action == "cancel":
            cmd = await _parse_command(conn, WsCancelCommand, data)
            if cmd:
                task = asyncio.create_task(_handle_cancel(conn, cmd))
                conn._execute_tasks.add(task)
                task.add_done_callback(conn._execute_tasks.discard)
        elif action == "create_session":
            cmd = await _parse_command(conn, WsCreateSessionCommand, data)
            if cmd:
                await _handle_create_session(conn, cmd)
        elif action == "switch_session":
            cmd = await _parse_command(conn, WsSwitchSessionCommand, data)
            if cmd:
                await _handle_switch_session(conn, cmd)
        elif action == "list_sessions":
            cmd = await _parse_command(conn, WsListSessionsCommand, data)
            if cmd:
                await _handle_list_sessions(conn, cmd)
        elif action == "delete_session":
            cmd = await _parse_command(conn, WsDeleteSessionCommand, data)
            if cmd:
                await _handle_delete_session(conn, cmd)
        elif action == "get_messages":
            cmd = await _parse_command(conn, WsGetMessagesCommand, data)
            if cmd:
                await _handle_get_messages(conn, cmd)
        elif action == "delete_message":
            cmd = await _parse_command(conn, WsDeleteMessageCommand, data)
            if cmd:
                await _handle_delete_message(conn, cmd)
        else:
            await _send_error(conn, f"未知指令: {action}")


async def _parse_command(
    conn: WSConnection, model_cls: type[T], data: dict
) -> Optional[T]:
    """将指令 data 解析为 Command 模型，校验失败时发送错误事件并返回 None"""
    try:
        return model_cls.model_validate(data)
    except ValidationError as e:
        details = "; ".join(
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
            for err in e.errors()
        )
        await _send_error(conn, f"参数校验失败: {details}")
        return None


# ---- 工具相关 ----


def _resolve_tool_call(conn: WSConnection, cmd: WsToolResultCommand):
    """将客户端返回的 tool_result resolve 到对应的 Future"""
    call_id = cmd.call_id
    if not call_id:
        return
    future = conn.pending_calls.pop(call_id, None)
    if future and not future.done():
        if cmd.error:
            future.set_result(
                json.dumps({"success": False, "error": cmd.error}, ensure_ascii=False)
            )
        else:
            future.set_result(cmd.result)
    else:
        logger.warning(f"收到未匹配的 tool_result: call_id={call_id}")


async def _handle_register_tools(conn: WSConnection, cmd: WsRegisterToolsCommand):
    """注册远程工具（仅 Agent 类型生效）"""
    if conn.flow_type != FlowType.AGENT.value:
        await _send_error(conn, "远程工具仅 Agent 类型支持，当前网关关联的不是智能体")
        return
    conn.registered_tools = cmd.tools
    names = [t.get("name", "") for t in cmd.tools]
    await conn.websocket.send_json(
        {"type": "tools_registered", "data": {"count": len(cmd.tools), "names": names}}
    )


# ---- 执行 ----


async def _handle_execute(conn: WSConnection, cmd: WsExecuteCommand):
    """处理 execute 指令：流式执行 + 推送事件（会话级并发控制）

    - Agent + 显式 session_id：同一会话正在执行时拒绝，不同会话并发执行
    - Agent 未指定 session_id（新建会话）：全新会话无冲突，不加锁
    - Flow：每次执行独立 execution/thread_id，无共享状态，不加锁
    """
    explicit_session = cmd.session_id

    lock_session: Optional[int] = None
    if conn.flow_type == FlowType.AGENT.value and explicit_session is not None:
        # check-then-act 中间无 await，asyncio 原子
        if explicit_session in conn.executing_sessions:
            await _send_error(conn, f"会话 {explicit_session} 正在执行中，请等待完成")
            return
        conn.executing_sessions.add(explicit_session)
        lock_session = explicit_session
    try:
        if conn.flow_type == FlowType.AGENT.value and conn.registered_tools:
            _current_ws_conn.set(conn)

        session_id = (
            explicit_session
            if explicit_session is not None
            else conn.current_session_id
        )
        input_data = cmd.build_input_data(conn.input_config)

        record_id: Optional[int] = None
        async for event in ws_gateway_service.stream_execute(
            conn.gateway_id, input_data, session_id=session_id
        ):
            # 并发执行时事件流交错，统一打 call_id 标供客户端路由
            if record_id is None and event.get("type") == "call_started":
                record_id = event.get("data", {}).get("call_id")
            if record_id is not None:
                event.setdefault("call_id", record_id)
            await conn.websocket.send_json(event)
            if event.get("type") == "call_started":
                sid = event.get("data", {}).get("session_id")
                # 并发下 last-writer-wins 有歧义：仅未显式指定会话时记录当前会话
                if sid and explicit_session is None:
                    conn.current_session_id = sid
    except WebSocketDisconnect:
        raise
    except Exception as e:
        logger.exception(f"WS execute 异常: {e}")
        await _send_error(conn, str(e))
    finally:
        if lock_session is not None:
            conn.executing_sessions.discard(lock_session)
        _current_ws_conn.set(None)


async def _handle_resume(conn: WSConnection, cmd: WsResumeCommand):
    """处理 resume 指令：恢复等待人工输入的执行（Agent 传 session_id，Flow 传 execution_id）

    Agent 会话与 execute 共用会话级锁；Flow 由 resume_execution 的 CAS 乐观锁保证并发安全。
    """
    if not cmd.input.strip():
        await _send_error(conn, "缺少 input 字段（人工输入内容）")
        return
    human_input = cmd.input

    key = "session_id" if conn.flow_type == FlowType.AGENT.value else "execution_id"
    target_id = cmd.session_id if key == "session_id" else cmd.execution_id
    if target_id is None:
        await _send_error(conn, f"缺少 {key} 字段")
        return

    lock_session: Optional[int] = None
    if conn.flow_type == FlowType.AGENT.value:
        if target_id in conn.executing_sessions:
            await _send_error(conn, f"会话 {target_id} 正在执行中，请等待完成")
            return
        conn.executing_sessions.add(target_id)
        lock_session = target_id
    try:
        record_id: Optional[int] = None
        async for event in ws_gateway_service.stream_resume(
            conn.gateway_id, target_id, human_input
        ):
            if record_id is None and event.get("type") == "call_started":
                record_id = event.get("data", {}).get("call_id")
            if record_id is not None:
                event.setdefault("call_id", record_id)
            await conn.websocket.send_json(event)
    except WebSocketDisconnect:
        raise
    except Exception as e:
        logger.exception(f"WS resume 异常: {e}")
        await _send_error(conn, str(e))
    finally:
        if lock_session is not None:
            conn.executing_sessions.discard(lock_session)


async def _handle_tool_approval(conn: WSConnection, cmd: WsToolApprovalCommand):
    """处理 tool_approval 指令：确认/拒绝待审批的工具调用（仅 Agent 类型）

    WS 触发的执行中 LLM 调用需审批工具时，事件流下发
    tool_approval_required，客户端征询用户后通过本指令恢复；
    前端 SSE 路径的确认入口在 agent_api，两者共用 tool_approval_service。
    """
    if conn.flow_type != FlowType.AGENT.value:
        await _send_error(conn, "仅 Agent 类型支持工具审批")
        return

    session_id = cmd.session_id

    # 归属校验：会话必须由该网关创建
    async with AsyncSessionLocal() as db:
        _, session = await ws_gateway_service.get_session_by_token(
            db, conn.token, session_id
        )
    if not session:
        await _send_error(conn, f"会话 {session_id} 不存在或不属于该网关")
        return

    from app.services.tool_approval_service import tool_approval_service

    resolved = tool_approval_service.resolve(session_id, cmd.approval_id, cmd.result)
    await conn.websocket.send_json(
        {
            "type": "tool_approval_result",
            "data": {"session_id": session_id, "resolved": resolved},
        }
    )


async def _handle_cancel(conn: WSConnection, cmd: WsCancelCommand):
    """处理 cancel 指令：取消正在执行的会话（Agent）或执行记录（Flow）

    Agent：设置 interrupt 标志（chat_stream 循环检测点自行停止，finally
    负责 checkpoint 清理）+ 取消挂起的工具审批；WS 执行任务不在 SSE 的
    _streaming_tasks 中，不依赖 task.cancel。
    Flow：interrupt 标志 + cancel_execution（状态落库 CANCELLED 并取消运行节点）。
    """
    from app.services.interrupt_service import interrupt_service

    key = "session_id" if conn.flow_type == FlowType.AGENT.value else "execution_id"
    target_id = cmd.session_id if key == "session_id" else cmd.execution_id
    if target_id is None:
        await _send_error(conn, f"缺少 {key} 字段")
        return

    # 归属校验（与 resume 相同规则）
    async with AsyncSessionLocal() as db:
        gateway = await ws_gateway_service.get_by_token(db, conn.token)
        if not gateway:
            await _send_error(conn, "网关不存在")
            return
        valid = await _validate_ws_target(db, gateway, conn.flow_type, target_id)
    if not valid:
        target_desc = "会话" if conn.flow_type == FlowType.AGENT.value else "执行记录"
        await _send_error(conn, f"{target_desc} {target_id} 不存在或不属于该网关")
        return

    if conn.flow_type == FlowType.AGENT.value:
        from app.services.tool_approval_service import tool_approval_service

        interrupt_service.set_agent_interrupted(target_id)
        tool_approval_service.cancel(target_id)
        await conn.websocket.send_json(
            {"type": "cancel_accepted", "data": {"session_id": target_id}}
        )
    else:
        interrupt_service.set_flow_interrupted(target_id)
        from app.services.flow_executor_service import flow_executor_service

        async with AsyncSessionLocal() as db:
            execution = await flow_executor_service.cancel_execution(db, target_id)
        if not execution:
            await _send_error(conn, f"执行记录 {target_id} 不存在或不可取消")
            return
        await conn.websocket.send_json(
            {"type": "cancel_accepted", "data": {"execution_id": target_id}}
        )


async def _validate_ws_target(db, gateway, flow_type: Optional[str], target_id: int):
    """校验目标（Agent 会话 / Flow 执行记录）归属于该网关

    Returns:
        True 归属有效；False 不存在或不属于该网关。
    """
    if flow_type == FlowType.AGENT.value:
        from app.models.agent_session import AgentSession
        from sqlalchemy import select

        stmt = select(AgentSession.id).where(
            AgentSession.id == target_id,
            AgentSession.gateway_id == gateway.id,
            AgentSession.is_delete == 0,
        )
        return (await db.execute(stmt)).scalar_one_or_none() is not None

    from app.services.flow_executor_service import flow_executor_service

    execution = await flow_executor_service.get_execution(db, target_id)
    return bool(execution and execution.flow_id == gateway.flow_id)


# ---- 会话管理 ----


async def _handle_create_session(conn: WSConnection, cmd: WsCreateSessionCommand):
    """创建新会话（仅 Agent 类型）"""
    if conn.flow_type != FlowType.AGENT.value:
        await _send_error(conn, "仅 Agent 类型支持创建会话")
        return
    session_id, session_title = await ws_gateway_service.create_session_for_ws(
        conn.token, cmd.title
    )
    conn.current_session_id = session_id
    await conn.websocket.send_json(
        {
            "type": "session_created",
            "data": {"session_id": session_id, "title": session_title},
        }
    )


async def _handle_switch_session(conn: WSConnection, cmd: WsSwitchSessionCommand):
    """切换当前会话（仅 Agent 类型）"""
    if conn.flow_type != FlowType.AGENT.value:
        await _send_error(conn, "仅 Agent 类型支持会话操作")
        return
    session_id = cmd.session_id
    async with AsyncSessionLocal() as db:
        gateway, session = await ws_gateway_service.get_session_by_token(
            db, conn.token, session_id
        )
    if not session:
        await _send_error(conn, f"会话 {session_id} 不存在或不属于该网关")
        return
    conn.current_session_id = session_id
    await conn.websocket.send_json(
        {"type": "session_switched", "data": {"session_id": session_id}}
    )


async def _handle_list_sessions(conn: WSConnection, cmd: WsListSessionsCommand):
    """查询会话列表（仅 Agent 类型）"""
    if conn.flow_type != FlowType.AGENT.value:
        await _send_error(conn, "仅 Agent 类型支持会话操作")
        return
    page = cmd.page
    page_size = cmd.page_size
    async with AsyncSessionLocal() as db:
        sessions, total = await ws_gateway_service.get_sessions_by_token(
            db, conn.token, page, page_size
        )
    items = [
        {
            "id": s.id,
            "title": s.title,
            "create_time": s.create_time.isoformat() if s.create_time else None,
        }
        for s in sessions
    ]
    await conn.websocket.send_json(
        {"type": "sessions_list", "data": {"sessions": items, "total": total}}
    )


async def _handle_delete_session(conn: WSConnection, cmd: WsDeleteSessionCommand):
    """删除会话（仅 Agent 类型）"""
    if conn.flow_type != FlowType.AGENT.value:
        await _send_error(conn, "仅 Agent 类型支持会话操作")
        return
    session_id = cmd.session_id
    async with AsyncSessionLocal() as db:
        success, msg = await ws_gateway_service.delete_session_by_token(
            db, conn.token, session_id
        )
    if not success:
        await _send_error(conn, msg)
        return
    if conn.current_session_id == session_id:
        conn.current_session_id = None
    await conn.websocket.send_json(
        {"type": "session_deleted", "data": {"session_id": session_id}}
    )


async def _handle_get_messages(conn: WSConnection, cmd: WsGetMessagesCommand):
    """查询会话历史消息（仅 Agent 类型）"""
    if conn.flow_type != FlowType.AGENT.value:
        await _send_error(conn, "仅 Agent 类型支持会话操作")
        return
    session_id = cmd.session_id
    before_id = cmd.before_id
    limit = cmd.limit
    async with AsyncSessionLocal() as db:
        messages, total = await ws_gateway_service.get_session_messages_by_token(
            db, conn.token, session_id, before_id, limit
        )
    items = [_serialize_message(m) for m in messages]
    await conn.websocket.send_json(
        {"type": "messages_list", "data": {"messages": items, "total": total}}
    )


async def _handle_delete_message(conn: WSConnection, cmd: WsDeleteMessageCommand):
    """删除会话消息（仅 Agent 类型）"""
    if conn.flow_type != FlowType.AGENT.value:
        await _send_error(conn, "仅 Agent 类型支持会话操作")
        return
    session_id = cmd.session_id
    message_id = cmd.message_id
    async with AsyncSessionLocal() as db:
        success, msg = await ws_gateway_service.delete_session_message_by_token(
            db, conn.token, session_id, message_id
        )
    if not success:
        await _send_error(conn, msg)
        return
    await conn.websocket.send_json(
        {
            "type": "message_deleted",
            "data": {"session_id": session_id, "message_id": message_id},
        }
    )


# ---- 辅助 ----


def _serialize_message(m: Any) -> dict:
    """将消息对象序列化为 dict"""
    input_data = m.input_data if isinstance(m.input_data, dict) else {}
    return {
        "id": m.id,
        "role": m.role,
        "message_type": m.message_type,
        "content": m.content,
        "removed_count": (
            input_data.get("removed_count")
            if m.message_type == "context_summary"
            else None
        ),
        "create_time": m.create_time.isoformat() if m.create_time else None,
    }


async def _send_error(conn: WSConnection, message: str):
    """发送错误事件"""
    try:
        await conn.websocket.send_json({"type": "error", "data": {"message": message}})
    except Exception:
        pass


def register_trigger_ws_routes(app):
    """注册 WS 触发路由（绕过全局 HTTPBearer 依赖）"""
    app.router.routes.insert(
        0, WebSocketRoute("/ws/trigger/{token}", endpoint=trigger_ws)
    )

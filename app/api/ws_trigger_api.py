"""WebSocket 触发端点

外部客户端通过 ``ws://host/ws/trigger/{token}`` 连接，
以 JSON 指令驱动 Agent/Flow 执行，并实时接收流式事件。

支持远程工具注册：客户端注册的函数可被 Agent 在执行中反向调用。

认证方式：token 在 URL 路径中（与原 HTTP webhook 的 token 机制一致）。
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import WebSocket, WebSocketDisconnect
from starlette.routing import WebSocketRoute

from app.agent_flow.ws_tool_context import _current_ws_conn
from app.config.database import AsyncSessionLocal
from app.models.flow import FlowType
from app.services.flow_service import flow_service
from app.services.webhook_service import webhook_service

logger = logging.getLogger(__name__)


@dataclass
class WSConnection:
    """一个外部 WS 客户端连接的运行时状态"""

    websocket: WebSocket
    token: str
    webhook_id: int
    flow_id: int
    flow_type: Optional[str]
    webhook_name: str
    input_config: Optional[dict]
    registered_tools: list[dict] = field(default_factory=list)
    pending_calls: dict[str, asyncio.Future] = field(default_factory=dict)
    current_session_id: Optional[int] = None
    executing: bool = False
    tool_timeout: int = 120
    _execute_tasks: set = field(default_factory=set)


async def trigger_ws(websocket: WebSocket):
    """WebSocket 触发端点主函数

    1. token 鉴权 → 校验 webhook_config
    2. accept → 发送 connected 事件
    3. 后台 receiver 循环分发客户端指令
    """
    token = websocket.path_params.get("token", "")

    # ---- token 鉴权 ----
    async with AsyncSessionLocal() as db:
        webhook = await webhook_service.get_by_token(db, token)
        if not webhook:
            await websocket.close(code=4404)
            return
        if not webhook.is_enabled:
            await websocket.close(code=4403)
            return

        flow = await flow_service.get_by_id(db, webhook.flow_id, raise_not_found=False)
        flow_type = flow.flow_type if flow else None

        conn = WSConnection(
            websocket=websocket,
            token=token,
            webhook_id=webhook.id,
            flow_id=webhook.flow_id,
            flow_type=flow_type,
            webhook_name=webhook.name,
            input_config=webhook.input_config,
        )

    # ---- 接受连接 ----
    await websocket.accept()
    await websocket.send_json(
        {
            "type": "connected",
            "data": {
                "webhook_id": conn.webhook_id,
                "webhook_name": conn.webhook_name,
                "flow_id": conn.flow_id,
                "flow_type": conn.flow_type,
            },
        }
    )
    logger.info(f"WS trigger 连接: webhook={conn.webhook_name}({conn.webhook_id})")

    # ---- 消息循环 ----
    try:
        await _message_receiver(conn)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"WS trigger 异常: {e}")
    finally:
        for future in conn.pending_calls.values():
            if not future.done():
                future.cancel()
        conn.pending_calls.clear()
        for task in conn._execute_tasks:
            task.cancel()
        logger.info(f"WS trigger 断开: webhook_id={conn.webhook_id}")


async def _message_receiver(conn: WSConnection):
    """持续接收消息并分发

    快速指令（register_tools/tool_result/session 管理）同步处理；
    execute 指令分发为独立 task（不阻塞接收）。
    """
    while True:
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
            _resolve_tool_call(conn, data)
        elif action == "register_tools":
            await _handle_register_tools(conn, data)
        elif action == "unregister_tools":
            conn.registered_tools = []
            await conn.websocket.send_json({"type": "tools_unregistered", "data": {}})
        elif action == "execute":
            task = asyncio.create_task(_handle_execute(conn, data))
            conn._execute_tasks.add(task)
            task.add_done_callback(conn._execute_tasks.discard)
        elif action == "create_session":
            await _handle_create_session(conn, data)
        elif action == "switch_session":
            await _handle_switch_session(conn, data)
        elif action == "list_sessions":
            await _handle_list_sessions(conn, data)
        elif action == "delete_session":
            await _handle_delete_session(conn, data)
        elif action == "get_messages":
            await _handle_get_messages(conn, data)
        elif action == "delete_message":
            await _handle_delete_message(conn, data)
        else:
            await _send_error(conn, f"未知指令: {action}")


# ---- 工具相关 ----


def _resolve_tool_call(conn: WSConnection, data: dict):
    """将客户端返回的 tool_result resolve 到对应的 Future"""
    call_id = data.get("call_id")
    if not call_id:
        return
    future = conn.pending_calls.pop(call_id, None)
    if future and not future.done():
        error = data.get("error")
        if error:
            future.set_result(
                json.dumps({"success": False, "error": error}, ensure_ascii=False)
            )
        else:
            future.set_result(data.get("result"))
    else:
        logger.warning(f"收到未匹配的 tool_result: call_id={call_id}")


async def _handle_register_tools(conn: WSConnection, data: dict):
    """注册远程工具（仅 Agent 类型生效）"""
    tools = data.get("tools", [])
    if not isinstance(tools, list):
        await _send_error(conn, "tools 必须是数组")
        return
    if conn.flow_type != FlowType.AGENT.value:
        await _send_error(
            conn, "远程工具仅 Agent 类型支持，当前 Webhook 关联的不是智能体"
        )
        return
    conn.registered_tools = tools
    names = [t.get("name", "") for t in tools if isinstance(t, dict)]
    await conn.websocket.send_json(
        {"type": "tools_registered", "data": {"count": len(tools), "names": names}}
    )


# ---- 执行 ----


async def _handle_execute(conn: WSConnection, data: dict):
    """处理 execute 指令：流式执行 + 推送事件"""
    if conn.executing:
        await _send_error(conn, "正在执行中，请等待完成")
        return
    conn.executing = True
    try:
        if conn.flow_type == FlowType.AGENT.value and conn.registered_tools:
            _current_ws_conn.set(conn)

        session_id = data.get("session_id") or conn.current_session_id
        input_data = {**(conn.input_config or {})}
        for k, v in data.items():
            if k not in ("action", "session_id"):
                input_data[k] = v

        async for event in webhook_service.stream_execute(
            conn.webhook_id, input_data, session_id=session_id
        ):
            await conn.websocket.send_json(event)
            if event.get("type") == "call_started":
                sid = event.get("data", {}).get("session_id")
                if sid:
                    conn.current_session_id = sid
    except WebSocketDisconnect:
        raise
    except Exception as e:
        logger.exception(f"WS execute 异常: {e}")
        await _send_error(conn, str(e))
    finally:
        conn.executing = False
        _current_ws_conn.set(None)


# ---- 会话管理 ----


async def _handle_create_session(conn: WSConnection, data: dict):
    """创建新会话（仅 Agent 类型）"""
    if conn.flow_type != FlowType.AGENT.value:
        await _send_error(conn, "仅 Agent 类型支持创建会话")
        return
    title = data.get("title")
    session_id, session_title = await webhook_service.create_session_for_ws(
        conn.token, title
    )
    conn.current_session_id = session_id
    await conn.websocket.send_json(
        {
            "type": "session_created",
            "data": {"session_id": session_id, "title": session_title},
        }
    )


async def _handle_switch_session(conn: WSConnection, data: dict):
    """切换当前会话（仅 Agent 类型）"""
    if conn.flow_type != FlowType.AGENT.value:
        await _send_error(conn, "仅 Agent 类型支持会话操作")
        return
    session_id = data.get("session_id")
    if not session_id:
        await _send_error(conn, "缺少 session_id")
        return
    async with AsyncSessionLocal() as db:
        webhook, session = await webhook_service.get_session_by_token(
            db, conn.token, session_id
        )
    if not session:
        await _send_error(conn, f"会话 {session_id} 不存在或不属于该 Webhook")
        return
    conn.current_session_id = session_id
    await conn.websocket.send_json(
        {"type": "session_switched", "data": {"session_id": session_id}}
    )


async def _handle_list_sessions(conn: WSConnection, data: dict):
    """查询会话列表（仅 Agent 类型）"""
    if conn.flow_type != FlowType.AGENT.value:
        await _send_error(conn, "仅 Agent 类型支持会话操作")
        return
    page = data.get("page", 1)
    page_size = data.get("page_size", 20)
    async with AsyncSessionLocal() as db:
        sessions, total = await webhook_service.get_sessions_by_token(
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


async def _handle_delete_session(conn: WSConnection, data: dict):
    """删除会话（仅 Agent 类型）"""
    if conn.flow_type != FlowType.AGENT.value:
        await _send_error(conn, "仅 Agent 类型支持会话操作")
        return
    session_id = data.get("session_id")
    if not session_id:
        await _send_error(conn, "缺少 session_id")
        return
    async with AsyncSessionLocal() as db:
        success, msg = await webhook_service.delete_session_by_token(
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


async def _handle_get_messages(conn: WSConnection, data: dict):
    """查询会话历史消息（仅 Agent 类型）"""
    if conn.flow_type != FlowType.AGENT.value:
        await _send_error(conn, "仅 Agent 类型支持会话操作")
        return
    session_id = data.get("session_id")
    if not session_id:
        await _send_error(conn, "缺少 session_id")
        return
    before_id = data.get("before_id")
    limit = data.get("limit", 20)
    async with AsyncSessionLocal() as db:
        messages, total = await webhook_service.get_session_messages_by_token(
            db, conn.token, session_id, before_id, limit
        )
    items = [_serialize_message(m) for m in messages]
    await conn.websocket.send_json(
        {"type": "messages_list", "data": {"messages": items, "total": total}}
    )


async def _handle_delete_message(conn: WSConnection, data: dict):
    """删除会话消息（仅 Agent 类型）"""
    if conn.flow_type != FlowType.AGENT.value:
        await _send_error(conn, "仅 Agent 类型支持会话操作")
        return
    session_id = data.get("session_id")
    message_id = data.get("message_id")
    if not session_id or not message_id:
        await _send_error(conn, "缺少 session_id 或 message_id")
        return
    async with AsyncSessionLocal() as db:
        success, msg = await webhook_service.delete_session_message_by_token(
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
    return {
        "id": getattr(m, "id", None),
        "role": getattr(m, "role", None),
        "content": getattr(m, "content", None),
        "create_time": getattr(m, "create_time", None).isoformat()
        if getattr(m, "create_time", None)
        else None,
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

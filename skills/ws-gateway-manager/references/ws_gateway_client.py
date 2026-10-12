"""WS 网关生产级 Python 客户端（标准底座）。

合并自两处实践：
- skills/ws-gateway-manager/references/ws_client_example.py 的示例类（协议覆盖面）
- wechat-bot 项目 ws_client.py（生产踩坑修复版）

相比示例类修复的问题（均已实测踩坑）：
1. 工具注册从 connect() 拆出：connect() 内注册时调用方往往还没 tool()，
   schemas 为空导致注册被静默跳过 → 改为显式 register_tools()
2. result 序列化：示例类 str(result) 会把 dict 变 Python repr（非 JSON），
   Agent 收到脏数据 → dict/list 用 json.dumps(ensure_ascii=False)
3. 流式文本收集：execute 返回时注入 _collected_content（node_content 累积，
   跳过 start/end 节点），调用方无需自己拼流式片段
4. 断线解锁：ConnectionClosed 时解锁全部挂起 Future（否则永久挂起）
5. WsError 异常：携带 error_code（机器可读枚举），禁止匹配 message 文本
6. ConnectionError vs RuntimeError：连接级错误抛 ConnectionError，
   让外层重连循环能识别（RuntimeError 会被 except Exception 吞掉）
7. async 工具函数：iscoroutine 检查并 await（否则返回 coroutine 未驱动）
8. register_tools ack 走 Future：后台 _loop 独占 recv，任何地方再 recv
   会与它抢 socket，导致后续事件全堵

对外 API：
    client = WsGatewayWSClient("ws://host/ws/trigger/TOKEN")
    info = await client.connect()          # connected 事件 data
    client.tool("fn_name", "描述", schema, fn)  # 注册本地函数（可多次）
    await client.register_tools()          # 显式注册（Agent 类型）
    event = await client.execute("你好")   # flow_done/error 事件（含 _collected_content）
    await client.resume(session_id=5, input="同意")     # 人工交互/审批恢复
    await client.tool_approval(session_id=5, result="approved")
    sid = await client.create_session("标题")
    await client.cancel(session_id=5)
    await client.close()

依赖：pip install websockets
"""

import asyncio
import json
import logging
from typing import Any, Callable, Optional

import websockets

logger = logging.getLogger(__name__)


class WsError(Exception):
    """ws-gateway 服务端 error 事件的客户端异常。

    携带 error_code（机器可读枚举）和原始 message。
    客户端必须按 error_code 决定处理（重建会话/重试/报错），
    禁止匹配 message 字符串（错误文案会变）。
    """

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(f"[{error_code}] {message}")

    def __repr__(self) -> str:
        return f"WsError(error_code={self.error_code!r}, message={self.message!r})"


class WsGatewayWSClient:
    """WS 网关客户端：心跳保活 + 远程工具 + 会话管理 + 人工交互/审批恢复。

    - 心跳：默认每 30 秒 ping（服务端默认 120 秒空闲断开，关闭码 4408）
    - 并发：execute 可同时发起多个，事件按顶层 call_id 路由到各自 Future
    - 接收模型：单 _loop 独占 recv（websockets 只允许一个 recv 协程），
      所有响应按事件类型路由到对应 Future
    """

    def __init__(
        self,
        url: str,
        heartbeat_interval: int = 30,
        on_event: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.url = url
        self.heartbeat_interval = heartbeat_interval
        # 事件回调：所有原始事件（含未被内部路由的 tool_approval_required /
        # waiting_human / human_input_required 等）都会透传给调用方，
        # 用于实现审批弹窗、人工输入等交互逻辑
        self.on_event = on_event
        self.ws: Optional["websockets.WebSocketClientProtocol"] = None
        self.flow_type: Optional[str] = None
        self.gateway_info: dict[str, Any] = {}

        self._funcs: dict[str, Callable[..., Any]] = {}
        self._schemas: list[dict[str, Any]] = []

        # execute 路由：FIFO pending -> call_id -> Future；配套流式文本累积
        self._pending_executes: list[asyncio.Future] = []
        self._active_calls: dict[str, asyncio.Future] = {}
        self._call_contents: dict[str, list[str]] = {}

        # 单 Future 路由：session 创建 / 工具注册 ack
        self._pending_session: Optional[asyncio.Future] = None
        self._pending_register: Optional[asyncio.Future] = None

        self._heartbeat_task: Optional[asyncio.Task] = None
        self._loop_task: Optional[asyncio.Task] = None
        self._closed = False

    # ── 工具注册 ────────────────────────────────────────────────

    def tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        func: Callable[..., Any],
    ) -> None:
        """登记本地函数为远程工具（此时不发网络请求）。

        parameters 必须是合法 JSON Schema（type: object / properties / required）；
        description 是 LLM 决定何时调用的依据，必须清晰。
        """
        self._funcs[name] = func
        self._schemas.append(
            {"name": name, "description": description, "parameters": parameters}
        )

    async def register_tools(self) -> None:
        """把已登记的工具注册到网关（覆盖式，需一次发全部）。

        调用顺序：connect() → tool()*N → register_tools()。
        仅 Agent 类型有效；Flow 类型跳过（服务端不支持）。
        """
        if not self._schemas:
            logger.info("无工具需要注册（_schemas 为空）")
            return
        if self.flow_type != "agent":
            logger.warning(
                "远程工具仅 Agent 类型支持，当前为 %s，跳过注册", self.flow_type
            )
            return
        if not self.ws or self._closed:
            raise ConnectionError("ws 未连接，无法注册工具")
        if self._pending_register and not self._pending_register.done():
            raise RuntimeError("已有挂起的 register_tools 操作")

        loop = asyncio.get_event_loop()
        self._pending_register = loop.create_future()
        try:
            await self.ws.send(
                json.dumps({"action": "register_tools", "tools": self._schemas})
            )
            ack = await self._pending_register
            if ack.get("type") == "error":
                d = ack.get("data", {})
                raise WsError(d.get("error_code", "UNKNOWN"), d.get("message", ""))
            data = ack.get("data", {}) or {}
            logger.info(
                "已注册 %d 个远程工具: %s",
                data.get("count", len(self._schemas)),
                data.get("names", [s.get("name", "") for s in self._schemas]),
            )
        finally:
            self._pending_register = None

    # ── 连接管理 ────────────────────────────────────────────────

    async def connect(self) -> dict[str, Any]:
        """连接并完成握手（收 connected 帧），启动心跳与后台接收循环。

        返回 connected 事件 data（gateway_id / flow_id / flow_type /
        upload_url / download_url_template）。**不含工具注册**——
        注册用 tool() + register_tools() 显式完成。
        """
        logger.info("连接 ws 网关: %s", self.url)
        self.ws = await websockets.connect(self.url)

        conn = json.loads(await self.ws.recv())
        if conn.get("type") != "connected":
            raise RuntimeError(f"连接握手失败: {conn}")

        data = conn["data"]
        self.flow_type = data.get("flow_type")
        self.gateway_info = data
        logger.info(
            "已连接 gateway=%s flow_type=%s flow_id=%s",
            data.get("gateway_name"),
            self.flow_type,
            data.get("flow_id"),
        )

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._loop_task = asyncio.create_task(self._loop())
        return data

    async def close(self) -> None:
        """关闭连接并解锁全部挂起 Future。"""
        self._closed = True
        for task in (self._heartbeat_task, self._loop_task):
            if task:
                task.cancel()
        for fut in self._pending_executes:
            if not fut.done():
                fut.cancel()
        for fut in self._active_calls.values():
            if not fut.done():
                fut.cancel()
        for fut in (self._pending_session, self._pending_register):
            if fut and not fut.done():
                fut.cancel()
        self._call_contents.clear()
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
        logger.info("ws 连接已关闭")

    async def _heartbeat_loop(self) -> None:
        """周期 ping，避免服务端空闲超时（关闭码 4408）。"""
        try:
            while not self._closed:
                await asyncio.sleep(self.heartbeat_interval)
                if self.ws and not self._closed:
                    await self.ws.send("ping")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("心跳异常: %s", e)

    # ── 后台接收循环 ────────────────────────────────────────────

    async def _loop(self) -> None:
        """独占 ws.recv 的后台循环：按事件类型路由到对应 Future。"""
        try:
            async for raw in self.ws:
                if raw == "pong":
                    continue
                e = json.loads(raw)
                t = e.get("type")
                d = e.get("data") or {}

                if t == "tool_invoke":
                    await self._handle_tool_invoke(d)
                    continue

                if t == "call_started":
                    cid = d.get("call_id")
                    if self._pending_executes and cid is not None:
                        self._active_calls[cid] = self._pending_executes.pop(0)
                        self._call_contents[cid] = []
                    continue

                if t == "session_created":
                    if self._pending_session and not self._pending_session.done():
                        self._pending_session.set_result(d.get("session_id"))
                    continue

                if t == "tools_registered":
                    if self._pending_register and not self._pending_register.done():
                        self._pending_register.set_result(e)
                    continue

                # 流式文本累积（跳过 start/end；end 通常是结构化输出）
                if t == "node_content":
                    cid = e.get("call_id")
                    node_key = d.get("node_key")
                    chunk = d.get("content")
                    if cid and chunk and node_key not in ("start", "end"):
                        self._call_contents.setdefault(cid, []).append(chunk)
                    continue

                # 只有 flow_done / error 能终止 execute；
                # 无 call_id 的 error 路由优先级：pending_session → pending_register
                # → execute FIFO（call_started 前的会话校验失败）→ 记日志丢弃
                cid = e.get("call_id")
                if t in ("flow_done", "error"):
                    if cid:
                        fut = self._active_calls.pop(cid, None)
                        if fut and not fut.done():
                            fut.set_result(e)
                            continue
                    elif (
                        t == "error"
                        and self._pending_session
                        and not self._pending_session.done()
                    ):
                        self._pending_session.set_exception(
                            WsError(
                                d.get("error_code", "UNKNOWN"), d.get("message", "")
                            )
                        )
                        continue
                    elif (
                        t == "error"
                        and self._pending_register
                        and not self._pending_register.done()
                    ):
                        self._pending_register.set_result(e)
                        continue
                    elif self._pending_executes:
                        fut = self._pending_executes.pop(0)
                        if not fut.done():
                            fut.set_result(e)
                            continue
                    else:
                        logger.warning(
                            "[WS] 收到无 call_id 的 %s 事件但无 pending future: %s",
                            t,
                            e,
                        )

                # 全量事件透传（审批/人工交互等交互事件靠它驱动调用方逻辑）
                if self.on_event:
                    try:
                        self.on_event(e)
                    except Exception:
                        logger.exception("on_event 回调异常")
        except asyncio.CancelledError:
            pass
        except websockets.ConnectionClosed as e:
            logger.warning("ws 连接被服务端关闭: %s", e)
            self._closed = True
            self._fail_all_pending("CONNECTION_CLOSED", f"ws 连接已关闭: {e}")

    def _fail_all_pending(self, code: str, message: str) -> None:
        """断线时解锁全部挂起 Future（否则永久挂起）。"""
        for fut in list(self._active_calls.values()):
            if not fut.done():
                fut.set_exception(WsError(code, message))
        for fut in self._pending_executes:
            if not fut.done():
                fut.set_exception(WsError(code, message))
        for fut in (self._pending_session, self._pending_register):
            if fut and not fut.done():
                fut.set_exception(WsError(code, message))

    async def _handle_tool_invoke(self, data: dict[str, Any]) -> None:
        """执行本地工具并回传结果。result 必须是字符串（协议约束）。"""
        fn = self._funcs.get(data["name"])
        if not fn:
            result_str = f"未知工具: {data['name']}"
        else:
            try:
                ret = fn(**data.get("args", {}))
                if asyncio.iscoroutine(ret):
                    ret = await ret
            except Exception as e:
                logger.exception("工具 %s 执行异常", data["name"])
                ret = {"ok": False, "error": f"工具执行异常: {e}"}

            if isinstance(ret, str):
                result_str = ret
            elif isinstance(ret, (dict, list)):
                result_str = json.dumps(ret, ensure_ascii=False)
            else:
                result_str = str(ret)

        await self.ws.send(
            json.dumps(
                {
                    "action": "tool_result",
                    "call_id": data["call_id"],
                    "result": result_str,
                }
            )
        )

    def _require_connected(self) -> None:
        if not self.ws or self._closed:
            # ConnectionError（非 RuntimeError）：让外层重连循环能识别，
            # 避免被调用方的 except Exception 吞掉后断线事实丢失
            raise ConnectionError(
                f"ws 未连接（self.ws={self.ws}, _closed={self._closed}）"
            )

    def _finalize_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """execute/resume 共用的事件后处理：error 抛 WsError；成功注入流式文本。"""
        if event.get("type") == "error":
            d = event.get("data") or {}
            # 服务端部分错误（如 LLM 供应商 402）不带 error_code，兜底 INTERNAL_ERROR
            raise WsError(d.get("error_code") or "INTERNAL_ERROR", d.get("message", ""))
        cid = event.get("call_id")
        if cid and cid in self._call_contents:
            event = dict(event)
            event["_collected_content"] = "".join(self._call_contents.pop(cid))
        return event

    # ── 对外 API ────────────────────────────────────────────────

    async def execute(
        self,
        message: Optional[str] = None,
        session_id: Optional[int] = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """触发执行，返回 flow_done / error 事件。

        返回事件注入 _collected_content（流式 node_content 累积文本，
        跳过 start/end 节点），调用方直接取用无需自己拼接。
        files / params 等额外字段通过 **extra 传递（如
        execute("看这张图", files=[{"id": 42, "mime_type": "image/png"}])）。

        异常层级：
          - ConnectionError：连接级错误（外层重连循环据此触发重连）
          - WsError：服务端业务 error（SESSION_BUSY / SESSION_INVALID 等）
        """
        self._require_connected()
        payload: dict[str, Any] = {"action": "execute"}
        if message is not None:
            payload["message"] = message
        if session_id is not None:
            payload["session_id"] = session_id
        payload.update(extra)

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_executes.append(fut)
        await self.ws.send(json.dumps(payload))
        event = await fut
        return self._finalize_event(event)

    async def create_session(self, title: str = "") -> int:
        """创建新会话，返回 session_id。"""
        self._require_connected()
        if self._pending_session and not self._pending_session.done():
            raise RuntimeError("已有挂起的 session 操作")

        loop = asyncio.get_event_loop()
        self._pending_session = loop.create_future()
        await self.ws.send(json.dumps({"action": "create_session", "title": title}))
        return await self._pending_session

    async def resume(
        self,
        *,
        session_id: Optional[int] = None,
        execution_id: Optional[int] = None,
        input: str = "",
    ) -> dict[str, Any]:
        """恢复等待人工输入的执行（human_input_required / waiting_human）。

        Agent 传 session_id；Flow 从 waiting_human 事件取 execution_id。
        可能多轮交互（resume 后再次暂停），继续调用即可。
        """
        self._require_connected()
        payload: dict[str, Any] = {"action": "resume", "input": input}
        if session_id is not None:
            payload["session_id"] = session_id
        if execution_id is not None:
            payload["execution_id"] = execution_id
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_executes.append(fut)
        await self.ws.send(json.dumps(payload))
        event = await fut
        return self._finalize_event(event)

    async def tool_approval(
        self, *, session_id: int, approval_id: str, result: str
    ) -> dict[str, Any]:
        """确认/拒绝待审批的工具调用（result: approved / rejected）。

        approval_id 必填：从 tool_approval_required 事件的 data.approval_id 取
        （服务端按 (session_id, approval_id) 精确路由，缺失报 INVALID_PARAMS）。
        回执 tool_approval_result 通过 on_event 回调或事件流获取；
        无待审批时 resolved=false，可安全忽略。
        """
        self._require_connected()
        await self.ws.send(
            json.dumps(
                {
                    "action": "tool_approval",
                    "session_id": session_id,
                    "approval_id": approval_id,
                    "result": result,
                }
            )
        )
        # 回执 tool_approval_result 走 _loop → on_event 回调，由调用方处理
        return {}

    async def cancel(
        self,
        *,
        session_id: Optional[int] = None,
        execution_id: Optional[int] = None,
    ) -> None:
        """取消正在执行的会话（Agent）或执行记录（Flow）。

        受理后服务端推 cancel_accepted，随后 flow_done(status=cancelled)
        会路由到对应的 execute Future。
        """
        self._require_connected()
        payload: dict[str, Any] = {"action": "cancel"}
        if session_id is not None:
            payload["session_id"] = session_id
        if execution_id is not None:
            payload["execution_id"] = execution_id
        await self.ws.send(json.dumps(payload))

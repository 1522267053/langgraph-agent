"""
Gateway WebSocket 客户端示例

依赖：pip install websockets
用法：python ws_client_example.py [示例编号] [session_id]
"""

import asyncio
import json
import os
import sys
from datetime import datetime

import websockets

# Windows 控制台默认 GBK，LLM 输出含 emoji 时报 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SERVER_HOST = os.environ.get("WS_HOST", "127.0.0.1:8000")
WS_TOKEN = os.environ.get("WS_TOKEN", "YOUR_WS_TOKEN_HERE")


def _url():
    return f"ws://{SERVER_HOST}/ws/trigger/{WS_TOKEN}"


# ---- 公共工具 ----


async def _send(ws, **data):
    await ws.send(json.dumps(data))


async def _recv(ws):
    return json.loads(await ws.recv())


def _on_content(e):
    print(e["data"]["content"], end="", flush=True)


async def _drain(ws, *, on_content=_on_content, on_tool_invoke=None):
    """接收事件直到 flow_done / error，返回最终事件"""
    async for raw in ws:
        e = json.loads(raw)
        t = e["type"]
        if t == "node_content" and on_content:
            on_content(e)
        elif t == "tool_invoke" and on_tool_invoke:
            await on_tool_invoke(e["data"])
        elif t == "flow_done":
            print(f"\n[flow_done] status={e['data'].get('status')} output={json.dumps(e['data'].get('output_data'), ensure_ascii=False)[:200]}")
            return e
        elif t == "error":
            print(f"\n[错误] {e['data']['message']}")
            return e
    return None


async def _connect():
    """连接并返回 (ws, connected_data)，失败返回 (None, None)"""
    ws = await websockets.connect(_url())
    conn = await _recv(ws)
    return ws, conn["data"]


def _check_agent(conn_data):
    if conn_data.get("flow_type") != "agent":
        print(f"[错误] 需要「智能体」类型 Gateway，当前为 {conn_data.get('flow_type')}")
        return False
    return True


# ============================================================
# 示例 1：最简执行
# ============================================================


async def example_simple():
    """连接 → 发消息 → 逐 token 接收回复"""
    print("\n=== 示例 1：最简执行 ===")
    async with websockets.connect(_url()) as ws:
        conn = await _recv(ws)
        print(f"[已连接] {conn['data']['gateway_name']}")
        await _send(ws, action="execute", message="你好，介绍一下你自己")
        await _drain(ws)
        print()


# ============================================================
# 示例 2：远程工具注册 + 回调
# ============================================================


async def example_remote_tools():
    """注册本地函数，Agent 执行中反向调用

    注意：Gateway 必须关联「智能体」。
    """
    print("\n=== 示例 2：远程工具 ===")

    def get_local_time():
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def calculate(expression):
        try:
            return str(eval(expression))
        except Exception as e:
            return f"计算错误: {e}"

    handlers = {
        "get_local_time": lambda **kw: get_local_time(),
        "calculate": lambda **kw: calculate(kw.get("expression", "")),
    }
    tool_defs = [
        {
            "name": "get_local_time",
            "description": "获取客户端本地时间",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "calculate",
            "description": "在客户端执行数学计算",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "数学表达式"}
                },
                "required": ["expression"],
            },
        },
    ]

    async with websockets.connect(_url()) as ws:
        conn_data = (await _recv(ws))["data"]
        if not _check_agent(conn_data):
            return

        await _send(ws, action="register_tools", tools=tool_defs)
        reg = await _recv(ws)
        if reg["type"] == "error":
            return
        print(f"[工具注册] {reg['data']['names']}")

        async def on_tool_invoke(data):
            name, call_id = data["name"], data["call_id"]
            result = handlers.get(name, lambda **kw: "未知工具")(**data.get("args", {}))
            print(f"\n  [调用] {name} → {result}")
            await _send(ws, action="tool_result", call_id=call_id, result=str(result))

        await _send(ws, action="execute", message="现在几点？然后帮我算一下 123 * 456")
        await _drain(ws, on_tool_invoke=on_tool_invoke)
        print()


# ============================================================
# 示例 3：多会话管理
# ============================================================


async def example_sessions():
    """创建会话 → 多轮对话 → 切换 → 列表

    注意：Gateway 必须关联「智能体」。
    """
    print("\n=== 示例 3：会话管理 ===")
    async with websockets.connect(_url()) as ws:
        conn_data = (await _recv(ws))["data"]
        if not _check_agent(conn_data):
            return

        async def create(title):
            await _send(ws, action="create_session", title=title)
            r = await _recv(ws)
            if r["type"] == "error":
                print(f"[错误] {r['data']['message']}")
                return None
            return r["data"]["session_id"]

        async def chat(msg, sid, label=""):
            await _send(ws, action="execute", message=msg, session_id=sid)
            print(f"  {label} " if label else "", end="")
            await _drain(ws)
            print()

        s1 = await create("技术讨论")
        if not s1:
            return
        print(f"[会话 1] id={s1}")
        await chat("记住：我叫张三，我是工程师", s1)

        s2 = await create("闲聊")
        print(f"[会话 2] id={s2}")
        await chat("我叫什么名字？", s2, "[会话2]")

        await _send(ws, action="switch_session", session_id=s1)
        await _recv(ws)
        await chat("我叫什么名字？", s1, "[会话1]")

        await _send(ws, action="list_sessions")
        r = await _recv(ws)
        print(f"[会话列表] 共 {r['data']['total']} 个")
        for s in r["data"]["sessions"]:
            print(f"  #{s['id']} {s['title']}")


# ============================================================
# 示例 4：封装客户端类（适合集成到项目）
# ============================================================


class WsGatewayWSClient:
    """封装客户端：后台心跳 + 自动处理 tool_invoke，execute 按 call_id 返回结果

    - 心跳：默认每 30 秒发送 ping（服务端默认 120 秒空闲断开，关闭码 4408）
    - 并发：execute 可同时发起多个，事件按顶层 call_id 路由到各自的 Future
    """

    def __init__(self, url, heartbeat_interval=30):
        self.url = url
        self.heartbeat_interval = heartbeat_interval
        self.ws = None
        self.flow_type = None
        self._funcs = {}
        self._schemas = []
        self._pending_executes = []  # 等待 call_started 绑定的 Future（FIFO）
        self._active_calls = {}  # call_id -> Future
        self._heartbeat_task = None

    def tool(self, name, description, parameters, func):
        """注册本地函数为远程工具"""
        self._funcs[name] = func
        self._schemas.append(
            {"name": name, "description": description, "parameters": parameters}
        )

    async def connect(self):
        self.ws = await websockets.connect(self.url)
        conn = json.loads(await self.ws.recv())
        self.flow_type = conn["data"].get("flow_type")
        if self._schemas and self.flow_type != "agent":
            print(f"[警告] 远程工具仅 Agent 类型支持，当前为 {self.flow_type}")
        elif self._schemas:
            await self.ws.send(
                json.dumps({"action": "register_tools", "tools": self._schemas})
            )
            reg = json.loads(await self.ws.recv())
            if reg["type"] == "error":
                print(f"[错误] 工具注册失败: {reg['data']['message']}")
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        asyncio.create_task(self._loop())

    async def _heartbeat_loop(self):
        """周期 ping，防止空闲超时被服务端断开（4408）"""
        try:
            while True:
                await asyncio.sleep(self.heartbeat_interval)
                await self.ws.send("ping")
        except asyncio.CancelledError:
            pass

    async def _loop(self):
        """后台事件循环：tool_invoke 即时回调；执行事件按 call_id 路由"""
        async for raw in self.ws:
            if raw == "pong":
                continue
            e = json.loads(raw)
            t = e["type"]
            if t == "tool_invoke":
                d = e["data"]
                fn = self._funcs.get(d["name"])
                result = fn(**d.get("args", {})) if fn else "未知工具"
                await self.ws.send(
                    json.dumps(
                        {
                            "action": "tool_result",
                            "call_id": d["call_id"],
                            "result": str(result),
                        }
                    )
                )
            elif t == "call_started":
                cid = e["data"].get("call_id")
                if self._pending_executes and cid is not None:
                    self._active_calls[cid] = self._pending_executes.pop(0)
            else:
                cid = e.get("call_id")
                fut = self._active_calls.get(cid) if cid is not None else None
                if fut and not fut.done() and t in ("flow_done", "error"):
                    fut.set_result(e)
            if hasattr(self, "on_event"):
                self.on_event(e)

    async def execute(self, message=None, session_id=None, **extra):
        """触发执行并等待该次执行的 flow_done/error（按 call_id 路由，可并发调用）"""
        payload = {"action": "execute"}
        if message is not None:
            payload["message"] = message
        if session_id:
            payload["session_id"] = session_id
        payload.update(extra)
        fut = asyncio.get_event_loop().create_future()
        self._pending_executes.append(fut)
        await self.ws.send(json.dumps(payload))
        return await fut

    async def cancel(self, *, session_id=None, execution_id=None):
        """取消正在执行的会话（Agent）或执行记录（Flow）"""
        payload = {"action": "cancel"}
        if session_id:
            payload["session_id"] = session_id
        if execution_id:
            payload["execution_id"] = execution_id
        await self.ws.send(json.dumps(payload))

    async def close(self):
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self.ws:
            await self.ws.close()


async def example_client_class():
    """封装客户端类 + 自动工具回调"""
    print("\n=== 示例 4：封装客户端 ===")
    client = WsGatewayWSClient(_url())
    client.tool(
        "get_env",
        "获取客户端环境变量",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "变量名"}},
            "required": ["name"],
        },
        lambda name: os.environ.get(name, f"未设置: {name}"),
    )
    client.on_event = lambda e: (
        e["type"] == "node_content" and print(e["data"]["content"], end="", flush=True)
    )
    await client.connect()
    if client.flow_type != "agent":
        print("[错误] 示例需要「智能体」类型 Gateway")
        await client.close()
        return
    print("[已连接，工具已注册]")
    result = await client.execute("查看客户端 PATH 前100字符")
    print(f"\n[完成] {result.get('data', {}).get('status')}")
    await client.close()


# ============================================================
# 示例 5：指定 session_id 跨连接恢复
# ============================================================


async def example_resume():
    """用已知 session_id 恢复上下文

    用法：
      python ws_client_example.py 5        # 创建会话
      python ws_client_example.py 5 123    # 用 session_id=123 恢复
    """
    sid = int(sys.argv[2]) if len(sys.argv) > 2 else None
    async with websockets.connect(_url()) as ws:
        conn_data = (await _recv(ws))["data"]
        if not _check_agent(conn_data):
            return

        if sid is None:
            print("=== 示例 5：新建会话（第一次连接）===\n")
            await _send(ws, action="create_session", title="记忆测试")
            sid = (await _recv(ws))["data"]["session_id"]
            print(f"[创建会话] session_id={sid}")
            print(f"[记下此 ID，下次: python ws_client_example.py 5 {sid}]\n")
            await _send(
                ws,
                action="execute",
                message="我叫李四，Python 开发者，记住",
                session_id=sid,
            )
            await _drain(ws)
            print(f"\n\n[完成] 下次运行: python ws_client_example.py 5 {sid}")
        else:
            print(f"=== 示例 5：恢复会话 {sid}（新连接）===\n")
            await _send(
                ws, action="execute", message="我叫什么名字？做什么的？", session_id=sid
            )
            await _drain(ws)
            print("\n\n[完成] Agent 记住了上下文")


# ============================================================
# 示例 6：文件双向传输（上传 → 带 file_id 执行 → 下载产物）
# ============================================================


async def example_file_transfer():
    """上传本地文件 → 带着执行 → 下载 Agent 产生的文件

    依赖：pip install websockets httpx
    Gateway 必须关联「智能体」，且模型支持多模态（图片识别）。
    """
    import httpx

    print("\n=== 示例 6：文件传输 ===")
    http_base = f"http://{SERVER_HOST}"

    # ---- 上行：上传本地图片，拿 file_id ----
    local_img = "test.png"  # 替换为本地图片路径
    if not os.path.exists(local_img):
        print(f"[跳过] 请先准备本地文件: {local_img}")
        return

    async with httpx.AsyncClient() as client:
        with open(local_img, "rb") as f:
            resp = await client.post(
                f"{http_base}/api/ws-gateway/upload",
                params={"token": WS_TOKEN},
                files={"file": (os.path.basename(local_img), f)},
            )
        up = resp.json()["data"]
        file_id = up["file_id"]
        print(f"[上传成功] file_id={file_id}, mime={up['mime_type']}")

    # ---- 执行：把 file_id 塞进 execute 的 files 字段 ----
    async with websockets.connect(_url()) as ws:
        await _recv(ws)
        await _send(
            ws,
            action="execute",
            message="看这张图，描述一下内容",
            files=[{"id": file_id, "mime_type": up["mime_type"]}],
        )
        await _drain(ws)
        print()

    # ---- 下行：下载 Agent 产物（演示下载流程）----
    produced_id = input(
        "\n输入要下载的产物 file_id（从 flow_done.output_data 提取，没有就回车跳过）: "
    ).strip()
    if produced_id:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{http_base}/api/ws-gateway/download/{produced_id}",
                params={"token": WS_TOKEN},
            )
            out_path = f"downloaded_{produced_id}.bin"
            with open(out_path, "wb") as f:
                f.write(resp.content)
            print(f"[下载完成] {out_path}（{len(resp.content)} 字节）")


# ============================================================
# 示例 7：文件工具（远程工具调用时双向文件传输）
# ============================================================


async def example_file_tool():
    """注册文件处理工具，前端聊天调用时双向传输文件

    场景：WS 客户端只注册工具，不触发 execute。
    用户在前端 AgentChat.vue 聊天时，Agent 调用本工具，
    工具执行中通过 HTTP 端点下载服务端文件、本地处理、再上传结果。

    依赖：pip install websockets httpx
    """
    import httpx

    print("\n=== 示例 7：文件工具 ===")
    print("[提示] 注册后请到前端 AgentChat.vue 发消息触发，如「帮我处理这个文件」")
    http_base = f"http://{SERVER_HOST}"

    tool_defs = [
        {
            "name": "process_file",
            "description": "处理服务端文件（下载→转换→上传回系统）。传入 file_id",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_id": {
                        "type": "integer",
                        "description": "服务端文件ID",
                    }
                },
                "required": ["file_id"],
            },
        }
    ]

    async with websockets.connect(_url()) as ws:
        conn = await _recv(ws)
        if not _check_agent(conn["data"]):
            return

        # 从 connected 事件取文件端点模板（也可自行拼接）
        upload_url = http_base + conn["data"].get(
            "upload_url", f"/api/ws-gateway/upload?token={WS_TOKEN}"
        )
        download_tpl = http_base + conn["data"].get(
            "download_url_template",
            f"/api/ws-gateway/download/{{file_id}}?token={WS_TOKEN}",
        )

        # 注册工具
        await _send(ws, action="register_tools", tools=tool_defs)
        ack = await _recv(ws)
        print(f"[已注册] {ack['data']['names']}")
        print("[监听中] 等待前端调用...（Ctrl+C 退出）\n")

        async for raw in ws:
            e = json.loads(raw)
            t = e["type"]

            if t == "tool_invoke":
                d = e["data"]
                call_id = d["call_id"]
                file_id = d["args"]["file_id"]
                print(f"[工具调用] process_file(file_id={file_id})")

                async with httpx.AsyncClient() as client:
                    # 下行：下载服务端文件
                    url = download_tpl.replace("{file_id}", str(file_id))
                    print(f"  [下载] {url}")
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        await _send(
                            ws,
                            action="tool_result",
                            call_id=call_id,
                            result=json.dumps({"success": False, "error": "下载失败"}),
                        )
                        continue
                    original = resp.content
                    print(f"  [下载完成] {len(original)} 字节")

                    # 本地处理（示例原样返回，实际可做格式转换等）
                    processed = original

                    # 上行：上传处理结果到服务端
                    resp = await client.post(
                        upload_url,
                        files={"file": ("processed.bin", processed)},
                    )
                    new_file_id = resp.json()["data"]["file_id"]
                    print(f"  [上传完成] 新 file_id={new_file_id}")

                # 回传结果给 Agent
                await _send(
                    ws,
                    action="tool_result",
                    call_id=call_id,
                    result=json.dumps(
                        {
                            "success": True,
                            "file_id": new_file_id,
                            "message": "处理完成",
                        }
                    ),
                )
                print(f"  [已回传] file_id={new_file_id}\n")

            elif t == "node_content":
                # 前端聊天时 Agent 的文本回复也会推过来
                print(e["data"]["content"], end="", flush=True)

            elif t == "flow_done":
                print("\n[一轮对话完成]")

            elif t == "error":
                print(f"\n[错误] {e['data']['message']}")


# ============================================================
# 示例 8：人工交互（human_input_required / waiting_human → resume）
# ============================================================


async def example_human_resume():
    """人工交互闭环：执行暂停 → 征询本地输入 → resume 恢复

    前置：Agent 启用了「人工协助」能力（LLM 主动求助触发
    human_input_required），或 Flow 含 Human 节点（触发 waiting_human）。
    Agent 类型 resume 传 session_id；Flow 类型从 waiting_human 事件取
    execution_id。可能多轮交互（resume 后再次暂停），循环处理即可。
    """
    print("\n=== 示例 8：人工交互（resume）===")
    print("[提示] 需要触发人工交互的消息才会暂停，可按需修改 message")
    async with websockets.connect(_url()) as ws:
        conn_data = (await _recv(ws))["data"]
        is_agent = conn_data.get("flow_type") == "agent"

        session_id = None
        if is_agent:
            await _send(ws, action="create_session", title="人工交互测试")
            session_id = (await _recv(ws))["data"]["session_id"]
            await _send(
                ws, action="execute", session_id=session_id,
                message=(
                    "请调用 request_human_help 工具向真人求助确认："
                    "是否同意执行本次测试任务？"
                ),
            )
        else:
            await _send(ws, action="execute")

        async for raw in ws:
            e = json.loads(raw)
            t = e["type"]
            if t in ("human_input_required", "waiting_human"):
                question = e["data"].get("question", "需要您的输入")
                if t == "waiting_human":
                    execution_id = e["data"].get("execution_id")
                answer = input(f"\n[需要你的输入] {question}\n> ")
                if is_agent:
                    await _send(
                        ws, action="resume", session_id=session_id, input=answer
                    )
                else:
                    await _send(
                        ws, action="resume", execution_id=execution_id, input=answer
                    )
            elif t == "node_content":
                _on_content(e)
            elif t == "flow_done":
                print(f"\n[完成] status={e['data'].get('status')}")
                break
            elif t == "error":
                print(f"\n[错误] {e['data'].get('message')}")
                break


# ============================================================
# 示例 9：工具审批（tool_approval_required → tool_approval）
# ============================================================


async def example_tool_approval():
    """工具审批闭环：待审批事件 → 征询批准/拒绝 → tool_approval 恢复

    前置：Agent 的某个工具节点开启了「调用审批」（如 shell 节点），
    且执行消息会触发该工具。无待审批时调用 tool_approval 返回
    resolved=false（本示例开头演示该情况）。
    """
    print("\n=== 示例 9：工具审批（tool_approval）===")
    print("[提示] 需 Agent 有开启审批的工具才会触发，可按需修改 message")
    async with websockets.connect(_url()) as ws:
        conn_data = (await _recv(ws))["data"]
        if not _check_agent(conn_data):
            return

        await _send(ws, action="create_session", title="审批测试")
        session_id = (await _recv(ws))["data"]["session_id"]

        # 无待审批时调用：resolved=false，可安全忽略
        await _send(
            ws, action="tool_approval", session_id=session_id, result="approved"
        )
        r = await _recv(ws)
        if r["type"] == "tool_approval_result":
            print(f"[无待审批] resolved={r['data']['resolved']}（预期 false）")

        await _send(
            ws,
            action="execute",
            session_id=session_id,
            message="请立即调用 shell_executor 工具，command 参数填 echo hello_ws_test，然后把工具的真实输出原样告诉我",
        )
        async for raw in ws:
            e = json.loads(raw)
            t = e["type"]
            if t == "tool_approval_required":
                names = e["data"].get("approval_needed", [])
                answer = input(f"\n[待审批工具] {names}\n是否批准？(y/n) > ")
                await _send(
                    ws,
                    action="tool_approval",
                    session_id=session_id,
                    result="approved" if answer.strip().lower() == "y" else "rejected",
                )
            elif t == "node_content":
                _on_content(e)
            elif t == "flow_done":
                print(f"\n[完成] status={e['data'].get('status')}")
                break
            elif t == "error":
                print(f"\n[错误] {e['data'].get('message')}")
                break


# ============================================================
# 示例 10：取消执行（cancel）
# ============================================================


async def example_cancel():
    """执行中取消：cancel → cancel_accepted → flow_done(status=cancelled)

    Agent 传 session_id；Flow 传 execution_id（从 flow_start 事件获取）。
    收到首段输出后取消，观察 status=cancelled 的 flow_done。
    """
    print("\n=== 示例 10：取消执行（cancel）===")
    async with websockets.connect(_url()) as ws:
        conn_data = (await _recv(ws))["data"]
        is_agent = conn_data.get("flow_type") == "agent"

        session_id = execution_id = None
        if is_agent:
            await _send(ws, action="create_session", title="取消测试")
            session_id = (await _recv(ws))["data"]["session_id"]
            await _send(
                ws,
                action="execute",
                session_id=session_id,
                message="写一篇 2000 字关于人工智能发展史的长文",
            )
        else:
            await _send(ws, action="execute")

        cancelled = False
        got_content = False
        async for raw in ws:
            e = json.loads(raw)
            t = e["type"]
            if t == "flow_start":
                execution_id = e["data"].get("execution_id")
            elif t == "node_content":
                _on_content(e)
                if not got_content:
                    got_content = True
                    # 收到首段输出后取消
                    if is_agent:
                        await _send(ws, action="cancel", session_id=session_id)
                    else:
                        await _send(ws, action="cancel", execution_id=execution_id)
                    cancelled = True
            elif t == "cancel_accepted":
                print(f"\n[已受理取消] {e['data']}")
            elif t == "flow_done":
                status = e["data"].get("status")
                print(f"\n[完成] status={status}" + ("（已取消）" if cancelled else ""))
                break
            elif t == "error":
                print(f"\n[错误] {e['data'].get('message')}")
                break


# ============================================================
# 示例 11：多会话并发 + call_id 事件路由 + 心跳保活
# ============================================================


async def example_concurrent():
    """并发执行：不同会话并行 execute，事件按顶层 call_id 分流

    - 同一会话执行中再 execute 会被拒绝（「会话 X 正在执行中」），
      客户端应本地排队重试
    - 不同会话并发执行，事件流交错，靠顶层 call_id 区分归属
    - 心跳：30 秒 ping（服务端默认 120 秒空闲断开，关闭码 4408）
    """
    print("\n=== 示例 11：并发多会话 + call_id 路由 ===")
    async with websockets.connect(_url()) as ws:
        conn_data = (await _recv(ws))["data"]
        if not _check_agent(conn_data):
            return

        async def create(title):
            await _send(ws, action="create_session", title=title)
            r = await _recv(ws)
            if r["type"] == "error":
                raise RuntimeError(r["data"]["message"])
            return r["data"]["session_id"]

        s1 = await create("并发A")
        s2 = await create("并发B")
        print(f"[会话] A={s1} B={s2}")

        async def heartbeat():
            while True:
                await asyncio.sleep(30)
                await ws.send("ping")

        hb = asyncio.create_task(heartbeat())

        # 同一会话连发两条：第二条会被会话锁拒绝
        await _send(ws, action="execute", session_id=s1, message="用一句话介绍 Python")
        await _send(ws, action="execute", session_id=s1, message="再介绍一次")

        # 不同会话并发
        await _send(
            ws, action="execute", session_id=s2, message="用一句话介绍 LangGraph"
        )

        streams = {}  # call_id -> 内容片段
        done_calls = set()
        try:
            async for raw in ws:
                if raw == "pong":
                    continue
                e = json.loads(raw)
                t = e["type"]
                cid = e.get("call_id")
                if t == "call_started":
                    streams[cid] = []
                    print(f"[call {cid}] 开始 session={e['data'].get('session_id')}")
                elif t == "node_content" and cid in streams:
                    streams[cid].append(e["data"]["content"])
                elif t == "error" and "正在执行中" in e["data"].get("message", ""):
                    print(f"[会话锁拒绝] {e['data']['message']}")
                elif t == "flow_done" and cid is not None:
                    done_calls.add(cid)
                    text = "".join(streams.get(cid, []))
                    print(f"[call {cid}] 完成: {text[:50]}...")
                    if streams and len(done_calls) == len(streams):
                        break
        finally:
            hb.cancel()


# ============================================================
# 主入口
# ============================================================

EXAMPLES = {
    "1": ("最简执行", example_simple),
    "2": ("远程工具", example_remote_tools),
    "3": ("会话管理", example_sessions),
    "4": ("封装客户端", example_client_class),
    "5": ("指定 session_id 继续", example_resume),
    "6": ("文件传输", example_file_transfer),
    "7": ("文件工具", example_file_tool),
    "8": ("人工交互 resume", example_human_resume),
    "9": ("工具审批 tool_approval", example_tool_approval),
    "10": ("取消执行 cancel", example_cancel),
    "11": ("并发多会话 + call_id", example_concurrent),
}


async def main():
    if WS_TOKEN == "YOUR_WS_TOKEN_HERE":
        print("请设置环境变量 WS_TOKEN，或修改脚本中的 WS_TOKEN")
        sys.exit(1)

    choice = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in EXAMPLES else None
    if not choice:
        print("选择示例：")
        for k, (name, _) in EXAMPLES.items():
            print(f"  {k}. {name}")
        choice = input("输入编号 (1-11): ").strip()

    if choice in EXAMPLES:
        await EXAMPLES[choice][1]()
    else:
        print("无效选择")


if __name__ == "__main__":
    asyncio.run(main())

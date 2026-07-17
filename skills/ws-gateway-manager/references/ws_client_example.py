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

    handlers = {"get_local_time": lambda **kw: get_local_time(),
                "calculate": lambda **kw: calculate(kw.get("expression", ""))}
    tool_defs = [
        {"name": "get_local_time", "description": "获取客户端本地时间",
         "parameters": {"type": "object", "properties": {}}},
        {"name": "calculate", "description": "在客户端执行数学计算",
         "parameters": {"type": "object",
                        "properties": {"expression": {"type": "string", "description": "数学表达式"}},
                        "required": ["expression"]}},
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
    """封装客户端：后台自动处理 tool_invoke，execute 返回 Future"""

    def __init__(self, url):
        self.url = url
        self.ws:websockets.ClientConnection = None
        self._funcs = {}
        self._schemas = []
        self._done = None

    def tool(self, name, description, parameters, func):
        """注册本地函数为远程工具"""
        self._funcs[name] = func
        self._schemas.append({"name": name, "description": description, "parameters": parameters})

    async def connect(self):
        self.ws:websockets.ClientConnection = await websockets.connect(self.url)
        conn = json.loads(await self.ws.recv())
        self.flow_type = conn["data"].get("flow_type")
        if self._schemas and self.flow_type != "agent":
            print(f"[警告] 远程工具仅 Agent 类型支持，当前为 {self.flow_type}")
        elif self._schemas:
            await self.ws.send(json.dumps({"action": "register_tools", "tools": self._schemas}))
            reg = json.loads(await self.ws.recv())
            if reg["type"] == "error":
                print(f"[错误] 工具注册失败: {reg['data']['message']}")
        asyncio.create_task(self._loop())

    async def _loop(self):
        async for raw in self.ws:
            if raw == "pong":
                continue
            e = json.loads(raw)
            if e["type"] == "tool_invoke":
                d = e["data"]
                fn = self._funcs.get(d["name"])
                result = fn(**d.get("args", {})) if fn else "未知工具"
                await self.ws.send(json.dumps(
                    {"action": "tool_result", "call_id": d["call_id"], "result": str(result)}
                ))
            elif e["type"] in ("flow_done", "error") and self._done and not self._done.done():
                self._done.set_result(e)
            if hasattr(self, "on_event"):
                self.on_event(e)

    async def execute(self, message, session_id=None):
        payload = {"action": "execute", "message": message}
        if session_id:
            payload["session_id"] = session_id
        self._done = asyncio.get_event_loop().create_future()
        await self.ws.send(json.dumps(payload))
        return await self._done

    async def close(self):
        if self.ws:
            await self.ws.close()


async def example_client_class():
    """封装客户端类 + 自动工具回调"""
    print("\n=== 示例 4：封装客户端 ===")
    client = WsGatewayWSClient(_url())
    client.tool("get_env", "获取客户端环境变量",
                {"type": "object",
                 "properties": {"name": {"type": "string", "description": "变量名"}},
                 "required": ["name"]},
                lambda name: os.environ.get(name, f"未设置: {name}"))
    client.on_event = lambda e: e["type"] == "node_content" and print(
        e["data"]["content"], end="", flush=True
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
            await _send(ws, action="execute",
                        message="我叫李四，Python 开发者，记住", session_id=sid)
            await _drain(ws)
            print(f"\n\n[完成] 下次运行: python ws_client_example.py 5 {sid}")
        else:
            print(f"=== 示例 5：恢复会话 {sid}（新连接）===\n")
            await _send(ws, action="execute",
                        message="我叫什么名字？做什么的？", session_id=sid)
            await _drain(ws)
            print("\n\n[完成] Agent 记住了上下文")


# ============================================================
# 主入口
# ============================================================

EXAMPLES = {
    "1": ("最简执行", example_simple),
    "2": ("远程工具", example_remote_tools),
    "3": ("会话管理", example_sessions),
    "4": ("封装客户端", example_client_class),
    "5": ("指定 session_id 继续", example_resume),
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
        choice = input("输入编号 (1-5): ").strip()

    if choice in EXAMPLES:
        await EXAMPLES[choice][1]()
    else:
        print("无效选择")


if __name__ == "__main__":
    asyncio.run(main())

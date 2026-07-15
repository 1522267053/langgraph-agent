"""WebSocket 远程工具上下文

通过 contextvars 将 WS 连接对象传递到 setup_tool_handlers，
使 Agent 执行时能发现并调用客户端注册的远程工具。

asyncio 中 contextvars 自动传播到子任务（包括 asyncio.gather），
因此 LangGraph 并行节点也能正确读取。
"""

import contextvars

_current_ws_conn: contextvars.ContextVar = contextvars.ContextVar(
    "_current_ws_conn", default=None
)

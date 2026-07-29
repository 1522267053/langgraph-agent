"""WebSocket 远程工具上下文

通过 contextvars 将 WS 连接对象传递到 setup_tool_handlers，
使 Agent 执行时能发现并调用客户端注册的远程工具。

asyncio 中 contextvars 自动传播到子任务（包括 asyncio.gather），
因此 LangGraph 并行节点也能正确读取。

此外维护一个全局连接注册表（flow_id -> conn），使前端 SSE 聊天
路径（不走 WS execute）也能发现 WS 客户端注册的工具。WS 客户端
连接时注册、断开时注销。配置层保证一个智能体仅绑定一个网关，
故运行时同一智能体仅允许一个活跃 WS 连接。
"""

import contextvars
from typing import Any, Optional

_current_ws_conn: contextvars.ContextVar = contextvars.ContextVar(
    "_current_ws_conn", default=None
)

# 全局连接注册表：flow_id -> WSConnection
_active_ws_conns: dict[int, Any] = {}


def register_ws_conn(flow_id: int, conn: Any) -> bool:
    """注册 WS 连接到全局表（check-then-act，中间无 await，asyncio 原子）

    Returns:
        True 占用成功；False 该智能体已有活跃连接（调用方应拒绝新连接）。
    """
    if flow_id in _active_ws_conns:
        return False
    _active_ws_conns[flow_id] = conn
    return True


def unregister_ws_conn(flow_id: int, conn: Any) -> None:
    """注销 WS 连接（仅当当前占用者是自己时才移除，防止误删后续连接）"""
    if _active_ws_conns.get(flow_id) is conn:
        _active_ws_conns.pop(flow_id, None)


def get_active_ws_conn(flow_id: int) -> Optional[Any]:
    """获取该智能体当前活跃的 WS 连接（供前端 SSE 路径注入工具）"""
    return _active_ws_conns.get(flow_id)

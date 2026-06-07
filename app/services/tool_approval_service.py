"""
工具确认服务（仅 Agent 模式生效）

通过 asyncio.Event 实现 SSE 流内的工具确认等待/唤醒，
不使用 LangGraph interrupt 机制，避免节点重执行问题。
"""

import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ToolApprovalFuture:
    """工具确认等待句柄"""

    event: asyncio.Event = field(default_factory=asyncio.Event)
    result: str | None = None
    tool_calls: list[dict] = field(default_factory=list)
    approval_needed: list[str] = field(default_factory=list)


class ToolApprovalService:
    """管理工具确认的等待/唤醒，以 session_id 为 key"""

    def __init__(self):
        self._pending: dict[int, ToolApprovalFuture] = {}

    def register(
        self,
        session_id: int,
        tool_calls: list[dict],
        approval_needed: list[str],
    ) -> ToolApprovalFuture:
        """注册一个待确认的工具调用，返回 Future 供 await"""
        future = ToolApprovalFuture(
            tool_calls=tool_calls, approval_needed=approval_needed
        )
        self._pending[session_id] = future
        logger.info(
            f"工具确认等待注册: session_id={session_id}, "
            f"approval_needed={approval_needed}"
        )
        return future

    def resolve(self, session_id: int, result: str) -> bool:
        """前端确认/拒绝后唤醒等待"""
        future = self._pending.get(session_id)
        if not future:
            return False
        future.result = result
        future.event.set()
        logger.info(f"工具确认结果: session_id={session_id}, result={result}")
        return True

    def remove(self, session_id: int) -> None:
        """确认完成后移除等待句柄"""
        self._pending.pop(session_id, None)

    def cancel(self, session_id: int) -> None:
        """取消等待（SSE 断开 / 用户停止时调用）"""
        future = self._pending.pop(session_id, None)
        if future:
            future.result = "rejected"
            future.event.set()

    def is_pending(self, session_id: int) -> bool:
        return session_id in self._pending

    def get_pending(self, session_id: int) -> ToolApprovalFuture | None:
        return self._pending.get(session_id)


tool_approval_service = ToolApprovalService()

"""
工具确认服务（仅 Agent 模式生效）

复用 AsyncPendingService 基类的队列化生命周期管理，
支持同一 session 多个审批批次同时 pending（队列展示）。

通过 asyncio.Event 实现 SSE 流内的工具确认等待/唤醒，
不使用 LangGraph interrupt 机制，避免节点重执行问题。
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

from langchain_core.messages import ToolCall

from app.constants.timing import USER_RESPONSE_TIMEOUT_SECONDS
from app.services.async_pending_service import AsyncPendingService

logger = logging.getLogger(__name__)


@dataclass
class ToolApprovalFuture:
    """工具确认等待句柄"""

    event: asyncio.Event = field(default_factory=asyncio.Event)
    result: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    approval_needed: list[str] = field(default_factory=list)
    # 确认死线（time.time() 秒）：后端权威超时点，供断线重连回放时重算剩余
    expires_at: float = 0.0


class ToolApprovalService(AsyncPendingService[ToolApprovalFuture, str]):
    """管理工具确认的等待/唤醒，以 (session_id, approval_id) 为 key"""

    def __init__(self):
        super().__init__(timeout_seconds=USER_RESPONSE_TIMEOUT_SECONDS)

    def _create_future(self, item_id: str, **kwargs) -> ToolApprovalFuture:
        """构造 ToolApprovalFuture（接收 tool_calls + approval_needed kwargs）"""
        now = time.time()
        return ToolApprovalFuture(
            tool_calls=kwargs.get("tool_calls", []),
            approval_needed=kwargs.get("approval_needed", []),
            expires_at=now + self._timeout_seconds,
        )

    def _resolve_future(self, future: ToolApprovalFuture, result: str) -> None:
        """resolve 时把审批结果写入 future.result（approved / rejected）"""
        future.result = result

    def _cancel_result(self) -> str:
        """cancel 时填充 "rejected"（保持原有语义：取消视为拒绝）"""
        return "rejected"

    def _log_register(
        self, session_id: int, item_id: str, future: ToolApprovalFuture
    ) -> None:
        """注册日志（保持原有格式：session_id=XX, approval_needed=[...]）"""
        logger.info(
            "工具确认等待注册: session_id=%s, approval_id=%s, approval_needed=%s",
            session_id,
            item_id,
            future.approval_needed,
        )


tool_approval_service = ToolApprovalService()

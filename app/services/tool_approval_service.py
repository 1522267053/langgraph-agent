"""
工具确认服务（仅 Agent 模式生效）

复用 AsyncPendingService 基类的队列化生命周期管理，
支持同一 session 多个审批批次同时 pending（队列展示）。

通过 asyncio.Event 实现 SSE 流内的工具确认等待/唤醒，
不使用 LangGraph interrupt 机制，避免节点重执行问题。

审计落盘：register 写入 asked 快照（outcome=NULL），resolve/cancel/mark_timeout
补写终态。写入由共享 PendingAuditLogger 执行（fire-and-forget，失败仅告警），
绝不阻塞审批主流程；item_id 唯一约束保证每批次至多一行。
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

from langchain_core.messages import ToolCall

from app.constants.timing import USER_RESPONSE_TIMEOUT_SECONDS
from app.services.async_pending_service import AsyncPendingService
from app.services.pending_audit import PendingAuditLogger

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
    # 审批原因（白名单/危险正则命中说明），register 时由调用方透传，落审计表
    approval_reason: str | None = None
    # 逐工具决策（方案B：单窗口多卡片独立决策）。
    # resolve 时由 API 层填充：{tool_call_id: "approved" | "rejected"}；
    # 批内等待方（base_handler）按 tool_call_id 取各自结局。
    # 整批决策（action 粒度）时保持 None——等待方回退读 result。
    per_tool_decisions: dict[str, str] | None = None
    # asked 审计行是否已写入（重复注册复用 Future 时不重复写）
    audit_asked: bool = False


class ToolApprovalService(AsyncPendingService[ToolApprovalFuture, str]):
    """管理工具确认的等待/唤醒，以 (session_id, approval_id) 为 key"""

    def __init__(self):
        super().__init__(timeout_seconds=USER_RESPONSE_TIMEOUT_SECONDS)
        self._audit = PendingAuditLogger("approval")

    def _create_future(self, item_id: str, **kwargs) -> ToolApprovalFuture:
        """构造 ToolApprovalFuture（接收 tool_calls + approval_needed + approval_reason kwargs）"""
        now = time.time()
        return ToolApprovalFuture(
            tool_calls=kwargs.get("tool_calls", []),
            approval_needed=kwargs.get("approval_needed", []),
            expires_at=now + self._timeout_seconds,
            approval_reason=kwargs.get("approval_reason"),
        )

    def _resolve_future(self, future: ToolApprovalFuture, result: str|None) -> None:
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

    # ---- 审计 payload ----

    def _serialize_tool_calls(self, tool_calls: list[ToolCall]) -> list[dict]:
        """ToolCall 对象/字典统一序列化为可存 JSON 的列表"""
        result: list[dict] = []
        for tc in tool_calls or []:
            if isinstance(tc, dict):
                result.append(tc)
            else:
                result.append(
                    {
                        "id": getattr(tc, "id", None),
                        "name": getattr(tc, "name", None),
                        "args": getattr(tc, "args", None),
                    }
                )
        return result

    def _audit_payload(self, future: ToolApprovalFuture) -> dict:
        return {
            "tool_calls": self._serialize_tool_calls(future.tool_calls),
            "approval_needed": list(future.approval_needed or []),
            "approval_reason": future.approval_reason,
        }

    # ---- 生命周期 override：在基类行为之上追加审计 ----

    def register(self, session_id: int, item_id: str, **kwargs) -> ToolApprovalFuture:
        future = super().register(session_id, item_id, **kwargs)
        # 重复注册复用现有 Future 时不重复写 asked 审计
        if not future.audit_asked:
            future.audit_asked = True
            self._audit.audit_asked(session_id, item_id, self._audit_payload(future))
        return future

    def resolve(
        self,
        session_id: int,
        item_id: str,
        result: str,
        per_tool_decisions: dict[str, str] | None = None,
    ) -> bool:
        """按 item_id 精确路由到指定 Future 并唤醒

        Args:
            session_id: 队列 key（Agent=会话ID / Flow=执行ID）
            item_id: 审批批次 ID
            result: 整批决策（approved / rejected）
            per_tool_decisions: 逐工具决策（方案B，可选）。
                {tool_call_id: "approved" | "rejected"}；提供时 result 语义为
                「是否存在拒绝项」（仅审计用），批内等待方按 id 取各自结局。

        Returns:
            True 表示找到并唤醒；False 表示 item_id 不存在（已超时/已 cancel/从未注册）
        """
        item = self._get_item(session_id, item_id)
        if not item:
            return False
        if per_tool_decisions is not None:
            item.future.per_tool_decisions = dict(per_tool_decisions)
        resolved = super().resolve(session_id, item_id, result)
        if resolved:
            self._audit.audit_decided(session_id, item_id, result)
        return resolved

    def cancel(self, session_id: int, item_id: str | None = None) -> None:
        # 先收集本批要取消的 id，再走基类取消，最后补审计
        items = self._pending.get(session_id)
        target_ids = [item_id] if item_id else (list(items.keys()) if items else [])
        super().cancel(session_id, item_id)
        for iid in target_ids:
            self._audit.audit_decided(session_id, iid, "cancelled")

    def mark_timeout(self, session_id: int, item_id: str) -> bool:
        """超时终态补写（幂等）：调用方在等待超时后调用，与用户决策竞争时先到先得"""
        self._audit.audit_decided(session_id, item_id, "timeout")
        return True

    def get_approval_tool_names(self, session_id: int, item_id: str) -> list[str]:
        """取指定审批批次需确认的工具名（须在 resolve 前调用——resolve 后队列已弹出）"""
        item = self._get_item(session_id, item_id)
        if not item:
            return []
        return list(item.future.approval_needed or [])


tool_approval_service = ToolApprovalService()

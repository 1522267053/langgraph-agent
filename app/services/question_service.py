"""
问题反问服务（仅 Agent 模式生效）

复用 AsyncPendingService 基类的队列化生命周期管理，
支持同一 session 多个 question 同时 pending（队列展示）。

生命周期：
1. LLM 调用 ask_user_question 工具 → register(session_id, question_id) 返回 Future
2. emit_fn 通过 SSE 推送 QuestionRequestEvent 给前端
3. 前端 QuestionDialog 渲染选项，用户点击/超时/SSE 断开
4. resolve(session_id, question_id, answers) 唤醒 Future → 工具返回 answers 给 LLM

并发：每个 session_id 可同时有多个 pending question（队列），不同 question_id 独立 Future

审计落盘：register 写入 asked 快照（outcome=NULL），resolve/mark_timeout/cancel
补写终态。写入由共享 PendingAuditLogger 执行（fire-and-forget，失败仅告警），
绝不阻塞反问主流程；item_id 唯一约束保证每批次至多一行。
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from app.constants.timing import USER_RESPONSE_TIMEOUT_SECONDS
from app.services.async_pending_service import AsyncPendingService
from app.services.pending_audit import PendingAuditLogger

logger = logging.getLogger(__name__)


@dataclass
class QuestionFuture:
    """问题反问等待句柄"""

    event: asyncio.Event = field(default_factory=asyncio.Event)
    answers: list[str] | None = None  # 用户所选标签列表（multiple=True 时可多个）
    metadata: dict[str, Any] = field(default_factory=dict)
    # asked 审计行是否已写入（重复注册复用 Future 时不重复写）
    audit_asked: bool = False


class QuestionService(AsyncPendingService[QuestionFuture, list[str]]):
    """管理问题反问的等待/唤醒，以 (session_id, question_id) 为 key"""

    def __init__(self):
        super().__init__(timeout_seconds=USER_RESPONSE_TIMEOUT_SECONDS)
        self._audit = PendingAuditLogger("question")

    def _create_future(self, item_id: str, **kwargs) -> QuestionFuture:
        """构造 QuestionFuture（保留 metadata 透传字段）"""
        return QuestionFuture(metadata=kwargs.get("metadata", {}))

    def _resolve_future(self, future: QuestionFuture, result: list[str] | None) -> None:
        """resolve 时把用户答案写入 future.answers"""
        future.answers = result

    def _cancel_result(self) -> list[str] | None:
        """cancel 时填充 None（前端视为"用户取消"）"""
        return None

    def _log_register(
        self, session_id: int, item_id: str, future: QuestionFuture
    ) -> None:
        """注册日志（保持原有格式：session_id=XX，question_id 补充在尾部）"""
        logger.info(
            "问题反问等待注册: session_id=%s, question_id=%s",
            session_id,
            item_id,
        )

    # ---- 生命周期 override：在基类行为之上追加审计 ----

    def register(self, session_id: int, item_id: str, **kwargs) -> QuestionFuture:
        future = super().register(session_id, item_id, **kwargs)
        # 重复注册复用现有 Future 时不重复写 asked 审计
        if not future.audit_asked:
            future.audit_asked = True
            payload = {
                "question": kwargs.get("question"),
                "options": kwargs.get("options"),
                "multiple": bool(kwargs.get("multiple")),
            }
            self._audit.audit_asked(session_id, item_id, payload)
        return future

    def resolve(self, session_id: int, item_id: str, result: list[str]) -> bool:
        resolved = super().resolve(session_id, item_id, result)
        if resolved:
            # answers=[] 表示用户取消（前端约定：空列表 = cancel）
            outcome = "cancelled" if not result else "answered"
            self._audit.audit_decided(
                session_id, item_id, outcome, answers=result or None
            )
        return resolved

    def cancel(self, session_id: int, item_id: str | None = None) -> None:
        # 先收集本批要取消的 id，再走基类取消，最后补审计
        items = self._pending.get(session_id)
        target_ids = [item_id] if item_id else (list(items.keys()) if items else [])
        super().cancel(session_id, item_id)
        for iid in target_ids:
            self._audit.audit_decided(session_id, iid, "cancelled")

    def mark_timeout(self, session_id: int, item_id: str) -> bool:
        """超时终态补写（幂等）：调用方在等待超时后调用，与用户回答竞争时先到先得"""
        self._audit.audit_decided(session_id, item_id, "timeout")
        return True


question_service = QuestionService()

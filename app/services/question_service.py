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
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from app.constants.timing import USER_RESPONSE_TIMEOUT_SECONDS
from app.services.async_pending_service import AsyncPendingService

logger = logging.getLogger(__name__)


@dataclass
class QuestionFuture:
    """问题反问等待句柄"""

    event: asyncio.Event = field(default_factory=asyncio.Event)
    answers: list[str] | None = None  # 用户所选标签列表（multiple=True 时可多个）
    metadata: dict[str, Any] = field(default_factory=dict)


class QuestionService(AsyncPendingService[QuestionFuture, list[str]]):
    """管理问题反问的等待/唤醒，以 (session_id, question_id) 为 key"""

    def __init__(self):
        super().__init__(timeout_seconds=USER_RESPONSE_TIMEOUT_SECONDS)

    def _create_future(self, item_id: str, **kwargs) -> QuestionFuture:
        """构造 QuestionFuture（保留 metadata 透传字段）"""
        return QuestionFuture(metadata=kwargs.get("metadata", {}))

    def _resolve_future(self, future: QuestionFuture, result: list[str]) -> None:
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


question_service = QuestionService()

"""
问题反问服务（仅 Agent 模式生效）

复用 tool_approval_service 的 asyncio.Event 模式，不使用 LangGraph interrupt，
避免节点重执行问题。

生命周期：
1. LLM 调用 ask_user_question 工具 → register(session_id) 返回 Future
2. emit_fn 通过 SSE 推送 QuestionRequestEvent 给前端
3. 前端 QuestionDialog 渲染选项，用户点击/超时/SSE 断开
4. resolve(session_id, answers) 唤醒 Future → 工具返回 answers 给 LLM

并发：每个 session_id 同时只能有一个 pending question（单会话串行）
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class QuestionFuture:
    """问题反问等待句柄"""

    event: asyncio.Event = field(default_factory=asyncio.Event)
    answers: list[str] | None = None  # 用户所选标签列表（multiple=True 时可多个）
    metadata: dict[str, Any] = field(default_factory=dict)


class QuestionService:
    """管理问题反问的等待/唤醒，以 session_id 为 key"""

    def __init__(self):
        self._pending: dict[int, QuestionFuture] = {}

    def register(self, session_id: int) -> QuestionFuture:
        """注册一个待回答的问题，返回 Future 供 await"""
        # 同一会话若已有 pending 问题，先 cancel 旧的（保证单会话串行）
        existing = self._pending.pop(session_id, None)
        if existing:
            existing.answers = None
            existing.event.set()

        future = QuestionFuture()
        self._pending[session_id] = future
        logger.info("问题反问等待注册: session_id=%s", session_id)
        return future

    def resolve(self, session_id: int, answers: list[str]) -> bool:
        """前端回答后唤醒等待"""
        future = self._pending.get(session_id)
        if not future:
            return False
        future.answers = answers
        future.event.set()
        logger.info("问题反问结果: session_id=%s, answers=%s", session_id, answers)
        return True

    def remove(self, session_id: int) -> None:
        """回答完成后移除等待句柄"""
        self._pending.pop(session_id, None)

    def cancel(self, session_id: int) -> None:
        """取消等待（SSE 断开 / 用户停止 / 新问题覆盖时调用）"""
        future = self._pending.pop(session_id, None)
        if future:
            future.answers = None
            future.event.set()
            logger.info("问题反问已取消: session_id=%s", session_id)

    def is_pending(self, session_id: int) -> bool:
        return session_id in self._pending

    def get_pending(self, session_id: int) -> QuestionFuture | None:
        return self._pending.get(session_id)


question_service = QuestionService()

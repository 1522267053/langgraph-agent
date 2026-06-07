"""对话消息缓冲区，管理消息的完整生命周期：加载、追加、压缩、持久化"""

import logging
from typing import TYPE_CHECKING, Callable, Optional, Union

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
)
from sqlalchemy import func, select

from app.agent_flow.flow_event import (
    ContextCompressingEvent,
    FlowEvent,
    TokenUsageEvent,
)

if TYPE_CHECKING:
    from langgraph.types import StreamWriter
    from app.services.agent_conversation_service import AgentConversationService
    from app.services.conversation_service import ConversationService

logger = logging.getLogger(__name__)


class MessageBuffer:
    """对话消息缓冲区

    封装消息的完整生命周期，作为消息的唯一归属方。
    消费方（react loop、工具处理）通过 msg_buf.messages 读取、
    通过 msg_buf.append() 追加，压缩时 msg_buf 内部原子替换列表。

    持久化时自动感知压缩状态：
    - 未压缩：增量保存（saved_count 对比）
    - 已压缩：只保存 _post_compress_offset 之后的新增部分
    """

    def __init__(
        self,
        messages: list[BaseMessage],
        *,
        session_id: int = 0,
        execution_id: int = 0,
        db_session_factory=None,
        conversation_service: Optional[
            Union["ConversationService", "AgentConversationService"]
        ] = None,
        node_key: str = "",
        emit_fn: Optional[Callable] = None,
    ):
        self._messages = messages
        self.session_id = session_id
        self.execution_id = execution_id
        self.db_session_factory = db_session_factory
        self.conversation_service = conversation_service
        self.node_key = node_key
        self._post_compress_offset: int = 0
        self._emit_fn = emit_fn

    @property
    def messages(self) -> list[BaseMessage]:
        return self._messages

    @property
    def _id_param(self) -> int:
        return self.session_id if self.session_id else self.execution_id

    def append(self, msg: BaseMessage) -> None:
        """追加消息"""
        self._messages.append(msg)

    def _emit(self, writer: Optional["StreamWriter"], event: FlowEvent) -> None:
        """通过构造时传入的 emit 回调发送事件"""
        if self._emit_fn and writer:
            self._emit_fn(writer, event)

    async def maybe_compress(
        self,
        context_length: int,
        node_config: dict,
        writer: Optional["StreamWriter"] = None,
    ) -> bool:
        """检查并执行压缩（先保存当前消息到 DB → 再压缩 DB → 替换本地列表），返回是否成功"""
        if context_length <= 0 or not self.session_id or not self.db_session_factory:
            return False

        from app.services.agent_executor_service import agent_executor_service

        self._emit(
            writer,
            ContextCompressingEvent(status="compressing"),
        )

        # 先保存当前消息到 DB，确保 _do_compress 能看到本次执行的所有消息
        await self.save_to_db()

        try:
            async with self.db_session_factory() as db:
                result = await agent_executor_service.compress_session(
                    db, self.session_id
                )
        except Exception as e:
            logger.warning(f"自动压缩失败: {e}")
            self._emit(writer, ContextCompressingEvent(status="failed"))
            return False

        summary = result.get("summary")
        if not summary:
            logger.info(f"[上下文压缩] 压缩结果无摘要: result={list(result.keys())}")
            self._emit(writer, ContextCompressingEvent(status="failed"))
            return False

        removed = result.get("removed_count", 0)
        self._emit(
            writer,
            ContextCompressingEvent(status="done", removed_count=removed),
        )

        # 发送压缩 LLM 调用的 token 用量事件
        token_usage = result.get("token_usage") or {}
        if token_usage.get("total_tokens"):
            self._emit(
                writer,
                TokenUsageEvent(
                    node_key=self.node_key,
                    prompt_tokens=token_usage.get("prompt_tokens", 0),
                    completion_tokens=token_usage.get("completion_tokens", 0),
                    total_tokens=token_usage.get("total_tokens", 0),
                ),
            )

        user_content = f"{agent_executor_service.COMPRESS_MARKER} 共 {removed} 条历史对话已压缩为以下摘要："
        self._messages = [
            HumanMessage(content=user_content),
            AIMessage(content=summary),
        ]
        self._post_compress_offset = 2
        return True

    async def save_to_db(self) -> None:
        """持久化到 DB，压缩后只保存 _post_compress_offset 之后的新增部分"""
        if not self.conversation_service or not self.db_session_factory:
            return

        from app.models.agent_message import AgentMessage
        from app.models.conversation_message import ConversationMessage

        try:
            ModelClass = AgentMessage if self.session_id else ConversationMessage
            id_field = (
                AgentMessage.session_id
                if self.session_id
                else ConversationMessage.execution_id
            )

            async with self.db_session_factory() as db:
                if self._post_compress_offset > 0:
                    new_messages = self._messages[self._post_compress_offset :]
                    start_seq = self._post_compress_offset
                else:
                    query = select(func.count(ModelClass.id)).where(
                        id_field == self._id_param,
                        ModelClass.is_delete == 0,
                    )
                    result = await db.execute(query)
                    saved_count = result.scalar() or 0
                    new_messages = self._messages[saved_count:]
                    start_seq = saved_count

                new_messages = [
                    m
                    for m in new_messages
                    if not (
                        isinstance(m, AIMessageChunk)
                        and not m.content
                        and not m.tool_calls
                        and not m.additional_kwargs.get("reasoning_content")
                    )
                ]

                if new_messages:
                    await self.conversation_service.add_messages(
                        db,
                        self._id_param,
                        self.node_key,
                        new_messages,
                        start_sequence=start_seq,
                    )
        except Exception:
            logger.warning(f"保存对话历史到数据库失败: node_key={self.node_key}")

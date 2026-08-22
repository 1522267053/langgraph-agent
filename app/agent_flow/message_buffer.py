"""对话消息缓冲区，管理消息的完整生命周期：加载、追加、压缩、持久化"""

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Callable, Optional, Union

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_flow.flow_event import (
    ContextCompressingEvent,
    FlowEvent,
    TokenUsageEvent,
)
from app.utils.message_utils import (
    DB_PERSISTED_MESSAGE_KEY,
    extract_tool_calls,
    extract_tool_info,
    normalize_role,
    serialize_content,
)

if TYPE_CHECKING:
    from langgraph.types import StreamWriter
    from app.services.agent_conversation_service import AgentConversationService
    from app.services.conversation_service import ConversationService

logger = logging.getLogger(__name__)
_message_save_lock = asyncio.Lock()


class MessageBuffer:
    """对话消息缓冲区

    封装消息的完整生命周期，作为消息的唯一归属方。
    消费方（react loop、工具处理）通过 msg_buf.messages 读取、
    通过 msg_buf.append() 追加，压缩时 msg_buf 内部原子替换列表。

    持久化时通过消息上的数据库标记识别新增内容；压缩后只检查
    _post_compress_offset 之后的消息。
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
        history_mode: str = "node",
        emit_fn: Optional[Callable] = None,
    ):
        self._messages = messages
        self.session_id = session_id
        self.execution_id = execution_id
        self.db_session_factory = db_session_factory
        self.conversation_service = conversation_service
        self.node_key = node_key
        self.history_mode = history_mode
        self._post_compress_offset: int = 0
        self._persistence_reconciled = False
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

        # 发送压缩 LLM 调用的 token 用量事件 + 持久化
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
            if self.db_session_factory:
                try:
                    from app.services.token_usage_service import (
                        token_usage_service,
                    )

                    async with self.db_session_factory() as db:
                        await token_usage_service.record_usage(
                            db,
                            source_type="agent",
                            source_id=self.session_id,
                            node_key="_compress",
                            prompt_tokens=token_usage.get("prompt_tokens", 0),
                            completion_tokens=token_usage.get("completion_tokens", 0),
                            total_tokens=token_usage.get("total_tokens", 0),
                        )
                except Exception as e:
                    logger.warning(f"记录压缩 token_usage 失败: {e}")

        user_content = f"{agent_executor_service.COMPRESS_MARKER} 共 {removed} 条历史对话已压缩为以下摘要："
        self._messages = [
            HumanMessage(content=user_content),
            AIMessage(content=summary),
        ]
        self._post_compress_offset = 2
        return True

    @staticmethod
    def _message_fingerprint(message: BaseMessage) -> tuple[str, str, str, str]:
        """构造不含运行时 metadata 的稳定消息指纹。"""
        tool_call_id, _ = extract_tool_info(message)
        return (
            normalize_role(message),
            serialize_content(message.content),
            json.dumps(
                extract_tool_calls(message),
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
            tool_call_id or "",
        )

    async def _reconcile_persisted_messages(self, db: AsyncSession) -> None:
        """将升级前 checkpoint 中已存在于数据库的消息标记为已保存。"""
        if self._persistence_reconciled or not self._messages:
            return

        if self.session_id or self.history_mode == "flow":
            persisted = await self.conversation_service.get_full_history(
                db, self._id_param, limit=0
            )
        elif self.history_mode == "none":
            persisted = []
        else:
            persisted = await self.conversation_service.get_history(
                db, self._id_param, self.node_key, limit=0
            )
        self._persistence_reconciled = True

        if not persisted:
            return

        local_fingerprints = [
            self._message_fingerprint(message) for message in self._messages
        ]
        persisted_fingerprints = [
            self._message_fingerprint(message) for message in persisted
        ]
        best_match_length = 0
        first_local = local_fingerprints[0]
        for start, fingerprint in enumerate(persisted_fingerprints):
            if fingerprint != first_local:
                continue
            match_length = 0
            while (
                match_length < len(local_fingerprints)
                and start + match_length < len(persisted_fingerprints)
                and local_fingerprints[match_length]
                == persisted_fingerprints[start + match_length]
            ):
                match_length += 1
            best_match_length = max(best_match_length, match_length)

        for message in self._messages[:best_match_length]:
            message.response_metadata[DB_PERSISTED_MESSAGE_KEY] = True

    async def save_to_db(self) -> None:
        """持久化到 DB，压缩后只保存 _post_compress_offset 之后的新增部分"""
        if not self.conversation_service or not self.db_session_factory:
            return

        try:
            async with _message_save_lock:
                async with self.db_session_factory() as db:
                    await self._reconcile_persisted_messages(db)
                    if self._post_compress_offset > 0:
                        candidates = self._messages[self._post_compress_offset :]
                    else:
                        candidates = self._messages

                    new_messages = [
                        m
                        for m in candidates
                        if not m.response_metadata.get(DB_PERSISTED_MESSAGE_KEY)
                        and not (
                            isinstance(m, AIMessageChunk)
                            and not m.content
                            and not m.tool_calls
                            and not m.additional_kwargs.get("reasoning_content")
                        )
                    ]

                    if new_messages:
                        start_seq = (
                            await self.conversation_service.get_max_sequence(
                                db, self._id_param, self.node_key
                            )
                            + 1
                        )
                        await self.conversation_service.add_messages(
                            db,
                            self._id_param,
                            self.node_key,
                            new_messages,
                            start_sequence=start_seq,
                        )
                        for message in new_messages:
                            message.response_metadata[DB_PERSISTED_MESSAGE_KEY] = True
        except Exception as exc:
            logger.warning(
                f"保存对话历史到数据库失败: node_key={self.node_key}, error={exc}"
            )

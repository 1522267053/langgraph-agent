"""
对话历史服务
"""

import logging
from typing import Optional, cast

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_message import ConversationMessage
from app.utils.media_resolver import (
    build_content_from_db_files,
    build_media_blocks,
    extract_files_from_params,
)
from app.utils.message_utils import (
    DB_PERSISTED_MESSAGE_KEY,
    extract_thinking,
    extract_tool_calls,
    extract_tool_info,
    extract_tool_status,
    extract_token_usage,
    normalize_role,
    serialize_content,
)
from app.utils.knowledge_reference import (
    KNOWLEDGE_CITATIONS_KEY,
    KNOWLEDGE_REFERENCES_KEY,
    extract_message_knowledge_citations,
    extract_message_knowledge_references,
)

logger = logging.getLogger(__name__)


class ConversationService:
    """对话历史管理服务"""

    async def add_messages(
        self,
        db: AsyncSession,
        execution_id: int,
        node_key: str,
        messages: list[BaseMessage],
        start_sequence: int = 0,
    ) -> list[ConversationMessage]:
        """批量保存消息（一次性提交）"""
        result = []
        for i, msg in enumerate(messages):
            tool_calls = extract_tool_calls(msg)
            tool_call_id, name = extract_tool_info(msg)
            thinking = extract_thinking(msg)
            token_usage = extract_token_usage(msg)
            tool_status = extract_tool_status(msg)
            knowledge_references = extract_message_knowledge_references(msg)
            knowledge_citations = extract_message_knowledge_citations(msg)
            role = normalize_role(msg)

            kwargs: dict = {
                "execution_id": execution_id,
                "node_key": node_key,
                "role": role,
                "content": serialize_content(msg.content),
                "sequence": start_sequence + i,
            }
            # file_read 媒体注入的多模态消息标记为内部消息；
            # media_sources 存入 input_data，历史加载时按 capabilities 重建媒体块
            if msg.additional_kwargs.get("_media_injected"):
                kwargs["message_type"] = "media_injected"
                media_sources = msg.additional_kwargs.get("_media_sources")
                if isinstance(media_sources, list) and media_sources:
                    kwargs["input_data"] = {"media_sources": media_sources}
            if tool_calls is not None:
                kwargs["tool_calls"] = tool_calls
            if tool_call_id is not None:
                kwargs["tool_call_id"] = tool_call_id
            if name is not None:
                kwargs["name"] = name
            if tool_status is not None:
                kwargs["status"] = tool_status
            if knowledge_references:
                kwargs["knowledge_references"] = knowledge_references
            if knowledge_citations:
                kwargs["knowledge_citations"] = knowledge_citations
            if thinking:
                kwargs["thinking"] = thinking
            if token_usage.get("prompt_tokens") is not None:
                kwargs["prompt_tokens"] = token_usage["prompt_tokens"]
            if token_usage.get("completion_tokens") is not None:
                kwargs["completion_tokens"] = token_usage["completion_tokens"]
            if token_usage.get("total_tokens") is not None:
                kwargs["total_tokens"] = token_usage["total_tokens"]
            if role == "human":
                raw_params = msg.additional_kwargs.get("_raw_input_params")
                if raw_params:
                    files = extract_files_from_params(raw_params)
                    if files:
                        kwargs["files"] = files

            msg_record = ConversationMessage(**kwargs)
            db.add(msg_record)
            result.append(msg_record)

        await db.commit()
        return result

    async def get_history(
        self,
        db: AsyncSession,
        execution_id: int,
        node_key: Optional[str] = None,
        limit: int = 50,
        capabilities: Optional[dict] = None,
    ) -> list[BaseMessage]:
        """获取对话历史"""
        query = select(ConversationMessage).where(
            ConversationMessage.execution_id == execution_id,
            ConversationMessage.is_delete == 0,
        )

        if node_key:
            query = query.where(ConversationMessage.node_key == node_key)

        query = query.order_by(
            ConversationMessage.sequence.desc(), ConversationMessage.id.desc()
        )
        if limit > 0:
            query = query.limit(limit)

        result = await db.execute(query)
        messages = list(result.scalars().all())
        messages.reverse()

        return [await self._to_langchain_message(db, m, capabilities) for m in messages]

    async def get_full_history(
        self,
        db: AsyncSession,
        execution_id: int,
        limit: int = 0,
        capabilities: Optional[dict] = None,
    ) -> list[BaseMessage]:
        """获取全流程对话历史，limit=0 表示不限制"""
        return await self.get_history(db, execution_id, None, limit, capabilities)

    async def get_max_sequence(
        self, db: AsyncSession, execution_id: int, node_key: str
    ) -> int:
        """获取最大序号"""
        from sqlalchemy import func

        query = select(func.max(ConversationMessage.sequence)).where(
            ConversationMessage.execution_id == execution_id,
            ConversationMessage.is_delete == 0,
        )
        result = await db.execute(query)
        max_seq = result.scalar()
        return max_seq if max_seq is not None else -1

    async def _to_langchain_message(
        self,
        db: AsyncSession,
        msg: ConversationMessage,
        capabilities: Optional[dict] = None,
    ) -> BaseMessage:
        """将数据库消息转换为 LangChain 消息（含附件的多模态重建）"""
        if msg.role == "system":
            message: BaseMessage = SystemMessage(content=msg.content or "")
        elif msg.role == "human":
            content = msg.content or ""
            # file_read 媒体注入消息：按 sources 重建媒体块（capabilities 为空时
            # 保持纯文本）
            if msg.message_type == "media_injected" and capabilities:
                media_sources = (msg.input_data or {}).get("media_sources")
                if isinstance(media_sources, list) and media_sources:
                    content = await build_media_blocks(media_sources, capabilities)
            files = msg.files if isinstance(msg.files, list) else None
            if files:
                content = await build_content_from_db_files(
                    db, content, files, capabilities
                )
            message = HumanMessage(content=content)
        elif msg.role == "ai":
            ai_msg = AIMessage(content=msg.content or "")
            if msg.tool_calls:
                ai_msg.tool_calls = cast(list[ToolCall], msg.tool_calls)
            if msg.thinking:
                ai_msg.additional_kwargs["reasoning_content"] = msg.thinking
            if msg.knowledge_citations:
                ai_msg.additional_kwargs[KNOWLEDGE_CITATIONS_KEY] = (
                    msg.knowledge_citations
                )
            message = ai_msg
        elif msg.role == "tool":
            kwargs = {
                "content": msg.content or "",
                "tool_call_id": msg.tool_call_id or "",
                "name": msg.name,
            }
            if msg.status:
                kwargs["status"] = msg.status
            if msg.knowledge_references:
                kwargs["artifact"] = {
                    KNOWLEDGE_REFERENCES_KEY: msg.knowledge_references
                }
            message = ToolMessage(**kwargs)
        else:
            message = HumanMessage(content=msg.content or "")

        message.response_metadata[DB_PERSISTED_MESSAGE_KEY] = True
        return message


conversation_service = ConversationService()

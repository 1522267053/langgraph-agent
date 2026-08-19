"""
Agent 对话历史服务（适配器模式）

实现与 ConversationService 相同的接口，但操作 agent_message 表
用于 Agent 模式下保存完整对话历史（含工具调用）
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

from app.models.agent_message import AgentMessage

from app.utils.media_resolver import (
    build_content_from_db_files,
    extract_files_from_params,
)
from app.utils.message_utils import (
    extract_token_usage,
    extract_thinking,
    extract_tool_calls,
    extract_tool_info,
    extract_tool_status,
    normalize_role,
    remove_unmatched_tool_calls,
    serialize_content,
)


logger = logging.getLogger(__name__)


class AgentConversationService:
    """
    Agent 对话历史管理服务

    与 ConversationService 接口兼容，但操作 agent_message 表
    """

    async def add_messages(
        self,
        db: AsyncSession,
        session_id: int,
        node_key: str,
        messages: list[BaseMessage],
        start_sequence: int = 0,
    ) -> list[AgentMessage]:
        """批量保存消息（一次性提交）"""
        result = []
        for i, msg in enumerate(messages):
            tool_calls = extract_tool_calls(msg)
            tool_call_id = extract_tool_info(msg)[0]
            thinking = extract_thinking(msg)
            token_usage = extract_token_usage(msg)
            tool_status = extract_tool_status(msg)

            role = normalize_role(msg)

            kwargs: dict = {
                "session_id": session_id,
                "role": role,
                "content": serialize_content(msg.content),
                "sequence": start_sequence + i,
            }
            raw_user = msg.additional_kwargs.get("_raw_user_content")
            if raw_user and role == "human":
                kwargs["original_content"] = raw_user
            raw_params = msg.additional_kwargs.get("_raw_input_params")
            if raw_params and role == "human":
                kwargs["input_data"] = raw_params
                files = extract_files_from_params(raw_params)
                if files:
                    kwargs["files"] = files
            if thinking:
                kwargs["thinking"] = thinking
            if tool_calls is not None:
                kwargs["tool_calls"] = tool_calls
            if tool_call_id is not None:
                kwargs["tool_call_id"] = tool_call_id
            if tool_status is not None:
                kwargs["status"] = tool_status
            if token_usage.get("prompt_tokens") is not None:
                kwargs["prompt_tokens"] = token_usage["prompt_tokens"]
            if token_usage.get("completion_tokens") is not None:
                kwargs["completion_tokens"] = token_usage["completion_tokens"]
            if token_usage.get("total_tokens") is not None:
                kwargs["total_tokens"] = token_usage["total_tokens"]

            msg_record = AgentMessage(**kwargs)
            db.add(msg_record)
            result.append(msg_record)

        await db.commit()
        return result

    async def get_history(
        self,
        db: AsyncSession,
        session_id: int,
        node_key: Optional[str] = None,
        limit: int = 0,
        capabilities: Optional[dict] = None,
    ) -> list[BaseMessage]:
        """获取对话历史，limit=0 表示不限制"""
        query = select(AgentMessage).where(
            AgentMessage.session_id == session_id, AgentMessage.is_delete == 0
        )

        if node_key:
            pass

        query = query.order_by(AgentMessage.sequence.asc())
        if limit > 0:
            query = query.limit(limit)

        result = await db.execute(query)
        messages = result.scalars().all()

        langchain_messages = [
            await self._to_langchain_message(db, m, capabilities) for m in messages
        ]
        return self._validate_tool_pairs(langchain_messages)

    @staticmethod
    def _validate_tool_pairs(messages: list[BaseMessage]) -> list[BaseMessage]:
        """
        校验并修复消息中 tool_call 与 tool result 的配对关系。

        确保发送给 LLM 的消息列表满足：
        1. ToolMessage 必须紧跟在对应的 AIMessage（含 tool_calls）之后
        2. 孤立的 ToolMessage（无前置 tool_call）会被移除
        3. 含 tool_calls 但缺少 ToolMessage 的 AIMessage，其未匹配的 tool_calls 会被清除
        """
        result: list[BaseMessage] = []
        pending_ai_index: int = -1
        pending_ids: set[str | None] = set()

        def flush_pending():
            nonlocal pending_ai_index, pending_ids
            if pending_ids and 0 <= pending_ai_index < len(result):
                remove_unmatched_tool_calls(result[pending_ai_index], pending_ids)
            pending_ai_index = -1
            pending_ids.clear()

        for msg in messages:
            if isinstance(msg, AIMessage) and msg.tool_calls:
                flush_pending()
                pending_ids = {
                    tc.get("id", "") if isinstance(tc, dict) else tc.id
                    for tc in msg.tool_calls
                }
                result.append(msg)
                pending_ai_index = len(result) - 1
            elif isinstance(msg, AIMessage):
                flush_pending()
                result.append(msg)
            elif isinstance(msg, ToolMessage):
                if pending_ai_index >= 0 and msg.tool_call_id in pending_ids:
                    pending_ids.discard(msg.tool_call_id)
                    result.append(msg)
                    if not pending_ids:
                        pending_ai_index = -1
                else:
                    logger.debug(
                        f"移除孤立 ToolMessage: tool_call_id={msg.tool_call_id}"
                    )
            else:
                flush_pending()
                result.append(msg)

        flush_pending()
        return result

    async def get_full_history(
        self,
        db: AsyncSession,
        session_id: int,
        limit: int = 0,
        capabilities: Optional[dict] = None,
    ) -> list[BaseMessage]:
        """获取全流程对话历史"""
        return await self.get_history(
            db, session_id, limit=limit, capabilities=capabilities
        )

    async def get_max_sequence(
        self, db: AsyncSession, session_id: int, node_key: str = ""
    ) -> int:
        """获取最大序号"""
        from sqlalchemy import func

        query = select(func.max(AgentMessage.sequence)).where(
            AgentMessage.session_id == session_id, AgentMessage.is_delete == 0
        )
        result = await db.execute(query)
        max_seq = result.scalar()
        return max_seq if max_seq is not None else -1

    async def _to_langchain_message(
        self,
        db: AsyncSession,
        msg: AgentMessage,
        capabilities: Optional[dict] = None,
    ) -> BaseMessage:
        """将数据库消息转换为 LangChain 消息（含附件的多模态重建）"""
        if msg.role == "system":
            return SystemMessage(content=msg.content or "")
        elif msg.role == "human":
            content = msg.content or ""
            files = msg.files if isinstance(msg.files, list) else None
            if files:
                content = await build_content_from_db_files(
                    db, content, files, capabilities
                )
            return HumanMessage(content=content)
        elif msg.role == "ai":
            ai_msg = AIMessage(content=msg.content or "")
            if msg.tool_calls:
                ai_msg.tool_calls = cast(list[ToolCall], msg.tool_calls)
            if msg.thinking:
                ai_msg.additional_kwargs["reasoning_content"] = msg.thinking
            return ai_msg
        elif msg.role == "tool":
            kwargs = {
                "content": msg.content or "",
                "tool_call_id": msg.tool_call_id or "",
            }
            if msg.status:
                kwargs["status"] = msg.status
            return ToolMessage(**kwargs)
        else:
            return HumanMessage(content=msg.content or "")


agent_conversation_service = AgentConversationService()

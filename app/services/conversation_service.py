"""
对话历史服务
"""

from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from langchain_core.messages import (
    BaseMessage,
    SystemMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
)

from app.models.conversation_message import ConversationMessage
from app.utils.message_utils import (
    extract_thinking,
    extract_tool_calls,
    extract_tool_info,
    extract_tool_status,
    extract_token_usage,
    normalize_role,
    serialize_content,
)


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

            kwargs: dict = {
                "execution_id": execution_id,
                "node_key": node_key,
                "role": normalize_role(msg),
                "content": serialize_content(msg.content),
                "sequence": start_sequence + i,
            }
            if tool_calls is not None:
                kwargs["tool_calls"] = tool_calls
            if tool_call_id is not None:
                kwargs["tool_call_id"] = tool_call_id
            if name is not None:
                kwargs["name"] = name
            if tool_status is not None:
                kwargs["status"] = tool_status
            if thinking:
                kwargs["thinking"] = thinking
            if token_usage.get("prompt_tokens") is not None:
                kwargs["prompt_tokens"] = token_usage["prompt_tokens"]
            if token_usage.get("completion_tokens") is not None:
                kwargs["completion_tokens"] = token_usage["completion_tokens"]
            if token_usage.get("total_tokens") is not None:
                kwargs["total_tokens"] = token_usage["total_tokens"]

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
    ) -> list[BaseMessage]:
        """获取对话历史"""
        query = select(ConversationMessage).where(
            ConversationMessage.execution_id == execution_id,
            ConversationMessage.is_delete == 0,
        )

        if node_key:
            query = query.where(ConversationMessage.node_key == node_key)

        query = query.order_by(ConversationMessage.sequence.asc())
        if limit > 0:
            query = query.limit(limit)

        result = await db.execute(query)
        messages = result.scalars().all()

        return [self._to_langchain_message(m) for m in messages]

    async def get_full_history(
        self, db: AsyncSession, execution_id: int, limit: int = 0
    ) -> list[BaseMessage]:
        """获取全流程对话历史，limit=0 表示不限制"""
        return await self.get_history(db, execution_id, None, limit)

        await db.commit()

    async def get_max_sequence(
        self, db: AsyncSession, execution_id: int, node_key: str
    ) -> int:
        """获取最大序号"""
        from sqlalchemy import func

        query = select(func.max(ConversationMessage.sequence)).where(
            ConversationMessage.execution_id == execution_id,
            ConversationMessage.node_key == node_key,
            ConversationMessage.is_delete == 0,
        )
        result = await db.execute(query)
        max_seq = result.scalar()
        return max_seq if max_seq is not None else -1

    def _to_langchain_message(self, msg: ConversationMessage) -> BaseMessage:
        """将数据库消息转换为LangChain消息"""
        if msg.role == "system":
            return SystemMessage(content=msg.content or "")
        elif msg.role == "human":
            return HumanMessage(content=msg.content or "")
        elif msg.role == "ai":
            ai_msg = AIMessage(content=msg.content or "")
            if msg.tool_calls:
                ai_msg.tool_calls = msg.tool_calls
            if msg.thinking:
                ai_msg.additional_kwargs["reasoning_content"] = msg.thinking
            return ai_msg
        elif msg.role == "tool":
            kwargs = {
                "content": msg.content or "",
                "tool_call_id": msg.tool_call_id or "",
                "name": msg.name,
            }
            if msg.status:
                kwargs["status"] = msg.status
            return ToolMessage(**kwargs)
        else:
            return HumanMessage(content=msg.content or "")

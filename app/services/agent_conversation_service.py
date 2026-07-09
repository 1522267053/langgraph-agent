"""
Agent 对话历史服务（适配器模式）

实现与 ConversationService 相同的接口，但操作 agent_message 表
用于 Agent 模式下保存完整对话历史（含工具调用）
"""

import asyncio
import base64
import logging
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

from app.models.agent_message import AgentMessage

from app.services.file_service import file_service
from app.utils.message_utils import (
    extract_token_usage,
    extract_thinking,
    extract_tool_calls,
    extract_tool_info,
    extract_tool_status,
    normalize_role,
    serialize_content,
)


logger = logging.getLogger(__name__)


class AgentConversationService:
    """
    Agent 对话历史管理服务

    与 ConversationService 接口兼容，但操作 agent_message 表
    """

    def __init__(self):
        self._pending_files: list[dict] | None = None

    def set_pending_files(self, files: list[dict] | None):
        """设置待保存到下一条 human 消息的附件信息"""
        self._pending_files = files

    def _consume_pending_files(self) -> list[dict] | None:
        """消费并清除待保存的附件信息"""
        files = self._pending_files
        self._pending_files = None
        return files

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
            files = self._consume_pending_files() if role == "human" else None

            kwargs: dict = {
                "session_id": session_id,
                "role": role,
                "content": serialize_content(msg.content),
                "sequence": start_sequence + i,
            }
            raw_user = msg.additional_kwargs.get("_raw_user_content")
            if raw_user and role == "human":
                kwargs["original_content"] = raw_user
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
            if files is not None:
                kwargs["files"] = files

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
        pending_ids: set[str] = set()

        def flush_pending():
            nonlocal pending_ai_index, pending_ids
            if pending_ids and 0 <= pending_ai_index < len(result):
                ai = result[pending_ai_index]
                if ai.tool_calls:
                    ai.tool_calls = [
                        tc
                        for tc in ai.tool_calls
                        if (
                            tc.get("id", "")
                            if isinstance(tc, dict)
                            else getattr(tc, "id", "")
                        )
                        not in pending_ids
                    ]
                    if not ai.tool_calls:
                        ai.tool_calls = None
            pending_ai_index = -1
            pending_ids.clear()

        for msg in messages:
            if isinstance(msg, AIMessage) and msg.tool_calls:
                flush_pending()
                pending_ids = {
                    tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
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
        self, db: AsyncSession, session_id: int, capabilities: Optional[dict] = None
    ) -> list[BaseMessage]:
        """获取全流程对话历史"""
        return await self.get_history(db, session_id, None, capabilities=capabilities)

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
        elif msg.role in ("human", "user"):
            content = msg.content or ""
            files = msg.files if isinstance(msg.files, list) else None
            if files:
                content = await self._build_multimodal_content(
                    db, content, files, capabilities
                )
            return HumanMessage(content=content)
        elif msg.role in ("ai", "assistant"):
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
            }
            if msg.status:
                kwargs["status"] = msg.status
            return ToolMessage(**kwargs)
        else:
            return HumanMessage(content=msg.content or "")

    @staticmethod
    async def _build_multimodal_content(
        db: AsyncSession,
        text: str,
        files: list[dict],
        capabilities: Optional[dict] = None,
    ) -> str | list[dict]:
        """根据附件文件信息重建多模态 content 列表

        图片附件仅在 capabilities["image"] 开启时才注入 image_url 块，
        与 media_resolver.collect_media_blocks 行为保持一致，避免向不支持
        视觉的模型发送 image_url 内容导致 400 错误。
        """
        image_enabled = bool((capabilities or {}).get("image"))
        parts: list[dict] = [{"type": "text", "text": text}]

        for file_info in files:
            file_id = file_info.get("id")
            mime_type = file_info.get("mime_type", "")

            if not file_id or not mime_type.startswith("image/"):
                continue

            if not image_enabled:
                continue

            try:
                file_path, _, _ = await file_service.get_download_path(db, file_id)
                if not file_path.exists():
                    continue
                data = await asyncio.to_thread(file_path.read_bytes)
                b64_data = base64.b64encode(data).decode("utf-8")
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64_data}"},
                    }
                )
            except Exception:
                logger.warning(f"重建多模态消息失败: file_id={file_id}")

        if len(parts) == 1:
            return text
        return parts


agent_conversation_service = AgentConversationService()

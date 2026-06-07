"""
消息处理公共工具

提供 LangChain 消息的 token 用量提取、thinking 内容提取、
tool_calls 序列化等通用方法，避免多处重复实现。
"""

import json
from typing import Optional

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)


def extract_token_usage(message: BaseMessage) -> dict:
    """从 AI 消息中提取 token 用量，非 AI 消息返回空字典。

    兼容 usage_metadata 和 response_metadata 两种格式。
    """
    if not isinstance(message, (AIMessage, AIMessageChunk)):
        return {}
    usage = getattr(message, "usage_metadata", None)
    if usage:
        return {
            "prompt_tokens": usage.get("input_tokens"),
            "completion_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }
    resp_meta = getattr(message, "response_metadata", {})
    token_usage = resp_meta.get("token_usage") or resp_meta.get("usage") or {}
    return {
        "prompt_tokens": token_usage.get("prompt_tokens"),
        "completion_tokens": token_usage.get("completion_tokens"),
        "total_tokens": token_usage.get("total_tokens"),
    }


def extract_thinking(msg: BaseMessage) -> str:
    """从消息中提取 thinking/reasoning 内容。

    依次检查 additional_kwargs.reasoning_content 和
    content 列表中的 thinking 块。
    """
    if hasattr(msg, "additional_kwargs") and msg.additional_kwargs:
        if "reasoning_content" in msg.additional_kwargs:
            return msg.additional_kwargs["reasoning_content"]
    if isinstance(msg.content, list):
        parts = []
        for block in msg.content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                parts.append(block.get("thinking", ""))
        return "".join(parts)
    return ""


def extract_tool_calls(msg: BaseMessage) -> Optional[list[dict]]:
    """从消息中提取 tool_calls 并统一序列化为字典列表。"""
    if not (hasattr(msg, "tool_calls") and msg.tool_calls):
        return None
    return [
        {
            "id": getattr(tc, "id", None) if hasattr(tc, "id") else tc.get("id"),
            "name": getattr(tc, "name", None)
            if hasattr(tc, "name")
            else tc.get("name"),
            "args": getattr(tc, "args", None)
            if hasattr(tc, "args")
            else tc.get("args"),
        }
        for tc in msg.tool_calls
    ]


def extract_tool_status(msg: BaseMessage) -> Optional[str]:
    """从 ToolMessage 中推断工具执行状态。

    依次检查 additional_kwargs.status、content 中的 success 字段。
    返回 'success' 或 'error'，非 ToolMessage 返回 None。
    """
    if not isinstance(msg, ToolMessage):
        return None
    status = getattr(msg, "additional_kwargs", {}).get("status")
    if status in ("success", "error"):
        return status
    content = msg.content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict) and parsed.get("success") is False:
                return "error"
        except (json.JSONDecodeError, TypeError):
            pass
    return "success"


def extract_tool_info(msg: BaseMessage) -> tuple[Optional[str], Optional[str]]:
    """从 ToolMessage 中提取 tool_call_id 和 name。"""
    if isinstance(msg, ToolMessage):
        tool_call_id = getattr(msg, "tool_call_id", None)
        name = getattr(msg, "name", None)
        return tool_call_id, name
    return None, None


def normalize_role(message: BaseMessage) -> str:
    """将 LangChain 消息类型标准化为 system/human/ai/tool。"""
    msg_type = message.type
    if msg_type in ("system", "human", "ai", "tool"):
        return msg_type
    if isinstance(message, SystemMessage):
        return "system"
    if isinstance(message, HumanMessage):
        return "human"
    if isinstance(message, (AIMessage, AIMessageChunk)):
        return "ai"
    if isinstance(message, ToolMessage):
        return "tool"
    return "human"


def extract_text_content(content) -> str:
    """从消息内容中提取纯文本（兼容多模态列表格式）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(part.get("text", ""))
            elif isinstance(part, str):
                texts.append(part)
        return "\n".join(texts)
    return str(content) if content else ""


def serialize_content(content) -> str:
    """序列化消息内容为字符串存储。

    list 格式（如 Anthropic 多块 content）→ 提取纯文本；
    str 格式（如 OpenAI）→ 原样返回。
    thinking 块已通过 extract_thinking 单独存储到 thinking 列。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return extract_text_content(content)
    return str(content) if content else ""


def deserialize_content(content: str | None):
    """反序列化存储内容。

    兼容历史数据：旧版 Anthropic 消息的 content 列可能存储为 JSON 字符串
    （如 [{"type":"thinking",...},{"type":"text","..."}]），需提取纯文本。
    新版数据 content 列直接存储纯文本，原样返回。
    """
    if not content:
        return ""
    if isinstance(content, list):
        return extract_text_content(content)
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return extract_text_content(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
    return content

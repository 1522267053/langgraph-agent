"""
LLM 工具模块
支持 reasoning_content / thinking content 提取，用于 DeepSeek 等兼容 OpenAI API 的提供商

功能：
1. 从 LLM 响应中提取 reasoning_content
2. 多轮对话中保持 reasoning_content
"""

from typing import Optional, Any, Union
from langchain_core.messages import BaseMessage, AIMessage, BaseMessageChunk


def extract_reasoning_content(response: BaseMessage) -> Optional[str]:
    """
    从 LLM 响应中提取 reasoning_content / thinking content

    支持的格式（DeepSeek 等兼容 OpenAI API 的提供商）：
    1. additional_kwargs.reasoning_content（最常见）
    2. response_metadata.reasoning_content
    3. response_metadata.reasoning（OpenRouter 格式）

    Args:
        response: LLM 响应消息对象

    Returns:
        reasoning_content 字符串，不存在则返回 None
    """
    if response is None:
        return None

    # 方式1: additional_kwargs.reasoning_content（DeepSeek R1 等标准格式）
    if hasattr(response, "additional_kwargs"):
        additional_kwargs = response.additional_kwargs
        if isinstance(additional_kwargs, dict):
            if reasoning := additional_kwargs.get("reasoning_content"):
                return reasoning

    # 方式2: response_metadata.reasoning_content
    if hasattr(response, "response_metadata"):
        response_metadata = response.response_metadata
        if isinstance(response_metadata, dict):
            if reasoning := response_metadata.get("reasoning_content"):
                return reasoning

    return None


def extract_reasoning_from_chunk(chunk: Union[BaseMessageChunk, Any]) -> Optional[str]:
    """
    从流式 chunk 中提取 reasoning_content

    Args:
        chunk: 流式响应的 chunk 对象

    Returns:
        reasoning_content 字符串，不存在则返回 None
    """
    if chunk is None:
        return None

    # 获取 message 属性（ChatGenerationChunk 类型）
    message = getattr(chunk, "message", chunk)

    if hasattr(message, "additional_kwargs"):
        additional_kwargs = message.additional_kwargs
        if isinstance(additional_kwargs, dict):
            if reasoning := additional_kwargs.get("reasoning_content"):
                return reasoning

    return None


def get_chunk_content(chunk: Union[BaseMessageChunk, Any]) -> Optional[str]:
    """
    从流式 chunk 中安全提取内容

    Args:
        chunk: 流式响应的 chunk 对象

    Returns:
        内容字符串，不存在则返回 None
    """
    if chunk is None:
        return None

    content = getattr(chunk, "content", None)
    if content is None:
        return None

    # content 可能是 str 或 list[str | dict]
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        # 处理 list 类型 content
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if text := item.get("text"):
                    parts.append(text)
        return "".join(parts) if parts else None

    return None


def create_ai_message_with_reasoning(
    content: str, reasoning_content: Optional[str] = None
) -> AIMessage:
    """
    创建带有 reasoning_content 的 AIMessage

    用于多轮对话中保持 reasoning_content，确保后续请求能正确传递历史思考内容

    Args:
        content: AI 响应内容
        reasoning_content: 思考内容（可选）

    Returns:
        AIMessage 实例
    """
    additional_kwargs = {}
    if reasoning_content:
        additional_kwargs["reasoning_content"] = reasoning_content

    return AIMessage(content=content, additional_kwargs=additional_kwargs)


def get_reasoning_from_message(message: BaseMessage) -> Optional[str]:
    """
    从消息中提取 reasoning_content

    用于多轮对话中获取历史 AI 响应的思考内容

    Args:
        message: 消息对象

    Returns:
        reasoning_content 字符串，不存在则返回 None
    """
    if isinstance(message, AIMessage):
        additional_kwargs = message.additional_kwargs
        if isinstance(additional_kwargs, dict):
            return additional_kwargs.get("reasoning_content")
    return None

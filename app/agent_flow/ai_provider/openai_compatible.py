"""OpenAI 兼容提供商，使用 openai SDK 实现 LLM"""

from typing import Any, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk
from langchain_openai.chat_models.base import BaseChatOpenAI

from app.agent_flow.ai_provider.base import (
    AIProviderRegistry,
    BaseAIProvider,
)


# ---- 支持 reasoning_content 的 OpenAI 兼容聊天模型 ----


def _extract_reasoning_from_delta(delta: dict) -> Optional[str]:
    """从 streaming delta 中提取思考内容，兼容 reasoning_content 和 reasoning_details 两种字段。"""
    reasoning = delta.get("reasoning_content")
    if reasoning:
        return reasoning
    details = delta.get("reasoning_details")
    if isinstance(details, str):
        return details
    if isinstance(details, list):
        parts = [d.get("content", "") for d in details if isinstance(d, dict)]
        text = "".join(parts)
        return text if text else None
    return None


def _extract_reasoning_from_message(message: Any) -> Optional[str]:
    """从非流式响应的 message 中提取思考内容，兼容两种字段。"""
    if hasattr(message, "reasoning_content") and message.reasoning_content:
        return message.reasoning_content
    if hasattr(message, "reasoning_details"):
        details = message.reasoning_details
        if isinstance(details, str):
            return details
        if isinstance(details, list):
            parts = [d.get("content", "") for d in details if isinstance(d, dict)]
            text = "".join(parts)
            if text:
                return text
    return None


class ChatOpenAIReasoning(BaseChatOpenAI):
    """
    继承 BaseChatOpenAI，添加 reasoning_content / reasoning_details 提取逻辑。

    与 ChatDeepSeek 不同，不做任何请求格式修改（不序列化 tool message、
    不扁平化 assistant content），确保对所有 OpenAI 兼容 API 的兼容性。

    始终发送 reasoning_split=True，MiniMax 等支持此参数的 API 会将思考内容
    分离到 reasoning_details 字段返回，不支持的 API 会静默忽略该参数。
    """

    def __init__(self, **kwargs):
        extra_body = kwargs.pop("extra_body", None) or {}
        extra_body.setdefault("reasoning_split", True)
        kwargs["extra_body"] = extra_body
        super().__init__(**kwargs)

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: Optional[dict] = None,
    ):
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if (choices := chunk.get("choices")) and generation_chunk:
            top = choices[0]
            if isinstance(generation_chunk.message, AIMessageChunk):
                delta = top.get("delta", {})
                reasoning = _extract_reasoning_from_delta(delta)
                if reasoning:
                    generation_chunk.message.additional_kwargs["reasoning_content"] = (
                        reasoning
                    )
        return generation_chunk

    def _create_chat_result(
        self,
        response: Any,
        generation_info: Optional[dict] = None,
    ):
        from openai import BaseModel as OpenAIModel

        rtn = super()._create_chat_result(response, generation_info)
        if not isinstance(response, OpenAIModel):
            return rtn
        choices = getattr(response, "choices", None)
        if choices:
            reasoning = _extract_reasoning_from_message(choices[0].message)
            if reasoning:
                rtn.generations[0].message.additional_kwargs["reasoning_content"] = (
                    reasoning
                )
        return rtn


# ---- 提供商实现 ----


@AIProviderRegistry.register("openai_compatible", aliases=["custom"])
class OpenAICompatibleProvider(BaseAIProvider):
    name = "openai_compatible"
    label = "其他(OpenAI兼容)"
    default_base_url = "https://api.openai.com/v1"

    def create_chat_model(self, model: str, **kwargs) -> BaseChatModel:
        base_url = self.base_url or self.default_base_url
        return ChatOpenAIReasoning(
            model=model,
            api_key=self.api_key,
            base_url=base_url,
            stream_usage=True,
            **kwargs,
        )

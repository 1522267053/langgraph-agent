"""Anthropic (Claude) 提供商"""

import logging

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from app.agent_flow.ai_provider.base import AIProviderRegistry, BaseAIProvider

logger = logging.getLogger(__name__)


@AIProviderRegistry.register("anthropic", aliases=["claude"])
class AnthropicProvider(BaseAIProvider):
    name = "anthropic"
    label = "Anthropic (Claude)"
    default_base_url = ""

    def create_chat_model(self, model: str, **kwargs) -> BaseChatModel:
        llm_kwargs = {
            "model_provider": "anthropic",
            "model": model,
            "api_key": self.api_key,
        }
        if self.base_url:
            llm_kwargs["base_url"] = self.base_url
        # Anthropic 不支持 OpenAI 的 reasoning_effort 参数（Claude 用 thinking 机制），
        # 透传会导致 SDK 报错，这里直接丢弃以保持兼容
        if "reasoning_effort" in kwargs:
            dropped = kwargs.pop("reasoning_effort")
            logger.warning(
                "Anthropic 不支持 reasoning_effort 参数（当前值=%s），已忽略。"
                "Claude 如需深度推理请使用 thinking 机制。",
                dropped,
            )
        llm_kwargs.update(kwargs)
        return init_chat_model(**llm_kwargs)

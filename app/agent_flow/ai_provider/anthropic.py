"""Anthropic (Claude) 提供商"""

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from app.agent_flow.ai_provider.base import AIProviderRegistry, BaseAIProvider


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
        llm_kwargs.update(kwargs)
        return init_chat_model(**llm_kwargs)

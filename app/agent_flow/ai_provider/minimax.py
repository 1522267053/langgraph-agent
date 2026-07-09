"""MiniMax 提供商（Anthropic 兼容对话模式）"""

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from app.agent_flow.ai_provider.base import (
    AIProviderRegistry,
    BaseAIProvider,
)


@AIProviderRegistry.register("minimax")
class MiniMaxProvider(BaseAIProvider):
    name = "minimax"
    label = "MiniMax"
    default_base_url = "https://api.minimaxi.com/anthropic"

    def create_chat_model(self, model: str, **kwargs) -> BaseChatModel:
        base_url = self.base_url or self.default_base_url
        return init_chat_model(
            model_provider="anthropic",
            model=model,
            api_key=self.api_key,
            base_url=base_url,
            **kwargs,
        )

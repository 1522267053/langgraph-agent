"""DeepSeek 提供商（保留 reasoning_content 支持）"""

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from app.agent_flow.ai_provider.base import AIProviderRegistry, BaseAIProvider


@AIProviderRegistry.register("deepseek")
class DeepSeekProvider(BaseAIProvider):
    name = "deepseek"
    label = "DeepSeek"
    default_base_url = "https://api.deepseek.com/v1"

    def create_chat_model(self, model: str, **kwargs) -> BaseChatModel:
        base_url = self.base_url or self.default_base_url
        return init_chat_model(
            model_provider="deepseek",
            model=model,
            api_key=self.api_key,
            api_base=base_url,
            **kwargs,
        )

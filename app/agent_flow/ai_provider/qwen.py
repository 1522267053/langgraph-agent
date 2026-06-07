"""通义千问提供商（OpenAI 兼容模式）"""

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from app.agent_flow.ai_provider.base import AIProviderRegistry, BaseAIProvider


@AIProviderRegistry.register("qwen")
class QwenProvider(BaseAIProvider):
    name = "qwen"
    label = "通义千问"
    default_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def create_chat_model(self, model: str, **kwargs) -> BaseChatModel:
        base_url = self.base_url or self.default_base_url
        return init_chat_model(
            model_provider="deepseek",
            model=model,
            api_key=self.api_key,
            api_base=base_url,
            **kwargs,
        )

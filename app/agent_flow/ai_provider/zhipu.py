"""智谱 AI 提供商（OpenAI 兼容模式）"""

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from app.agent_flow.ai_provider.base import AIProviderRegistry, BaseAIProvider


@AIProviderRegistry.register("zhipu")
class ZhipuProvider(BaseAIProvider):
    name = "zhipu"
    label = "智谱AI"
    default_base_url = "https://open.bigmodel.cn/api/paas/v4"

    def create_chat_model(self, model: str, **kwargs) -> BaseChatModel:
        base_url = self.base_url or self.default_base_url
        return init_chat_model(
            model_provider="deepseek",
            model=model,
            api_key=self.api_key,
            api_base=base_url,
            **kwargs,
        )

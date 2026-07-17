"""
统一 AI 提供商抽象层

通过适配器缓存（adapter_type）分发，替代原有的 AIProviderRegistry 查找。
适配器缓存从 ai_provider 表加载，同步后自动刷新。
"""

from app.agent_flow.ai_provider.base import (
    AIProviderRegistry,
    BaseAIProvider,
)

from app.agent_flow.ai_provider.openai_compatible import OpenAICompatibleProvider
from app.agent_flow.ai_provider.anthropic import AnthropicProvider

__all__ = [
    "AIProviderRegistry",
    "BaseAIProvider",
    "create_provider",
]


def create_provider(
    provider_name: str, api_key: str, base_url: str = ""
) -> BaseAIProvider:
    """
    根据名称创建 AI 提供商实例

    优先从适配器缓存（ai_provider 表）获取 adapter_type 分发，
    缓存未命中时回退到 AIProviderRegistry 查找。

    Args:
        provider_name: 供应商标识
        api_key: API Key
        base_url: 自定义 API 地址（为空时使用提供商默认值）

    Returns:
        BaseAIProvider 实例
    """
    from app.services.ai_provider_service import get_adapter_type

    adapter = get_adapter_type(provider_name)
    if adapter == "anthropic":
        return AnthropicProvider(api_key=api_key, base_url=base_url)

    if adapter == "openai_compatible":
        return OpenAICompatibleProvider(api_key=api_key, base_url=base_url)

    cls = AIProviderRegistry.get(provider_name)
    if not cls:
        available = ", ".join(sorted(AIProviderRegistry.list_providers().keys()))
        raise ValueError(
            f"不支持的 AI 提供商: {provider_name}，可用提供商: {available}"
        )
    return cls(api_key=api_key, base_url=base_url)

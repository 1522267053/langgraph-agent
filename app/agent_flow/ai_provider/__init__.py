"""
统一 AI 提供商抽象层

覆盖 LLM 聊天模型和媒体生成能力（图片/音频/视频）。
通过装饰器自动注册，新增提供商只需在 ai_provider/ 下新建文件并用 @register 装饰。
"""

from app.agent_flow.ai_provider.base import (
    BaseAIProvider,
    MediaGenFieldDef,
    MediaResult,
    AIProviderRegistry,
)

__all__ = [
    "BaseAIProvider",
    "MediaGenFieldDef",
    "MediaResult",
    "AIProviderRegistry",
    "create_provider",
]


def create_provider(
    provider_name: str, api_key: str, base_url: str = ""
) -> BaseAIProvider:
    """
    根据名称创建 AI 提供商实例

    Args:
        provider_name: 提供商名称（deepseek/zhipu/qwen/openai_compatible/custom/minimax）
        api_key: API Key
        base_url: 自定义 API 地址（为空时使用提供商默认值）

    Returns:
        BaseAIProvider 实例
    """
    cls = AIProviderRegistry.get(provider_name)
    if not cls:
        available = ", ".join(sorted(AIProviderRegistry.list_providers().keys()))
        raise ValueError(
            f"不支持的 AI 提供商: {provider_name}，可用提供商: {available}"
        )
    return cls(api_key=api_key, base_url=base_url)

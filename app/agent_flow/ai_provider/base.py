"""
AI 提供商抽象基类和数据结构
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Type

from langchain_core.language_models.chat_models import BaseChatModel


class BaseAIProvider(ABC):
    """
    AI 提供商抽象基类

    所有提供商必须实现 create_chat_model()。

    子类应定义以下类属性：
    - name: str — 供应商标识（用于注册）
    - label: str — 前端显示名称
    - default_base_url: str — 默认 API 地址
    """

    name: str = ""
    label: str = ""
    default_base_url: str = ""

    def __init__(self, api_key: str, base_url: str = ""):
        self.api_key = api_key
        self.base_url = base_url

    # ---- LLM（必须实现）----

    @abstractmethod
    def create_chat_model(self, model: str, **kwargs) -> BaseChatModel:
        """创建 LangChain 聊天模型实例"""


# ---- 提供商注册表 ----


class AIProviderRegistry:
    """AI 提供商注册表，通过装饰器自动注册"""

    _providers: Dict[str, Type[BaseAIProvider]] = {}
    _info_cache: Optional[List[dict]] = None

    @classmethod
    def register(cls, name: str, *, aliases: Optional[List[str]] = None):
        """
        注册提供商（装饰器用法）

        Args:
            name: 提供商主名称
            aliases: 可选的别名列表（如 ["custom"]）
        """
        all_names = [name] + (aliases or [])

        def decorator(provider_cls: Type[BaseAIProvider]) -> Type[BaseAIProvider]:
            for n in all_names:
                cls._providers[n] = provider_cls
            return provider_cls

        return decorator

    @classmethod
    def get(cls, name: str) -> Optional[Type[BaseAIProvider]]:
        """按名称查找提供商类"""
        return cls._providers.get(name)

    @classmethod
    def list_providers(cls) -> Dict[str, Type[BaseAIProvider]]:
        """返回所有已注册的提供商"""
        return dict(cls._providers)

    @classmethod
    def list_provider_info(cls) -> List[dict]:
        """返回所有已注册提供商的元数据（去重别名，结果缓存）"""
        if cls._info_cache is not None:
            return cls._info_cache

        seen: set[str] = set()
        result: List[dict] = []
        for provider_cls in cls._providers.values():
            if provider_cls.__name__ in seen:
                continue
            seen.add(provider_cls.__name__)
            info = {
                "name": getattr(provider_cls, "name", ""),
                "label": getattr(provider_cls, "label", ""),
                "default_base_url": getattr(provider_cls, "default_base_url", ""),
            }
            result.append(info)
        cls._info_cache = result
        return result

    @classmethod
    def invalidate_info_cache(cls):
        """清除 provider info 缓存，通常在注册新 provider 后调用"""
        cls._info_cache = None

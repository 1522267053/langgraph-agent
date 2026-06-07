"""
AI 提供商抽象基类和数据结构
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Type

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.agent_flow.ai_provider.base import BaseAIProvider


@dataclass
class MediaResult:
    """媒体生成结果"""

    url: Optional[str] = None
    content: Optional[bytes] = None
    mime_type: str = ""
    file_ext: str = ""
    file_name: str = ""


@dataclass
class MediaGenFieldDef:
    """媒体生成参数字段定义，Provider 通过重写 get_media_gen_fields 返回"""

    name: str
    label: str
    field_type: str  # text | number | select | switch | textarea
    default: object = None
    required: bool = False
    options: List[str] = field(default_factory=list)
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    step: Optional[float] = None
    placeholder: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        """转换为可序列化的字典（供 API 返回）"""
        return {
            "name": self.name,
            "label": self.label,
            "field_type": self.field_type,
            "default": self.default,
            "required": self.required,
            "options": self.options,
            "min_val": self.min_val,
            "max_val": self.max_val,
            "step": self.step,
            "placeholder": self.placeholder,
            "description": self.description,
        }


# ---- 默认工具参数 Schema ----


class DefaultImageToolInput(BaseModel):
    """默认图片生成工具参数"""

    prompt: str = Field(..., description="图片描述，越详细越好")
    size: str = Field(
        default="1024x1024",
        description="图片尺寸: 1024x1024, 1792x1024, 1024x1792",
    )


class DefaultAudioToolInput(BaseModel):
    """默认音频生成工具参数"""

    text: str = Field(..., description="要转换为语音的文本内容")
    voice: str = Field(default="alloy", description="语音音色")
    speed: float = Field(default=1.0, description="语速，0.25-4.0")


class DefaultVideoToolInput(BaseModel):
    """默认视频生成工具参数"""

    prompt: str = Field(..., description="视频描述，越详细越好")


# ---- 抽象基类 ----


class BaseAIProvider(ABC):
    """
    AI 提供商抽象基类

    所有提供商必须实现 create_chat_model()，
    媒体生成方法按需重写（默认抛出 NotImplementedError）。

    子类应定义以下类属性：
    - name: str — 供应商标识（用于注册）
    - label: str — 前端显示名称
    - default_base_url: str — 默认 API 地址
    """

    name: str = ""
    label: str = ""
    default_base_url: str = ""
    supports_image: bool = False
    supports_audio: bool = False
    supports_video: bool = False

    def __init__(self, api_key: str, base_url: str = ""):
        self.api_key = api_key
        self.base_url = base_url

    # ---- LLM（必须实现）----

    @abstractmethod
    def create_chat_model(self, model: str, **kwargs) -> BaseChatModel:
        """创建 LangChain 聊天模型实例"""

    # ---- 媒体生成（子类按需重写）----

    async def generate_image(
        self, prompt: str, model: str = "", **kwargs
    ) -> MediaResult:
        """生成图片"""
        raise NotImplementedError(f"[{self.name}] 不支持图片生成")

    async def generate_audio(self, text: str, model: str = "", **kwargs) -> MediaResult:
        """生成语音/音频"""
        raise NotImplementedError(f"[{self.name}] 不支持音频生成")

    async def generate_video(
        self, prompt: str, model: str = "", **kwargs
    ) -> MediaResult:
        """生成视频"""
        raise NotImplementedError(f"[{self.name}] 不支持视频生成")

    # ---- 工具参数 Schema（子类按需重写以匹配实际 API 参数）----

    def get_image_tool_schema(self) -> Type[BaseModel]:
        """返回图片生成工具的参数 Schema"""
        return DefaultImageToolInput

    def get_audio_tool_schema(self) -> Type[BaseModel]:
        """返回音频生成工具的参数 Schema"""
        return DefaultAudioToolInput

    def get_video_tool_schema(self) -> Type[BaseModel]:
        """返回视频生成工具的参数 Schema"""
        return DefaultVideoToolInput

    # ---- 前端配置驱动（子类按需重写）----

    @classmethod
    def get_media_gen_fields(cls, media_type: str) -> List[MediaGenFieldDef]:
        """
        返回该 Provider 对指定媒体类型的参数字段定义

        前端根据此定义动态渲染配置表单。
        子类应重写此方法以提供 Provider 特有的参数。

        Args:
            media_type: 媒体类型（image/audio/video）
        """
        return []

    @classmethod
    def get_media_gen_defaults(cls, media_type: str) -> dict:
        """返回该 Provider 对指定媒体类型的默认参数值"""
        return {f.name: f.default for f in cls.get_media_gen_fields(media_type)}


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
        """
        返回所有已注册提供商的元数据（去重别名，结果缓存）

        每个提供商返回一条记录，包含 name、label、default_base_url 和媒体生成能力。
        media_fields 在首次调用时一次性计算并缓存，后续直接返回。
        """
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
                "supports_image": getattr(provider_cls, "supports_image", False),
                "supports_audio": getattr(provider_cls, "supports_audio", False),
                "supports_video": getattr(provider_cls, "supports_video", False),
                "media_fields": {},
            }
            for mt in ("image", "audio", "video"):
                if getattr(provider_cls, f"supports_{mt}", False):
                    try:
                        provider_cls_fields = provider_cls.get_media_gen_fields(mt)
                        info["media_fields"][mt] = [
                            f.to_dict() for f in provider_cls_fields
                        ]
                    except Exception:
                        info["media_fields"][mt] = []
            result.append(info)
        cls._info_cache = result
        return result

    @classmethod
    def invalidate_info_cache(cls):
        """清除 provider info 缓存，通常在注册新 provider 后调用"""
        cls._info_cache = None

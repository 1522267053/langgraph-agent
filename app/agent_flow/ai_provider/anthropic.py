"""Anthropic (Claude) 提供商"""

import logging
from typing import Any

import httpx
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from app.agent_flow.ai_provider.base import AIProviderRegistry, BaseAIProvider

logger = logging.getLogger(__name__)


# ---- 临时调试：打印真实请求（URL / headers / body） ----


def _mask(value: str) -> str:
    return value if len(value) <= 12 else f"{value[:8]}...{value[-4:]}"


def _log_anthropic_request(request: httpx.Request) -> None:
    """httpx request hook：打印真实 URL / headers / body（x-api-key / Authorization 脱敏）"""
    headers = {
        k: _mask(v) if k.lower() in ("authorization", "x-api-key", "api-key") else v
        for k, v in request.headers.items()
    }
    body = request.read().decode("utf-8", errors="replace")
    logger.info(
        "[Anthropic] %s %s\nheaders: %s\nbody: %s",
        request.method,
        str(request.url),
        headers,
        body,
    )


def _install_request_logger(llm) -> None:
    for client in (llm._client._client, llm._async_client._client):
        client.event_hooks["request"].append(_log_anthropic_request)


@AIProviderRegistry.register("anthropic", aliases=["claude"])
class AnthropicProvider(BaseAIProvider):
    name = "anthropic"
    label = "Anthropic (Claude)"
    default_base_url = ""

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        """剥离 base_url 尾部 /v1。

        Anthropic SDK 内部硬编码追加 /v1/messages（见 anthropic/resources/messages/messages.py），
        若用户 base_url 已含尾部 /v1，会得到 .../v1/v1/messages 而 404。
        与 OpenAI 兼容协议相反（OpenAI SDK 仅追加 /chat/completions，base_url 需含 /v1）。
        """
        if not base_url:
            return ""
        return base_url.rstrip("/").removesuffix("/v1").rstrip("/")

    def create_chat_model(self, model: str, **kwargs) -> BaseChatModel:
        llm_kwargs: dict[str, Any] = {
            "api_key": self.api_key,
        }
        if self.base_url:
            llm_kwargs["base_url"] = self._normalize_base_url(self.base_url)
        # Anthropic 不支持 OpenAI 的 reasoning_effort 参数（Claude 用 thinking 机制），
        # 透传会导致 SDK 报错，这里直接丢弃以保持兼容
        if "reasoning_effort" in kwargs:
            dropped = kwargs.pop("reasoning_effort")
            logger.warning(
                "Anthropic 不支持 reasoning_effort 参数（当前值=%s），已忽略。"
                "Claude 如需深度推理请使用 thinking 机制。",
                dropped,
            )
        # stream_usage 是 OpenAI 的 stream_options.include_usage 参数，
        # Anthropic 流式响应始终携带 usage，直接丢弃避免 SDK 校验报错
        kwargs.pop("stream_usage", None)
        llm_kwargs.update(kwargs)
        # 覆盖 SDK 默认的 Accept: application/json 为 */*（兼容 SSE 流式网关）；
        # ChatAnthropic 的 default_headers 会透传给 Anthropic SDK 客户端
        default_headers = llm_kwargs.pop("default_headers", None) or {}
        default_headers.setdefault("Accept", "*/*")
        llm_kwargs["default_headers"] = default_headers
        # model/model_provider 必须作为显式 kwargs 传入：
        # init_chat_model 是 @overload 函数，靠 model_provider 字面量分发重载，
        # 塞进 **dict 会导致重载解析失败
        llm = init_chat_model(
            model=model,
            model_provider="anthropic",
            **llm_kwargs,
        )
        # 临时调试：挂载 httpx request hook 打印真实请求
        # _install_request_logger(llm)
        return llm

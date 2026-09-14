"""Anthropic (Claude) 提供商"""

import logging
from functools import cached_property
from typing import Any

import anthropic
import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_anthropic.chat_models import ChatAnthropic

from app.agent_flow.ai_provider.base import AIProviderRegistry, BaseAIProvider
from app.utils.http_client import create_llm_async_client, create_llm_sync_client

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


# ---- reasoning_effort → Anthropic thinking 自动映射 ----

# 推理深度 → thinking.budget_tokens 阶梯（low 取 API 下限浅思考；深度需求
# 可经前端「附加参数」填 {"thinking": {"type": "enabled", "budget_tokens": N}} 覆盖）
_EFFORT_BUDGET_MAP = {"low": 2048, "medium": 4096, "high": 8192}
# 新代模型原生 reasoning_effort 合法档位（库字段为 Literal 校验，
# 超出即 ValidationError 使建模失败，须先过滤降级为告警丢弃）
_NATIVE_EFFORT_LEVELS = {"max", "xhigh", "high", "medium", "low"}


def _model_supports_adaptive_effort(model: str) -> bool:
    """代次探测：模型是否支持 effort/adaptive thinking（Opus 4.7+/Sonnet 5）。

    复用 langchain_anthropic 内部 profile 判定（与库 payload 组装逻辑一致）：
    新代模型已在 API 层移除 budget_tokens，传 {"type": "enabled", ...} 会被
    拒绝；profile 未知（第三方网关自定义模型名等）按不支持处理，回落到
    budget 形态兜底。
    """
    try:
        from langchain_anthropic.chat_models import (
            _get_default_model_profile,
            _reasoning_effort_levels,
        )

        levels = _reasoning_effort_levels(_get_default_model_profile(model))
    except Exception:  # noqa: BLE001 — 库版本变动时降级为 budget 形态，不阻断建模型
        return False
    return "xhigh" in levels


def _resolve_thinking(
    model: str,
    effort: Any,
    user_thinking: Any,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """决策 thinking 配置（纯函数，便于测试）。

    优先级：用户显式配置（extra_body.thinking）> effort 自动映射 > 不设置。

    Returns:
        (constructor_thinking, native_effort, dropped_effort)
        - constructor_thinking: 传给 ChatAnthropic 构造参数 thinking 的 dict
        - native_effort: 新代模型走库原生 reasoning_effort（库内部转
          output_config.effort + adaptive thinking）
        - dropped_effort: 无法映射需告警丢弃的自定义档位
    """
    # 用户显式配置完全接管：构造参数不设 thinking，值留在 model_kwargs
    # 透传（非法形态由 Pydantic 校验报可见错误，不静默吞掉）
    if user_thinking is not None:
        return None, None, None
    if not effort:
        return None, None, None

    effort_str = str(effort).lower()
    budget = _EFFORT_BUDGET_MAP.get(effort_str)
    adaptive_capable = _model_supports_adaptive_effort(model)
    if budget is not None and not adaptive_capable:
        # 老代/未知模型（3.7/4 系及兼容网关）：enabled + budget_tokens 形态
        return {"type": "enabled", "budget_tokens": budget}, None, None
    if adaptive_capable:
        if effort_str not in _NATIVE_EFFORT_LEVELS:
            # 自定义档位超出库 Literal 白名单会触发 ValidationError 使建模失败，
            # 降级为告警丢弃（与老代自定义档位行为一致）
            return None, None, effort_str
        # 新代模型：API 已移除 budget_tokens，effort（含 xhigh 等）交由库
        # 原生字段转 adaptive thinking
        return None, effort_str, None
    # OpenAI 专属档位（allow-create 自定义值）在老代模型上无对应概念，丢弃告警
    return None, None, effort_str


class ProxiedChatAnthropic(ChatAnthropic):
    """覆盖默认 httpx 客户端构建，统一走 app 代理策略

    langchain_anthropic 默认经 anthropic SDK 的 get_environment_proxies()
    拾取环境变量与 Windows 系统代理；这里改为 trust_env=False +
    全局配置的 proxy_url（见 app/utils/http_client.py）。
    anthropic_proxy 字段（若有）仍优先。
    """

    def _proxy_aware_http_kwargs(self) -> dict[str, Any]:
        """构建 httpx 层参数，语义与父类默认构建保持一致"""
        kwargs: dict[str, Any] = {"base_url": self.anthropic_api_url or None}
        if self.default_request_timeout is None or self.default_request_timeout > 0:
            kwargs["timeout"] = self.default_request_timeout
        if self.anthropic_proxy:
            # 显式配置的 anthropic_proxy 优先于全局 proxy_url（工厂内 setdefault）
            kwargs["proxy"] = self.anthropic_proxy
        return kwargs

    @cached_property
    def _client(self) -> anthropic.Client:
        params = {
            **self._client_params,
            "http_client": create_llm_sync_client(**self._proxy_aware_http_kwargs()),
        }
        return anthropic.Client(**params)

    @cached_property
    def _async_client(self) -> anthropic.AsyncClient:
        params = {
            **self._client_params,
            "http_client": create_llm_async_client(**self._proxy_aware_http_kwargs()),
        }
        return anthropic.AsyncClient(**params)


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
        # ---- extra_body：用户显式附加参数，直接进入 Anthropic API payload ----
        # ChatAnthropic 无 extra="allow"，未知字段会被 Pydantic 静默忽略，
        # 必须经 model_kwargs 透传（payload 组装时 **self.model_kwargs 展开）
        extra_body = kwargs.pop("extra_body", None) or {}
        model_kwargs: dict[str, Any] = dict(extra_body)

        # ---- reasoning_effort → thinking 自动映射 ----
        # Anthropic 不支持 OpenAI 的 reasoning_effort 参数语义；Claude 用
        # thinking 机制（pre-4.7 模型为 enabled+budget_tokens，Opus 4.7+/Sonnet 5
        # 走 adaptive thinking）。不映射直接透传会被 SDK 拒绝
        effort = kwargs.pop("reasoning_effort", None)
        thinking, native_effort, dropped_effort = _resolve_thinking(
            model, effort, model_kwargs.get("thinking")
        )
        if dropped_effort:
            logger.warning(
                "Anthropic 模型 %s 不支持推理深度档位 %r（OpenAI 专属概念），已忽略。"
                "可用档位：%s，或在附加参数中自定义 thinking。",
                model,
                dropped_effort,
                sorted(_EFFORT_BUDGET_MAP),
            )
        elif native_effort:
            # 新代模型：走库原生字段（库内部转 output_config.effort +
            # adaptive thinking；thinking 构造参数不设，交给库判定）
            kwargs["reasoning_effort"] = native_effort
            logger.info(
                "Anthropic 新代模型 %s：推理深度 %r 走原生 effort（adaptive thinking）",
                model,
                native_effort,
            )
        elif thinking:
            # 老代/未知模型：enabled + budget_tokens 形态。
            # temperature/max_tokens 不做任何改写：Anthropic API 自带权威校验
            # （thinking 启用时 temperature 必须=1、budget_tokens 必须<max_tokens），
            # 配置冲突时由 API 返回 400 错误消息，用户据此到 LLM 配置自行修正——
            # 静默改写会掩盖用户显式配置，报错才是可预期的行为
            llm_kwargs["thinking"] = thinking

        # stream_usage 是 OpenAI 的 stream_options.include_usage 参数，
        # Anthropic 流式响应始终携带 usage，直接丢弃避免 SDK 校验报错
        kwargs.pop("stream_usage", None)
        if model_kwargs:
            llm_kwargs["model_kwargs"] = model_kwargs
        llm_kwargs.update(kwargs)
        # 覆盖 SDK 默认的 Accept: application/json 为 */*（兼容 SSE 流式网关）；
        # ChatAnthropic 的 default_headers 会透传给 Anthropic SDK 客户端
        default_headers = llm_kwargs.pop("default_headers", None) or {}
        default_headers.setdefault("Accept", "*/*")
        llm_kwargs["default_headers"] = default_headers
        # 直接构造子类（原 init_chat_model 仅做字面量分发），以注入代理策略客户端
        llm = ProxiedChatAnthropic(
            model=model,
            **llm_kwargs,
        )
        # 临时调试：挂载 httpx request hook 打印真实请求
        # _install_request_logger(llm)
        return llm

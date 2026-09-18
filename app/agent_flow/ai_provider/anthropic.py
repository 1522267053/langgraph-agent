"""Anthropic (Claude) 提供商"""

import logging
from functools import cached_property
from typing import Any, Optional

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

# 推理深度 → thinking.budget_tokens 阶梯（深度需求可经前端「附加参数」
# 填 {"thinking": {"type": "enabled", "budget_tokens": N}} 覆盖）
_EFFORT_BUDGET_MAP = {"low": 2048, "medium": 4096, "high": 8192}

# budget_tokens 型档位的预算上限（对齐 opencode budgetVariants 钳制策略）
_THINKING_BUDGET_CEILING = 31_999


def _effort_budget(effort: str, max_tokens: Any) -> Optional[int]:
    """推理深度档位 → thinking.budget_tokens 预算（不支持原生 effort 的模型用）

    静态阶梯（low/medium/high）+ 动态 max 档：max → min(31999, max_tokens-1)，
    满足 Anthropic budget_tokens < max_tokens 的 API 校验。
    """
    if effort != "max":
        return _EFFORT_BUDGET_MAP.get(effort)
    try:
        mt = int(max_tokens) if max_tokens is not None else 0
    except (TypeError, ValueError):
        mt = 0
    if mt > 1:
        return min(_THINKING_BUDGET_CEILING, mt - 1)
    return _THINKING_BUDGET_CEILING


def _model_effort_levels(model: str) -> tuple[str, ...]:
    """查询模型在库 profile 中声明的 effort 档位（langchain-anthropic data/_profiles）。

    返回空 tuple = 该模型不支持 effort 机制：包括 sonnet-4-5 及更早的 Claude、
    以及所有非 Claude 模型（GLM/MiniMax/DeepSeek 等第三方 Anthropic 兼容端点、
    自定义网关模型名——_PROFILES 注册表仅收录 claude-* 模型）。
    """
    try:
        from langchain_anthropic.chat_models import (
            _get_default_model_profile,
            _reasoning_effort_levels,
        )

        return _reasoning_effort_levels(_get_default_model_profile(model))
    except Exception:  # noqa: BLE001 — 库版本变动时降级为 budget 形态，不阻断建模型
        return ()


def _resolve_thinking(
    model: str,
    effort: Any,
    user_thinking: Any,
    max_tokens: Any = None,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """决策 thinking 配置（纯函数，便于测试）。

    优先级：用户显式配置（extra_body.thinking）> effort 自动映射 > 不设置。

    两套机制的模型覆盖面不同（Anthropic API 事实）：
    - effort/output_config：Opus 4.5+、Sonnet 4.6+（profile 声明档位即支持）；
    - thinking/budget_tokens：Sonnet 系及更早 + 第三方 Anthropic 兼容端点
      （GLM/MiniMax/DeepSeek 经网关，它们兼容的是 Claude Code 生态的 thinking
      字段；output_config.effort 是最新代 API，第三方基本不认）。

    注意与库内部门控（chat_models.py L1694-1699）的差异：库仅在 profile 声明
    xhigh 时自动补 adaptive thinking（因 adaptive+summarized 形态只有最新代
    认识）；本函数的路由门控是「声明了任意档位」——opus-4-5/4-6/sonnet-4-6
    声明档位即支持 effort，走原生字段，不发任何 thinking。

    Returns:
        (constructor_thinking, native_effort, dropped_effort)
        - constructor_thinking: 传给 ChatAnthropic 构造参数 thinking 的 dict
        - native_effort: 传给库原生 reasoning_effort 的档位（库内部转
          output_config.effort）
        - dropped_effort: 无法映射需告警丢弃的档位
    """
    # 用户显式配置完全接管：构造参数不设 thinking，值留在 model_kwargs
    # 透传（非法形态由 Pydantic 校验报可见错误，不静默吞掉）
    if user_thinking is not None:
        return None, None, None
    if not effort:
        return None, None, None

    effort_str = str(effort).lower()
    levels = _model_effort_levels(model)
    if levels:
        # 支持 effort 的模型（Claude Opus 4.5+/Sonnet 4.6+）：
        # 命中声明档位 → 库原生 reasoning_effort；不在声明档位（如 opus-4-5
        # 传 max/xhigh）→ 本地丢弃告警，不放行给 API 报错
        if effort_str in levels:
            return None, effort_str, None
        return None, None, effort_str
    # 不支持 effort（旧 Claude + 第三方兼容端点 + 未知模型名）：
    # 档位映射为 enabled + budget_tokens（max 为动态档，按 max_tokens 钳制）；
    # allow-create 自定义档位无对应概念，丢弃
    budget = _effort_budget(effort_str, max_tokens)
    if budget is not None:
        return {"type": "enabled", "budget_tokens": budget}, None, None
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
            model, effort, model_kwargs.get("thinking"), kwargs.get("max_tokens")
        )
        if dropped_effort:
            logger.warning(
                "Anthropic 模型 %s 不支持推理深度档位 %r，已忽略。"
                "支持的档位取决于模型（或在附加参数中自定义 thinking）。",
                model,
                dropped_effort,
            )
        elif native_effort:
            # 支持 effort 的模型（Opus 4.5+/Sonnet 4.6+）：走库原生字段，
            # 库内部转 output_config.effort；thinking 构造参数不设，交给库判定
            kwargs["reasoning_effort"] = native_effort
            logger.info(
                "Anthropic 模型 %s：推理深度 %r 走原生 reasoning_effort（output_config.effort）",
                model,
                native_effort,
            )
        elif thinking:
            # 不支持 effort 的模型（旧 Claude / GLM、MiniMax 等第三方 Anthropic
            # 兼容端点 / 未知模型名）：enabled + budget_tokens 形态。
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

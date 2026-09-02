"""
统一出站 HTTP 客户端工厂

所有出站请求（LLM SDK、Embedding、MCP、市场、更新检查、API 节点）统一通过
本模块创建客户端，实现：

- trust_env 恒为 False：不读 HTTP_PROXY/HTTPS_PROXY 等环境变量，
  也不读 Windows 注册表系统代理（httpx 默认经 urllib.getproxies 会拾取两者）
- 仅当「系统设置 → 网络代理」配置了 proxy_url 时，才走用户指定的代理

注意：openai / anthropic SDK 已迁移到 httpx2，其自定义 http_client 必须是
httpx2 实例（见 create_llm_*）；普通出站请求与 mcp SDK 仍用 httpx。
"""

import asyncio
import logging
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
import httpx2

logger = logging.getLogger(__name__)

# 允许的代理协议（socks5 需要依赖 socksio）
_PROXY_ALLOWED_SCHEMES = ("http", "https", "socks5", "socks5h")


class _AutoCloseLLMAsyncClient(httpx2.AsyncClient):
    """GC 时自动关闭的 httpx2 AsyncClient（复刻 openai/anthropic SDK 的 wrapper 行为）

    供 LLM SDK 按模型实例创建、无显式生命周期管理的场景使用，
    避免未关闭的连接池随执行次数累积。
    """

    def __del__(self) -> None:
        try:
            if self.is_closed:
                return
            asyncio.get_running_loop().create_task(self.aclose())
        except Exception:
            pass


class _AutoCloseLLMClient(httpx2.Client):
    """_AutoCloseLLMAsyncClient 的同步版本"""

    def __del__(self) -> None:
        try:
            if self.is_closed:
                return
            self.close()
        except Exception:
            pass


def get_proxy_url() -> str:
    """读取全局代理地址（global_config 内存缓存），未配置返回空串"""
    try:
        from app.services.global_config_service import global_config_service

        return (global_config_service.proxy_url or "").strip()
    except Exception:
        logger.debug("读取代理配置失败，按直连处理", exc_info=True)
        return ""


def validate_proxy_url(value: str) -> Optional[str]:
    """校验代理地址格式

    Returns:
        合法返回 None，非法返回错误信息
    """
    value = (value or "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in _PROXY_ALLOWED_SCHEMES:
        return (
            "代理地址协议无效，支持 "
            f"{'/'.join(_PROXY_ALLOWED_SCHEMES)}，如 http://127.0.0.1:7890"
        )
    if not parsed.hostname:
        return "代理地址缺少主机名，如 http://127.0.0.1:7890"
    return None


def create_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """创建出站 httpx.AsyncClient

    强制 trust_env=False（忽略系统代理与环境变量），配置了 proxy_url 时走代理；
    timeout / verify / follow_redirects 等参数原样透传。
    """
    kwargs["trust_env"] = False
    proxy = get_proxy_url()
    if proxy:
        kwargs.setdefault("proxy", proxy)
    return httpx.AsyncClient(**kwargs)


def create_llm_async_client(**kwargs: Any) -> httpx2.AsyncClient:
    """为 LLM SDK / OpenAIEmbeddings 创建 http_async_client（httpx2 实例）

    openai / anthropic SDK 已迁移到 httpx2，自定义 http_client 必须是
    httpx2.AsyncClient。与 create_async_client 相同代理策略；使用 GC 自动
    关闭包装，生命周期跟随持有它的模型实例（与 SDK 自建内部 client 一致）。
    """
    return _build_llm_client(_AutoCloseLLMAsyncClient, kwargs)


def create_llm_sync_client(**kwargs: Any) -> httpx2.Client:
    """create_llm_async_client 的同步版本（供 SDK 同步 client 使用）"""
    return _build_llm_client(_AutoCloseLLMClient, kwargs)


def _build_llm_client(cls, kwargs: dict[str, Any]):
    kwargs["trust_env"] = False
    proxy = get_proxy_url()
    if proxy:
        kwargs.setdefault("proxy", proxy)
    return cls(**kwargs)


def mcp_httpx_client_factory(
    headers: Optional[dict] = None,
    timeout: Optional[httpx.Timeout] = None,
    auth: Optional[httpx.Auth] = None,
) -> httpx.AsyncClient:
    """MCP streamable-http/SSE 的 httpx 客户端工厂（mcp SDK 回调）

    复刻 mcp.shared._httpx_utils.create_mcp_http_client 的默认行为
    （follow_redirects=True、默认 30s/读 300s 超时），并叠加代理策略。
    """
    proxy = get_proxy_url()
    kwargs: dict[str, Any] = {
        "trust_env": False,
        "follow_redirects": True,
        "timeout": timeout or httpx.Timeout(30.0, read=300.0),
    }
    if proxy:
        kwargs["proxy"] = proxy
    if headers:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth
    return httpx.AsyncClient(**kwargs)

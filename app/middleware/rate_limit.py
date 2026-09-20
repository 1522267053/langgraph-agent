"""
404 速率限制（IP 维度，内存存储）

同 IP 在统计窗口内产生超过阈值的 404 视为扫描器行为，
触发后临时封禁该 IP，封禁期内所有请求直接返回 429。

使用方：
- auth_middleware：请求入口检查封禁状态 + 认证拦截前的路径存在性预判
- exception_middleware：全站 404 异常在包装/返回前计数（唯一汇聚点）
"""

import logging
import time

from starlette.requests import Request
from starlette.routing import Mount

from app.constants.timing import (
    RATE_LIMIT_BLOCK_SECONDS,
    RATE_THRESHOLD,
    RATE_WINDOW_SECONDS,
)

logger = logging.getLogger(__name__)

# 404 计数表（ip -> {count, window_start, blocked_until}）
_404_rate_limit: dict[str, dict] = {}

# 本机回环地址集合（内部调用豁免限流）
_LOOPBACK_ADDRS = {"127.0.0.1", "::1", "localhost"}

# 不计入 404 速率限制的路径前缀（正常可能 404 的路径，避免误封）
_IGNORED_404_PREFIXES = (
    "/favicon.ico",
    "/robots.txt",
    "/sitemap.xml",
    "/assets/",
    "/ws/",
)


def get_client_ip(request: Request) -> str:
    """获取客户端真实 IP（兼容 Nginx 反向代理）"""
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    return request.client.host if request.client else ""


def is_404_blocked(ip: str) -> bool:
    """检查 IP 是否因 404 过多被临时封禁"""
    info = _404_rate_limit.get(ip)
    if not info:
        return False
    return info.get("blocked_until", 0) > time.time()


def _record_404(ip: str) -> None:
    """记录一次 404，超限则封禁"""
    now = time.time()
    info = _404_rate_limit.get(ip)
    if not info or now - info["window_start"] > RATE_WINDOW_SECONDS:
        _404_rate_limit[ip] = {"count": 1, "window_start": now, "blocked_until": 0}
        return
    info["count"] += 1
    if info["count"] > RATE_THRESHOLD:
        info["blocked_until"] = now + RATE_LIMIT_BLOCK_SECONDS
        logger.warning("IP %s 因 404 过多被封禁 %d 秒", ip, RATE_LIMIT_BLOCK_SECONDS)


def record_404_request(request: Request) -> None:
    """记录一次 404 请求（中间件与异常处理器共用入口，内部判断豁免条件）"""
    ip = get_client_ip(request)
    if ip in _LOOPBACK_ADDRS:
        return
    path = request.url.path
    if any(path == p or path.startswith(p) for p in _IGNORED_404_PREFIXES):
        return
    _record_404(ip)


def api_path_exists(app, path: str) -> bool:
    """判断 API 路径是否命中已注册路由（认证拦截前的存在性预判）

    Mount 做前缀匹配（如 /uploads）；根挂载（app.mount("/", 前端SPA)，
    其 path 被归一化为空串）不算命中——兜底目录对任意路径都返回
    200/404，不能作为存在依据。其余路由用 path_regex 精确匹配。
    """
    for route in app.routes:
        if isinstance(route, Mount):
            if route.path and route.path != "/" and path.startswith(route.path):
                return True
        else:
            regex = getattr(route, "path_regex", None)
            if regex is not None and regex.match(path):
                return True
    return False

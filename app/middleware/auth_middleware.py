"""
认证中间件

基于 session cookie 的简单密码认证。
密码来源优先级：global_config（DB）> .env 环境变量。
无密码配置时放行所有请求。
包含 404 速率限制封禁（实现见 rate_limit.py）：同 IP 窗口内 404 过多临时封禁。
"""

import logging
import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config.database import AsyncSessionLocal
from app.middleware.rate_limit import (
    _LOOPBACK_ADDRS,
    api_path_exists,
    get_client_ip,
    is_404_blocked,
    record_404_request,
)
from app.services.global_config_service import global_config_service

logger = logging.getLogger(__name__)

COOKIE_NAME = "auth_session"
COOKIE_MAX_AGE = 7 * 24 * 60 * 60  # 7 天


# 豁免路径（不需要登录即可访问）
EXEMPT_PATHS = {
    "/api/auth/login",
    "/api/auth/check",
    "/api/config/check",
    "/api/config/providers",
    "/api/update/check-update",
    "/api/update/status",
    "/api/health",
    # WS 网关文件传输端点（token 自鉴权，见 ws_gateway_api.py）
    "/api/ws-gateway/upload",
    "/api/ws-gateway/download",
}

# 仅在未初始化时豁免认证的路径（初始化后需登录才能访问）
_INIT_ONLY_EXEMPT_PATHS = {
    "/api/config/init",
}

# 需要认证的静态资源路径（API 文档、用户上传文件等）
PROTECTED_STATIC_PATHS = {"/docs", "/redoc", "/openapi.json", "/uploads"}

# 内存缓存密码哈希，避免每次请求都查 DB
_cached_password_hash: str | None = None

# 内存缓存初始化状态，避免每次请求都查 DB
_cached_initialized: bool | None = None


async def _get_password_hash_cached() -> str | None:
    """获取密码哈希（启动时加载一次，密码变更时清缓存重新加载）"""
    global _cached_password_hash
    if _cached_password_hash is not None:
        return _cached_password_hash

    try:
        async with AsyncSessionLocal() as db:
            val = await global_config_service.get_password_hash(db)
            _cached_password_hash = val
            return val
    except Exception:
        logger.exception("获取密码哈希失败")
        return _cached_password_hash


async def _is_system_initialized() -> bool:
    """检查系统是否已完成初始化（内存缓存，首次查 DB）"""
    global _cached_initialized
    if _cached_initialized is not None:
        return _cached_initialized

    try:
        async with AsyncSessionLocal() as db:
            _cached_initialized = await global_config_service.is_initialized(db)
            return _cached_initialized
    except Exception:
        logger.exception("检查初始化状态失败")
        return False


def _verify_session(token: bytes | None, password_hash: str) -> bool:
    """验证 session token"""
    import hashlib
    import base64

    if not token:
        return False
    try:
        decoded = base64.b64decode(token).decode("utf-8")
        parts = decoded.split(":", 1)
        if len(parts) != 2:
            return False
        timestamp_str, signature = parts
        expected = hashlib.sha256(
            f"{timestamp_str}:{password_hash}".encode("utf-8")
        ).hexdigest()
        if signature != expected:
            return False
        ts = int(timestamp_str)
        return time.time() - ts <= COOKIE_MAX_AGE
    except Exception:
        return False


def _is_exempt(path: str) -> bool:
    """判断路径是否在豁免列表中"""
    for exempt in EXEMPT_PATHS:
        if path == exempt or path.startswith(exempt + "/"):
            return True
    return False


def _is_init_only_exempt(path: str) -> bool:
    """判断路径是否属于仅初始化阶段豁免的路径"""
    for p in _INIT_ONLY_EXEMPT_PATHS:
        if path == p or path.startswith(p + "/"):
            return True
    return False


def _is_protected_static(path: str) -> bool:
    """判断路径是否属于需要认证的静态资源（API 文档等）"""
    for prefix in PROTECTED_STATIC_PATHS:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


# 客户端 IP 与回环判断统一由 rate_limit 提供（历史调用方沿用旧名）
_get_client_ip = get_client_ip


def _is_loopback(request: Request) -> bool:
    """判断请求是否来自本机（内部服务调用，如 AI 的 api_call_tool / shell_executor）"""
    return get_client_ip(request) in _LOOPBACK_ADDRS


class AuthMiddleware(BaseHTTPMiddleware):
    """认证中间件"""

    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path

        # 封禁期内直接短路，不进入业务逻辑（404 计数由异常处理器统一负责）
        if is_404_blocked(get_client_ip(request)):
            return JSONResponse(
                status_code=429,
                content={"detail": "Too Many Requests"},
            )

        # 非 API 且非受保护静态路径
        if not path.startswith("/api/") and not _is_protected_static(path):
            return await call_next(request)

        # 仅初始化阶段豁免的路径：未初始化时放行，已初始化需登录
        if _is_init_only_exempt(path):
            if not await _is_system_initialized():
                return await call_next(request)
        elif _is_exempt(path):
            return await call_next(request)

        # 本机请求直接放行（AI 工具通过 api_call_tool / shell curl 调用本平台 API）
        if _is_loopback(request):
            return await call_next(request)

        # 获取密码哈希
        password_hash = await _get_password_hash_cached()
        if not password_hash:
            # 未配置密码，放行所有请求
            return await call_next(request)

        # 未认证前先判路径存在性：不存在的 API 路径计入 404 限流并返回 404，
        # 避免扫描器借 401 探测 API 面、也绕过 404 限流
        if not api_path_exists(request.app, path):
            record_404_request(request)
            return JSONResponse(
                status_code=404,
                content={"code": 404, "msg": "Not Found", "data": None},
            )

        # 检查 session cookie
        session_token = request.cookies.get(COOKIE_NAME)
        if _verify_session(
            session_token.encode("utf-8") if session_token else None, password_hash
        ):
            return await call_next(request)

        # 未认证
        return JSONResponse(
            status_code=401,
            content={"code": 0, "msg": "未登录或会话已过期", "data": None},
        )


def invalidate_password_cache():
    """清除密码缓存（密码变更时调用）"""
    global _cached_password_hash
    _cached_password_hash = None


def invalidate_init_cache():
    """清除初始化状态缓存（初始化完成时调用）"""
    global _cached_initialized
    _cached_initialized = None

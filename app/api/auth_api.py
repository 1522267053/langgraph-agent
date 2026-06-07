"""
认证 API 路由
"""

import logging
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.schemas.base_schema import ApiResponse
from app.schemas.global_config_schema import LoginRequest, AuthCheckResponse
from app.services.global_config_service import global_config_service
from app.middleware.auth_middleware import (
    COOKIE_NAME,
    COOKIE_MAX_AGE,
    _get_client_ip,
)

logger = logging.getLogger(__name__)

# ---- 登录失败锁定（IP 维度，内存存储） ----

_LOCK_THRESHOLD = 5
_LOCK_DURATION = 300

_login_failures: dict[str, list[float]] = defaultdict(list)


def _is_locked(ip: str) -> bool:
    """检查 IP 是否被锁定"""
    cutoff = time.time() - _LOCK_DURATION
    recent = [t for t in _login_failures[ip] if t > cutoff]
    _login_failures[ip] = recent
    return len(recent) >= _LOCK_THRESHOLD


def _get_remaining_attempts(ip: str) -> int:
    """获取剩余尝试次数"""
    cutoff = time.time() - _LOCK_DURATION
    recent = [t for t in _login_failures[ip] if t > cutoff]
    return max(0, _LOCK_THRESHOLD - len(recent))


def _get_lock_remaining(ip: str) -> int:
    """获取锁定剩余秒数"""
    attempts = _login_failures.get(ip, [])
    if not attempts:
        return 0
    oldest_in_window = min(attempts)
    elapsed = time.time() - oldest_in_window
    return max(0, int(_LOCK_DURATION - elapsed))


def _record_failure(ip: str) -> None:
    """记录失败"""
    _login_failures[ip].append(time.time())


def _clear_failures(ip: str) -> None:
    """登录成功后清除"""
    _login_failures.pop(ip, None)


class AuthApi:
    """认证 API"""

    def __init__(self):
        self.router = APIRouter(prefix="/api/auth", tags=["认证"])
        self._register_routes()

    def _register_routes(self):
        @self.router.get(
            "/check",
            response_model=ApiResponse[AuthCheckResponse],
            summary="检查是否需要登录",
        )
        async def check_auth(request: Request, db: AsyncSession = Depends(get_db)):
            """检查是否需要登录，以及当前是否已认证"""
            has_password = await global_config_service.has_password(db)
            has_username = await global_config_service.has_username(db)
            authenticated = False
            if has_password:
                session_token = request.cookies.get(COOKIE_NAME)
                if session_token:
                    expected_hash = await global_config_service.get_password_hash(db)
                    if expected_hash:
                        authenticated = _verify_session_token(
                            session_token, expected_hash
                        )
            username = None
            if authenticated:
                username = await global_config_service.get_username(db)

            return ApiResponse.success(
                data=AuthCheckResponse(
                    need_login=has_password,
                    authenticated=authenticated,
                    has_username=has_username,
                    username=username,
                )
            )

        @self.router.post("/login", response_model=ApiResponse, summary="登录")
        async def login(
            request: Request,
            body: LoginRequest,
            response: Response,
            db: AsyncSession = Depends(get_db),
        ):
            """验证用户名+密码并设置登录会话"""
            has_password = await global_config_service.has_password(db)
            if not has_password:
                return ApiResponse.error(msg="未配置登录密码")

            client_ip = _get_client_ip(request)

            # 检查锁定
            if _is_locked(client_ip):
                remaining = _get_lock_remaining(client_ip)
                return ApiResponse.error(
                    msg=f"登录失败次数过多，请 {remaining} 秒后再试"
                )

            # 校验用户名
            has_username = await global_config_service.has_username(db)
            if not has_username:
                return ApiResponse.error(msg="请先在设置中配置用户名")

            if not body.username:
                _record_failure(client_ip)
                left = _get_remaining_attempts(client_ip)
                return ApiResponse.error(msg=f"请输入用户名，还可尝试 {left} 次")
            username_valid = await global_config_service.verify_username(
                db, body.username
            )
            if not username_valid:
                _record_failure(client_ip)
                left = _get_remaining_attempts(client_ip)
                if left <= 0:
                    return ApiResponse.error(msg="登录失败次数过多，请5分钟后再试")
                return ApiResponse.error(msg=f"用户名或密码错误，还可尝试 {left} 次")

            # 校验密码
            valid = await global_config_service.verify_password_hash(db, body.password)
            if not valid:
                _record_failure(client_ip)
                left = _get_remaining_attempts(client_ip)
                if left <= 0:
                    return ApiResponse.error(msg="登录失败次数过多，请5分钟后再试")
                return ApiResponse.error(msg=f"用户名或密码错误，还可尝试 {left} 次")

            # 登录成功
            _clear_failures(client_ip)
            expected_hash = await global_config_service.get_password_hash(db)

            timestamp = str(int(time.time()))
            session_token = _make_session_token(expected_hash, timestamp)

            response.set_cookie(
                key=COOKIE_NAME,
                value=session_token,
                max_age=COOKIE_MAX_AGE,
                httponly=True,
                samesite="lax",
                path="/",
            )
            logger.info("登录成功（IP: %s）", client_ip)
            return ApiResponse.success(msg="登录成功")

        @self.router.post("/logout", response_model=ApiResponse, summary="登出")
        async def logout(response: Response):
            """清除登录会话"""
            response.delete_cookie(key=COOKIE_NAME, path="/")
            return ApiResponse.success(msg="已退出登录")


auth_api = AuthApi()
router = auth_api.router


def _make_session_token(password_hash: str, timestamp: str) -> str:
    import hashlib
    import base64

    payload = timestamp
    signature = hashlib.sha256(f"{payload}:{password_hash}".encode("utf-8")).hexdigest()
    token = f"{payload}:{signature}"
    return base64.b64encode(token.encode("utf-8")).decode("utf-8")


def _verify_session_token(token: str, password_hash: str) -> bool:
    import hashlib
    import base64
    import time as _time

    try:
        decoded = base64.b64decode(token.encode("utf-8")).decode("utf-8")
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
        if _time.time() - ts > COOKIE_MAX_AGE:
            return False
        return True
    except Exception:
        return False

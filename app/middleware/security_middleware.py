"""
安全响应头中间件
"""

import logging
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "X-Permitted-Cross-Domain-Policies": "none",
}

CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.bootcdn.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.bootcdn.net; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'self'; "
    "form-action 'self'; "
    "base-uri 'self'"
)


class SecurityHeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        response: Response = await call_next(request)

        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)

        response.headers.setdefault("Content-Security-Policy", CSP_POLICY)

        return response

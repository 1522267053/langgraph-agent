"""
中间件模块
"""

from app.middleware.exception_middleware import register_exception_handlers

__all__ = ["register_exception_handlers"]

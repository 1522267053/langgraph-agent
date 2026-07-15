"""
WebSocket 通知端点

通过 Cookie 认证，BaseHTTPMiddleware 不拦截 WebSocket 连接。
注意：不能通过 app/api/ 自动注册（会继承 FastAPI 全局 dependencies 中的 HTTPBearer），
需要在 main.py 中调用 register_websocket_routes(app) 注册。
"""

import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.routing import WebSocketRoute

from app.config.database import AsyncSessionLocal
from app.middleware.auth_middleware import (
    COOKIE_NAME,
    _get_password_hash_cached,
    _is_system_initialized,
    _verify_session,
)
from app.services.global_config_service import global_config_service
from app.services.ws_manager import ws_manager

logger = logging.getLogger(__name__)


async def notification_ws(websocket: WebSocket):
    """WebSocket 通知通道

    通过 session cookie 认证（与 HTTP 请求共用 auth_session cookie）。
    未配置密码时免认证直接连接。
    握手成功后绑定用户名，支持定向推送。
    """
    # ---- 认证校验 ----
    initialized = await _is_system_initialized()
    if initialized:
        password_hash = await _get_password_hash_cached()
        if password_hash:
            token = websocket.cookies.get(COOKIE_NAME)
            if not token or not _verify_session(
                token.encode("utf-8") if token else None, password_hash
            ):
                await websocket.close(code=4401)
                return

    # ---- 读取当前用户名（单用户系统，从全局配置获取）----
    username = "default"
    try:
        async with AsyncSessionLocal() as db:
            name = await global_config_service.get_username(db)
            if name:
                username = name
    except Exception as e:
        logger.warning(f"WebSocket 握手时读取用户名失败，使用默认值: {e}")

    # ---- 接受连接并绑定用户 ----
    await ws_manager.connect(websocket, username)

    try:
        # 保持连接，忽略客户端消息（仅做心跳）
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info(f"WebSocket 断开，当前连接数: {ws_manager.connection_count()}")
    except Exception as e:
        logger.warning(f"WebSocket 异常断开: {e}")
    finally:
        ws_manager.disconnect(websocket)


def register_websocket_routes(app: FastAPI) -> None:
    """注册 WebSocket 路由

    使用 Starlette 原生 WebSocketRoute 注册，绕过 FastAPI 全局 dependencies
    中的 HTTPBearer（HTTPBearer.__call__ 需要 HTTP Request，不兼容 WebSocket）。
    """
    app.router.routes.insert(
        0, WebSocketRoute("/ws/notifications", endpoint=notification_ws)
    )
    from app.api.ws_trigger_api import register_trigger_ws_routes

    register_trigger_ws_routes(app)

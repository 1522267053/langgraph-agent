"""
应用工厂

集中管理 FastAPI 应用的创建、中间件配置、路由注册和静态资源挂载。
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.security import HTTPBearer
from fastapi.staticfiles import StaticFiles

from app.api.ws_api import register_websocket_routes
from app.config.build_utils import get_frontend_dist_dir
from app.config.lifespan import shutdown, startup
from app.config.settings import settings
from app.middleware import register_exception_handlers
from app.middleware.auth_middleware import AuthMiddleware
from app.middleware.security_middleware import SecurityHeaderMiddleware
from app.schemas.base_schema import ApiResponse
from app.utils.loader import register_all_routers

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """应用生命周期"""
        await startup()
        yield
        await shutdown()

    # ---- 创建应用 ----
    http_bearer = HTTPBearer(auto_error=False)
    app = FastAPI(
        title="智能体平台API",
        description="智能体平台后端API服务",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.debug else None,
        dependencies=[Depends(http_bearer)],  # Swagger UI Authorize 按钮所需
    )

    # ---- 中间件 ----
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(SecurityHeaderMiddleware)

    # ---- 路由注册 ----
    register_all_routers(app)
    register_websocket_routes(app)
    _register_docs_routes(app)
    _register_health_check(app)
    _mount_static_files(app)

    logger.info("[OK] FastAPI 应用已创建")
    return app


def _register_docs_routes(app: FastAPI) -> None:
    """注册自定义文档路由（debug 模式）"""
    if not settings.debug:
        return

    @app.get("/docs", include_in_schema=False)
    async def custom_swagger_ui_html():
        """自定义 Swagger UI 使用国内 CDN"""
        return get_swagger_ui_html(
            openapi_url=app.openapi_url or "/openapi.json",
            title=app.title + " - Swagger UI",
            swagger_js_url="https://cdn.bootcdn.net/ajax/libs/swagger-ui/5.29.1/swagger-ui-bundle.min.js",
            swagger_css_url="https://cdn.bootcdn.net/ajax/libs/swagger-ui/5.29.1/swagger-ui.min.css",
            swagger_favicon_url="https://fastapi.tiangolo.com/img/favicon.png",
        )

    @app.get("/redoc", include_in_schema=False)
    async def redoc_html():
        """自定义 ReDoc 使用国内 CDN"""
        return get_redoc_html(
            openapi_url=app.openapi_url or "/openapi.json",
            title=app.title + " - ReDoc",
            redoc_js_url="https://cdn.bootcdn.net/ajax/libs/redoc/2.1.3/bundles/redoc.standalone.js",
            redoc_favicon_url="https://fastapi.tiangolo.com/img/favicon.png",
        )


def _register_health_check(app: FastAPI) -> None:
    """注册健康检查端点"""

    @app.get("/api/health", tags=["健康检查"])
    async def health_check():
        """健康检查"""
        from app.config.version import __version__

        return ApiResponse.success(
            {
                "message": "欢迎使用langraph智能体平台API",
                "status": "running",
                "version": __version__,
            }
        )


def _mount_static_files(app: FastAPI) -> None:
    """挂载静态文件目录"""
    # 上传文件目录（预览用）
    upload_abs_path = settings.get_absolute_path(settings.upload_dir)
    os.makedirs(upload_abs_path, exist_ok=True)
    app.mount(
        f"/{settings.upload_dir}",
        StaticFiles(directory=str(upload_abs_path)),
        name="uploads",
    )

    # 前端静态文件（放在所有路由之后，作为 fallback）
    frontend_dist = get_frontend_dist_dir()
    if frontend_dist.exists():
        app.mount(
            "/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend"
        )

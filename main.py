"""
FastAPI 应用主入口
"""

import sys

if sys.platform == "win32":
    import colorama

    colorama.init()

import asyncio
import logging
import logging.config
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.params import Depends
from fastapi.security import HTTPBearer
import os

from fastapi.staticfiles import StaticFiles

from app.config.database import init_db, close_db
from app.config.settings import settings
from app.config.logging_config import cleanup_logs, get_uvicorn_log_config
from app.config.build_utils import BASE_DIR, get_frontend_dist_dir
from app.middleware import register_exception_handlers
from app.middleware.auth_middleware import AuthMiddleware
from app.middleware.security_middleware import SecurityHeaderMiddleware
from app.utils.loader import (
    load_all_models,
    load_all_handlers,
    load_all_providers,
    register_all_routers,
)
from app.schemas.base_schema import ApiResponse

logging.config.dictConfig(get_uvicorn_log_config())
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时清理过期日志
    cleanup_logs(str(BASE_DIR / settings.log_dir), "app", settings.log_backup_days)

    # 自动加载所有模型
    load_all_models()

    # 自动加载所有节点处理器（触发装饰器注册）
    load_all_handlers()

    # 自动加载所有 AI 提供商（触发装饰器注册）
    load_all_providers()

    # 启动时初始化数据库
    # 确保临时文件目录存在
    (BASE_DIR / "temp").mkdir(parents=True, exist_ok=True)

    await init_db()
    logger.info("[OK] Database initialized")

    # 启动定时任务调度器
    from app.services.scheduler_service import scheduler_service

    await scheduler_service.start()

    # 延迟打开浏览器，等待服务就绪
    async def _open_browser():
        import asyncio as _asyncio

        url = f"http://127.0.0.1:{settings.app_port}/"
        await _asyncio.sleep(0.2)
        if sys.platform == "win32":
            import subprocess

            # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_BREAKAWAY_FROM_JOB
            CREATE_BREAKAWAY = 0x01000000
            subprocess.Popen(
                f'start "" "{url}"',
                shell=True,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.DETACHED_PROCESS
                | CREATE_BREAKAWAY,
            )

    asyncio.create_task(_open_browser())

    yield

    # 关闭时清理所有 MCP 服务器连接（关闭 stdio 子进程等）
    logger.info("Closing MCP connections...")
    from app.agent_flow.mcp_manager import mcp_tool_manager

    await mcp_tool_manager.clear_all_cache()
    logger.info("[OK] MCP connections closed")

    # 关闭定时任务调度器
    logger.info("Closing scheduler...")
    from app.services.scheduler_service import scheduler_service as _scheduler

    await _scheduler.shutdown()
    logger.info("[OK] Scheduler closed")

    # 关闭时清理数据库连接
    logger.info("Closing database connection...")
    await close_db()
    logger.info("[OK] Database connection closed")


http_bearer = HTTPBearer(auto_error=False)

# 创建 FastAPI 应用实例
# 禁用默认文档，使用国内 CDN 自定义文档页面
app = FastAPI(
    title="智能体平台API",
    description="智能体平台后端API服务",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,  # 禁用默认文档
    redoc_url=None,  # 禁用默认文档
    openapi_url="/openapi.json" if settings.debug else None,
    dependencies=[
        Depends(http_bearer)
    ],  # 这里必须这么加，否则访问接口的时候不会有Authorize按钮
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册全局异常处理器
register_exception_handlers(app)

app.add_middleware(AuthMiddleware)
app.add_middleware(SecurityHeaderMiddleware)

# 自动注册所有路由
register_all_routers(app)


if settings.debug:

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


@app.get("/api/health", tags=["健康检查"])
async def root():
    """健康检查"""
    from app.config.version import __version__

    return ApiResponse.success(
        {
            "message": "欢迎使用langraph智能体平台API",
            "status": "running",
            "version": __version__,
        }
    )


# 挂载上传文件静态目录（预览用）
upload_abs_path = settings.get_absolute_path(settings.upload_dir)
os.makedirs(upload_abs_path, exist_ok=True)
app.mount(
    f"/{settings.upload_dir}",
    StaticFiles(directory=str(upload_abs_path)),
    name="uploads",
)

# 挂载前端静态文件（放在所有路由之后，作为 fallback）
# html=True: 未匹配路径返回 index.html，支持 Vue Router history 模式
frontend_dist = get_frontend_dist_dir()
if frontend_dist.exists():
    app.mount(
        "/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend"
    )

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=settings.app_host,
        port=settings.app_port,
        log_config=get_uvicorn_log_config(),
        timeout_keep_alive=300,
    )

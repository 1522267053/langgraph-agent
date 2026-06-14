"""
应用生命周期管理

包含应用启动和关闭时的资源初始化与清理逻辑。
"""

import asyncio
import logging
import sys

from app.config.build_utils import BASE_DIR
from app.config.database import AsyncSessionLocal, close_db, init_db
from app.config.logging_config import cleanup_logs
from app.config.settings import settings

logger = logging.getLogger(__name__)


async def _load_notification_config() -> None:
    """从 DB 加载通知开关到 WebSocket 管理器"""
    from app.services.global_config_service import global_config_service
    from app.services.ws_manager import ws_manager

    async with AsyncSessionLocal() as db:
        notif_str = await global_config_service.get_value(
            db, "execution_notification_enabled"
        )
        ws_manager.set_notification_enabled(
            notif_str.lower() != "false" if notif_str else True
        )


async def _open_browser() -> None:
    """延迟打开浏览器"""
    url = f"http://127.0.0.1:{settings.app_port}/"
    await asyncio.sleep(0.2)
    if sys.platform == "win32":
        import subprocess

        CREATE_BREAKAWAY = 0x01000000
        subprocess.Popen(
            f'start "" "{url}"',
            shell=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | CREATE_BREAKAWAY,
        )


async def startup() -> None:
    """应用启动流程"""
    from app.utils.loader import (
        load_all_handlers,
        load_all_models,
        load_all_providers,
    )
    from app.services.scheduler_service import scheduler_service

    # ---- 清理过期日志 ----
    cleanup_logs(str(BASE_DIR / settings.log_dir), "app", settings.log_backup_days)

    # ---- 自动注册（模型、节点处理器、AI 提供商） ----
    load_all_models()
    load_all_handlers()
    load_all_providers()

    # ---- 初始化数据库 ----
    (BASE_DIR / "temp").mkdir(parents=True, exist_ok=True)
    await init_db()
    logger.info("[OK] Database initialized")

    # ---- 加载通知配置 ----
    await _load_notification_config()

    # ---- 启动定时任务调度器 ----
    await scheduler_service.start()

    # ---- 打开浏览器 ----
    asyncio.create_task(_open_browser())


async def shutdown() -> None:
    """应用关闭流程"""
    from app.agent_flow.mcp_manager import mcp_tool_manager
    from app.services.scheduler_service import scheduler_service

    # ---- 清理 MCP 连接 ----
    logger.info("Closing MCP connections...")
    await mcp_tool_manager.clear_all_cache()
    logger.info("[OK] MCP connections closed")

    # ---- 关闭定时任务调度器 ----
    logger.info("Closing scheduler...")
    await scheduler_service.shutdown()
    logger.info("[OK] Scheduler closed")

    # ---- 关闭数据库连接 ----
    logger.info("Closing database connection...")
    await close_db()
    logger.info("[OK] Database connection closed")

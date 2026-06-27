"""
应用生命周期管理

包含应用启动和关闭时的资源初始化与清理逻辑。
"""

import asyncio
import logging
import socket
import sys

from app.config.build_utils import BASE_DIR, is_desktop_mode
from app.config.database import AsyncSessionLocal, close_db, init_db
from app.config.logging_config import cleanup_logs
from app.config.settings import settings

logger = logging.getLogger(__name__)


def _get_local_ips() -> list[str]:
    """获取本机所有 IPv4 地址（排除 127.0.0.1）"""
    ips: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and ip != "127.0.0.1":
                ips.append(ip)
    except socket.gaierror:
        pass
    # 兜底：UDP socket 探测默认路由出口 IP
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
            s.close()
        except OSError:
            ips.append("127.0.0.1")
    return ips


def _log_startup_banner() -> None:
    """打印自定义启动横幅，列出所有可访问地址"""
    port = settings.app_port
    addresses = ["127.0.0.1", *_get_local_ips()]
    logger.info("Uvicorn running on:")
    for addr in addresses:
        logger.info("  → http://%s:%d (Press CTRL+C to quit)", addr, port)


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

    # ---- 同步内置技能（扫描 skills/ 目录） ----
    from app.services.builtin_agent_service import builtin_agent_service

    async with AsyncSessionLocal() as db:
        await builtin_agent_service.sync_skills(db)
    logger.info("[OK] Built-in skills synced")

    # ---- 启动定时任务调度器 ----
    await scheduler_service.start()

    # ---- 打印自定义启动横幅 ----
    _log_startup_banner()

    # ---- 打开浏览器（桌面模式下跳过） ----
    if not is_desktop_mode():
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

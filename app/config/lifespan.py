"""
应用生命周期管理

包含应用启动和关闭时的资源初始化与清理逻辑。
"""

import asyncio
import logging
import socket

from app.config.build_utils import (
    BASE_DIR,
    IS_WINDOWS,
    IS_WIN_PACKAGED,
    get_agents_base_dir,
    get_temp_dir,
)
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
    if settings.app_host in ["127.0.0.1", "localhost"]:
        return
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
    if IS_WINDOWS:
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
    get_temp_dir()  # 确保 workspace/temp/ 存在
    get_agents_base_dir()  # 确保 workspace/agents/ 存在
    await init_db()
    logger.info("[OK] Database initialized")

    # ---- 迁移旧版上下文压缩消息格式 ----
    from app.services.agent_executor_service import agent_executor_service

    async with AsyncSessionLocal() as db:
        await agent_executor_service.migrate_legacy_compression_messages(db)

    # ---- 更新收尾：若由 updater 拉起且最终结果未写入，后台轮询判定成功/中断 ----
    # ---- 更新续传：进程重启前处于下载中的，重新拉起下载任务 ----
    from app.services.update_service import update_service

    # 预加载全局配置缓存（含 proxy_url），确保持久 HTTP 客户端按代理配置创建
    from app.services.global_config_service import global_config_service

    async with AsyncSessionLocal() as db:
        await global_config_service.ensure_ai_cache(db)

    update_service.initialize_http_client()
    update_service.start_pending_result_resolver()
    update_service.start_pending_download_resume()

    # ---- 加载通知配置 ----
    await _load_notification_config()

    # ---- 同步内置技能（扫描 skills/ 目录） ----
    from app.services.builtin_agent_service import builtin_agent_service

    async with AsyncSessionLocal() as db:
        await builtin_agent_service.sync_skills(db)
    logger.info("[OK] Built-in skills synced")

    # ---- 启动定时任务调度器 ----
    await scheduler_service.start()

    # ---- 加载 AI 供应商适配器缓存，若表为空则首次同步 ----
    from app.services.ai_provider_service import (
        _load_adapter_cache,
        ai_provider_service,
    )

    async with AsyncSessionLocal() as db:
        await _load_adapter_cache(db)
        count = await ai_provider_service.count(db)

    logger.info("[OK] AI provider adapter cache loaded")
    if count == 0:
        logger.info("AI 供应商表为空，触发首次本地初始化...")
        try:
            await ai_provider_service.sync_from_local()
        except Exception as e:
            logger.error(f"本地初始化 AI 供应商/模型数据失败: {e}", exc_info=True)

    # ---- 打印自定义启动横幅 ----
    _log_startup_banner()

    # ---- 打开浏览器（Windows 打包由加载页处理，避免重复打开标签页） ----
    if not IS_WIN_PACKAGED:
        asyncio.create_task(_open_browser())


async def shutdown() -> None:
    """应用关闭流程"""
    from app.agent_flow.mcp_manager import mcp_tool_manager
    from app.services.agent_executor_service import agent_executor_service
    from app.services.scheduler_service import scheduler_service
    from app.services.update_service import update_service

    # ---- 先停止任务生产者，避免 Agent 清理期间产生新执行 ----
    logger.info("Closing scheduler...")
    await scheduler_service.shutdown()
    logger.info("[OK] Scheduler closed")

    # ---- 停止后台 Agent 执行，确保其数据库会话先完成清理 ----
    logger.info("Stopping background Agent runs...")
    await agent_executor_service.shutdown_runs()
    logger.info("[OK] Background Agent runs stopped")

    # ---- 清理 MCP 连接 ----
    logger.info("Closing MCP connections...")
    await mcp_tool_manager.clear_all_cache()
    logger.info("[OK] MCP connections closed")

    # ---- 关闭自动更新 HTTP 客户端 ----
    await update_service.close_http_client()

    # ---- 关闭数据库连接 ----
    logger.info("Closing database connection...")
    await close_db()
    logger.info("[OK] Database connection closed")

"""
FastAPI 应用主入口
"""

import os
import sys

from app.config.build_utils import IS_WINDOWS, IS_WIN_PACKAGED

# ---- Windows 打包（无控制台）环境：尽早重定向 stdout/stderr 到 devnull ----
if IS_WIN_PACKAGED:
    _devnull = open(os.devnull, "w")
    sys.stdout = _devnull
    sys.stderr = _devnull
    # 单实例检测：已有实例运行则打开其浏览器并退出新进程
    from app.config.tray import handle_duplicate_instance

    if handle_duplicate_instance():
        sys.exit(0)

    # 立即创建托盘图标（在加载页和重型 import 之前）
    from app.config.tray import create_tray_icon

    create_tray_icon()

    # 打开浏览器加载页（在重型 import 之前，争取 1 秒内弹出）
    from app.config.tray import open_loading_page

    open_loading_page()

if IS_WINDOWS and not IS_WIN_PACKAGED:
    import colorama

    colorama.init()

import logging.config

from app.config.app_setup import create_app
from app.config.logging_config import get_uvicorn_log_config
from app.config.settings import settings

# ---- 初始化日志 ----
logging.config.dictConfig(get_uvicorn_log_config())

# ---- 创建应用（uvicorn 通过 main:app 引用） ----
app = create_app()


if __name__ == "__main__":
    if IS_WIN_PACKAGED:
        from app.config.tray import run_with_tray

        run_with_tray(app)
    else:
        import uvicorn

        uvicorn.run(
            app,
            host=settings.app_host,
            port=settings.app_port,
            log_config=get_uvicorn_log_config(),
            timeout_keep_alive=300,
        )

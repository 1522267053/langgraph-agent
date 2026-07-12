"""
FastAPI 应用主入口
"""

import os
import sys

from app.config.build_utils import _is_packaged

_IS_WIN_PACKAGED = _is_packaged() and sys.platform == "win32"

# ---- Windows 打包（无控制台）环境：尽早重定向 stdout/stderr 到 devnull ----
if _IS_WIN_PACKAGED:
    _devnull = open(os.devnull, "w")
    sys.stdout = _devnull
    sys.stderr = _devnull
    # 立即打开加载页（在重型 import 之前，争取 1 秒内弹出浏览器）
    from app.config.tray import open_loading_page

    open_loading_page()

if sys.platform == "win32" and not _IS_WIN_PACKAGED:
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
    if _IS_WIN_PACKAGED:
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

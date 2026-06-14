"""
FastAPI 应用主入口
"""

import sys

if sys.platform == "win32":
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
    import uvicorn

    uvicorn.run(
        app,
        host=settings.app_host,
        port=settings.app_port,
        log_config=get_uvicorn_log_config(),
        timeout_keep_alive=300,
    )

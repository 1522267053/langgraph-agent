"""
Uvicorn 日志配置模块
提供统一的日志配置,包括控制台和文件输出
"""

import logging
import os
import time
from copy import deepcopy
from logging.handlers import RotatingFileHandler
from uvicorn.config import LOGGING_CONFIG

from app.config.settings import settings
from app.config.build_utils import BASE_DIR

logger = logging.getLogger(__name__)


class VideoRangeFilter(logging.Filter):
    """过滤视频 Range 请求（206 Partial Content）"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            return record.args[4] != 206
        except (IndexError, TypeError):
            return True


def cleanup_logs(
    log_dir: str,
    base_name: str,
    backup_days: int,
) -> int:
    """删除超过 backup_days 天的日志文件。

    Args:
        log_dir: 日志文件目录
        base_name: 日志文件基础名（如 app）
        backup_days: 保留天数，超过此天数的日志将被删除

    Returns:
        删除的文件数量
    """
    if backup_days <= 0:
        return 0

    cutoff = time.time() - backup_days * 86400
    prefix = f"{base_name}_"
    deleted = 0

    if not os.path.isdir(log_dir):
        return 0

    for f in os.listdir(log_dir):
        if not f.startswith(prefix) or not f.endswith(".log"):
            continue
        path = os.path.join(log_dir, f)
        if not os.path.isfile(path):
            continue
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                deleted += 1
                logger.debug("已清理过期日志: %s", f)
        except OSError as e:
            logger.warning("清理日志文件失败 %s: %s", f, e)

    if deleted > 0:
        logger.info(
            "日志清理完成，删除 %d 个过期文件（保留 %d 天）", deleted, backup_days
        )

    return deleted


class SizedTimedRotatingFileHandler(RotatingFileHandler):
    """
    按天轮转 + 单文件大小限制的日志处理器

    活跃文件: app_2026-04-06.log
    超限分割: app_2026-04-06.1.log, app_2026-04-06.2.log, ...
    跨天切换: 关闭旧文件, 下次写入自动创建 app_2026-04-07.log
    自动清理: 删除超过 backup_days 天的日志文件
    """

    def __init__(
        self,
        filename: str,
        maxBytes: int = 10 * 1024 * 1024,
        backup_days: int = 7,
        **kwargs,
    ):
        self.base_dir = os.path.dirname(filename) or "."
        self.base_name, self.base_ext = os.path.splitext(os.path.basename(filename))
        self.backup_days = backup_days
        self._current_date = time.strftime("%Y-%m-%d")
        self._last_cleanup_date = self._current_date

        super().__init__(
            self._build_path(self._current_date),
            maxBytes=maxBytes,
            backupCount=0,
            **kwargs,
        )

    def _build_path(self, date_str: str) -> str:
        """构建日志文件路径: {dir}/{name}_{date}{ext}"""
        return os.path.join(
            self.base_dir, f"{self.base_name}_{date_str}{self.base_ext}"
        )

    def _date_changed(self) -> bool:
        """检查日期是否已变更"""
        today = time.strftime("%Y-%m-%d")
        if today != self._current_date:
            self._current_date = today
            return True
        return False

    def shouldRollover(self, record):
        if self.stream is None:
            self.stream = self._open()
        if self._date_changed():
            return True
        if self.maxBytes > 0 and self.stream.tell() >= self.maxBytes:
            return True
        return False

    def doRollover(self):
        if self._date_changed():
            self._do_time_rollover()
        else:
            self._do_size_rollover()

    def _do_size_rollover(self):
        """按序号轮转: app_2026-04-06.2 ← app_2026-04-06.1 ← app_2026-04-06.log"""
        if self.stream:
            self.stream.close()
            self.stream = None

        date_str = self._current_date
        n = 0
        while os.path.exists(f"{self._build_path(date_str)}.{n + 1}"):
            n += 1
        while n >= 1:
            os.rename(
                f"{self._build_path(date_str)}.{n}",
                f"{self._build_path(date_str)}.{n + 1}",
            )
            n -= 1
        if os.path.exists(self._build_path(date_str)):
            os.rename(self._build_path(date_str), f"{self._build_path(date_str)}.1")

        self.baseFilename = self._build_path(date_str)
        self.stream = self._open()

    def _do_time_rollover(self):
        """跨天切换: 关闭旧文件, 更新路径, 下次写入自动创建新文件"""
        if self.stream:
            self.stream.close()
            self.stream = None
        self.baseFilename = self._build_path(self._current_date)
        self._cleanup_expired()

    def _cleanup_expired(self):
        """删除超过 backup_days 天的日志文件"""
        cleanup_logs(self.base_dir, self.base_name, self.backup_days)

    def emit(self, record):
        try:
            today = time.strftime("%Y-%m-%d")
            if today != self._last_cleanup_date:
                self._last_cleanup_date = today
                self._cleanup_expired()
            if self.shouldRollover(record):
                self.doRollover()
            logging.FileHandler.emit(self, record)
        except Exception:
            self.handleError(record)


def get_uvicorn_log_config() -> dict:
    """
    获取 uvicorn 日志配置

    Returns:
        dict: uvicorn 日志配置字典
    """
    log_config = deepcopy(LOGGING_CONFIG)

    log_config["formatters"]["default"]["fmt"] = settings.uvicorn_log_format
    log_config["formatters"]["default"]["datefmt"] = settings.log_date_format
    log_config["formatters"]["access"]["fmt"] = settings.uvicorn_access_log_format
    log_config["formatters"]["access"]["datefmt"] = settings.log_date_format

    log_config["formatters"]["file_default"] = {
        "format": settings.uvicorn_log_format,
        "datefmt": settings.log_date_format,
    }
    log_config["formatters"]["file_access"] = {
        "()": "uvicorn.logging.AccessFormatter",
        "fmt": settings.uvicorn_access_log_format,
        "datefmt": settings.log_date_format,
        "use_colors": False,
    }

    log_path = BASE_DIR / settings.log_dir
    log_path.mkdir(parents=True, exist_ok=True)

    log_file_path = str(log_path / "app.log")

    log_config["handlers"]["file_default"] = {
        "()": "app.config.logging_config.SizedTimedRotatingFileHandler",
        "formatter": "file_default",
        "filename": log_file_path,
        "maxBytes": settings.log_max_bytes,
        "backup_days": settings.log_backup_days,
        "encoding": "utf-8",
    }

    log_config["handlers"]["file_access"] = {
        "()": "app.config.logging_config.SizedTimedRotatingFileHandler",
        "formatter": "file_access",
        "filename": log_file_path,
        "maxBytes": settings.log_max_bytes,
        "backup_days": settings.log_backup_days,
        "encoding": "utf-8",
    }

    log_config["loggers"]["uvicorn"]["handlers"].append("file_default")
    log_config["loggers"]["uvicorn.access"]["handlers"].append("file_access")

    log_config["handlers"]["console_default"] = {
        "class": "logging.StreamHandler",
        "formatter": "default",
        "stream": "ext://sys.stdout",
    }

    log_config["handlers"]["console_access"] = {
        "class": "logging.StreamHandler",
        "formatter": "access",
        "stream": "ext://sys.stdout",
    }

    # 过滤视频 206 Range 请求日志
    log_config["filters"] = {
        "video_range": {
            "()": "app.config.logging_config.VideoRangeFilter",
        },
    }
    for handler_key in ("access", "console_access", "file_access"):
        if handler_key in log_config["handlers"]:
            log_config["handlers"][handler_key]["filters"] = ["video_range"]

    # 抑制 asyncio socket.send() 警告
    log_config["loggers"]["asyncio"] = {
        "level": "ERROR",
        "handlers": ["console_default", "file_default"],
        "propagate": False,
    }

    log_config["root"] = {
        "level": "INFO",
        "handlers": ["console_default", "file_default"],
    }

    return log_config

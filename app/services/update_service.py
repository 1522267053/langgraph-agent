"""
自动更新服务

负责版本检查、后台下载、状态管理与更新触发。
更新流程: 检查新版本 → 后台下载整包 → CRC 校验 → 触发 updater 替换重启。

状态机: idle → downloading → ready → applying → (done|failed|rolled_back)
跨进程持久化: status.json（运行状态）+ result.json（updater 写入的最终结果）
"""

import asyncio
import hashlib
import json
import logging
import os
import zipfile
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx

from app.config.build_utils import (
    BASE_DIR,
    IS_PACKAGED,
    IS_WINDOWS,
    get_update_cache_dir,
)
from app.config.settings import settings
from app.config.version import __version__, is_newer
from app.services.global_config_service import global_config_service

logger = logging.getLogger(__name__)


class UpdateState(str, Enum):
    IDLE = "idle"
    DOWNLOADING = "downloading"
    READY = "ready"
    APPLYING = "applying"
    DONE = "done"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


def _no_update_result() -> dict:
    return {
        "has_update": False,
        "current_version": __version__,
        "latest_version": __version__,
        "platform": "",
        "release_notes": "",
        "download_url": "",
        "file_size": 0,
        "sha256": "",
        "published_at": "",
        "force_upgrade": False,
    }


class UpdateService:
    def __init__(self):
        self._state: str = UpdateState.IDLE.value
        self._version: str = ""
        self._progress: int = 0
        self._error: str = ""
        self._sha256: str = ""
        self._file_size: int = 0
        self._download_task: Optional[asyncio.Task] = None
        self._download_lock = asyncio.Lock()

        self._latest_info: dict = _no_update_result()
        self._last_result: Optional[dict] = None

        self._restore_from_disk()

    # ---- 版本检查 ----

    async def fetch_latest_version(self) -> dict:
        """远程拉取最新版本信息（按当前平台过滤，每次实时请求）

        成功取到远程响应（含"明确无更新"）时同步刷新 _latest_info 缓存，
        使后续 get_status() 反映最新检查结果；网络异常时保留原缓存，
        避免瞬时抖动清掉已知的强制升级提示。
        """
        check_url = settings.version_check_url
        if not check_url:
            self._latest_info = _no_update_result()
            return self._latest_info

        try:
            from app.config.database import AsyncSessionLocal

            platform_tag = "windows" if IS_WINDOWS else "linux"
            sep = "&" if "?" in check_url else "?"
            request_url = f"{check_url}{sep}platform={platform_tag}"

            async with AsyncSessionLocal() as db:
                await global_config_service.ensure_marketplace_cache(db)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(request_url)
                resp.raise_for_status()
            body = resp.json()
            remote = body.get("data")
            if not remote:
                self._latest_info = _no_update_result()
                return self._latest_info
            latest = remote.get("version", "")
            has_update = bool(latest and is_newer(latest, __version__))
            download_url = remote.get("download_url", "")
            if download_url and not download_url.startswith("http"):
                from urllib.parse import urlparse

                parsed = urlparse(check_url)
                download_url = f"{parsed.scheme}://{parsed.netloc}{download_url}"
                token = global_config_service.marketplace_token
                if token:
                    qsep = "&" if "?" in download_url else "?"
                    download_url = f"{download_url}{qsep}token={token}"
            info = {
                "has_update": has_update,
                "current_version": __version__,
                "latest_version": latest or __version__,
                "platform": remote.get("platform", ""),
                "release_notes": remote.get("release_notes", ""),
                "download_url": download_url,
                "file_size": remote.get("file_size", 0),
                "sha256": remote.get("sha256", ""),
                "published_at": remote.get("published_at", ""),
                "force_upgrade": bool(remote.get("force_upgrade", False)),
            }
            self._latest_info = info
            return info
        except Exception as e:
            logger.warning("检查更新失败: %s", e)
            return _no_update_result()

    # ---- 下载 ----

    async def check_and_download(self) -> dict:
        """检查新版本，若有则启动后台下载"""
        info = await self.fetch_latest_version()
        self._latest_info = info
        if not info.get("has_update") or not info.get("download_url"):
            return self.get_status()

        latest = info["latest_version"]
        if (
            self._state in (UpdateState.DOWNLOADING.value, UpdateState.READY.value)
            and self._version == latest
        ):
            return self.get_status()

        await self._start_download(
            latest,
            info["download_url"],
            info.get("sha256", ""),
            info.get("file_size", 0),
        )
        return self.get_status()

    async def _start_download(
        self, version: str, url: str, sha256: str = "", file_size: int = 0
    ) -> None:
        async with self._download_lock:
            if self._download_task and not self._download_task.done():
                self._download_task.cancel()
                try:
                    await self._download_task
                except (asyncio.CancelledError, Exception):
                    pass
            self._version = version
            self._progress = 0
            self._error = ""
            self._sha256 = sha256
            self._file_size = file_size
            self._set_state(UpdateState.DOWNLOADING)
            self._download_task = asyncio.create_task(self._do_download(url))

    async def _do_download(self, url: str) -> None:
        cache_dir = get_update_cache_dir()
        zip_path = cache_dir / f"download_{self._version}.zip"
        tmp_path = cache_dir / f"download_{self._version}.zip.tmp"
        try:
            timeout = httpx.Timeout(10.0, read=120.0)
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True
            ) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0))
                    downloaded = 0
                    with open(tmp_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=256 * 1024):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total > 0:
                                self._progress = min(99, int(downloaded * 100 / total))

            # SHA256 校验（市场端提供时强制校验，防篡改/防损坏）
            if self._sha256:
                h = hashlib.sha256()
                with open(tmp_path, "rb") as f:
                    for block in iter(lambda: f.read(1024 * 1024), b""):
                        h.update(block)
                if h.hexdigest().lower() != self._sha256.lower():
                    raise ValueError("SHA256 校验失败，文件可能已损坏或被篡改")
            # 压缩包完整性校验
            with zipfile.ZipFile(tmp_path) as zf:
                bad = zf.testzip()
                if bad is not None:
                    raise ValueError(f"压缩包损坏: {bad}")
            if total > 0 and tmp_path.stat().st_size != total:
                raise ValueError("下载大小与 Content-Length 不匹配")

            tmp_path.replace(zip_path)
            self._progress = 100
            self._set_state(UpdateState.READY)
            logger.info("更新包 %s 下载完成", self._version)
        except asyncio.CancelledError:
            self._progress = 0
            self._set_state(UpdateState.IDLE)
            tmp_path.unlink(missing_ok=True)
            raise
        except Exception as e:
            logger.exception("下载更新包失败")
            self._error = str(e)
            self._set_state(UpdateState.FAILED)
            tmp_path.unlink(missing_ok=True)

    async def cancel_download(self) -> dict:
        if self._download_task and not self._download_task.done():
            self._download_task.cancel()
            try:
                await self._download_task
            except (asyncio.CancelledError, Exception):
                pass
        self._progress = 0
        self._error = ""
        self._set_state(UpdateState.IDLE)
        return self.get_status()

    # ---- 触发更新 ----

    async def apply_update(self) -> dict:
        if not IS_PACKAGED:
            return {"error": "仅打包环境支持自动更新，请手动下载升级"}
        if self._state != UpdateState.READY.value:
            return {"error": "更新包未就绪"}

        zip_path = get_update_cache_dir() / f"download_{self._version}.zip"
        if not zip_path.exists():
            self._error = "更新包文件丢失"
            self._set_state(UpdateState.FAILED)
            return {"error": self._error}

        ext = ".exe" if IS_WINDOWS else ""
        updater_path = BASE_DIR / f"updater{ext}"
        if not updater_path.exists():
            self._error = "updater 未找到，无法自动更新"
            self._set_state(UpdateState.FAILED)
            return {"error": self._error}

        self._set_state(UpdateState.APPLYING)
        self._launch_updater(str(updater_path), str(zip_path))
        asyncio.create_task(self._delayed_exit())
        return {"started": True}

    def _launch_updater(self, updater_path: str, zip_path: str) -> None:
        import subprocess

        cmd = [
            updater_path,
            str(os.getpid()),
            zip_path,
            str(BASE_DIR),
            str(settings.app_port),
            str(settings.update_health_check_timeout),
        ]
        if IS_WINDOWS:
            subprocess.Popen(
                cmd,
                creationflags=subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
        else:
            subprocess.Popen(cmd, start_new_session=True, close_fds=True)
        logger.info("updater 已启动: %s", updater_path)

    async def _delayed_exit(self) -> None:
        """延迟退出主进程，让 apply 响应先返回前端"""
        await asyncio.sleep(1.5)
        logger.info("主进程退出，交由 updater 接管")
        os._exit(0)

    # ---- 状态查询 ----

    def get_status(self) -> dict:
        return {
            "state": self._state,
            "version": self._version,
            "progress": self._progress,
            "error": self._error,
            "current_version": __version__,
            "latest_version": self._latest_info.get("latest_version", __version__),
            "has_update": self._latest_info.get("has_update", False),
            "download_url": self._latest_info.get("download_url", ""),
            "file_size": self._latest_info.get("file_size", 0),
            "release_notes": self._latest_info.get("release_notes", ""),
            "force_upgrade": self._latest_info.get("force_upgrade", False),
            "published_at": self._latest_info.get("published_at", ""),
            "last_result": self._last_result,
        }

    def clear_last_result(self) -> None:
        """前端展示完上次更新结果后调用，避免重复提示"""
        self._last_result = None

    # ---- 持久化 ----

    @property
    def _status_file(self) -> Path:
        return get_update_cache_dir() / "status.json"

    @property
    def _result_file(self) -> Path:
        return get_update_cache_dir() / "result.json"

    def _set_state(self, state: UpdateState) -> None:
        self._state = state.value
        self._persist_status()

    def _persist_status(self) -> None:
        data = {
            "state": self._state,
            "version": self._version,
            "progress": self._progress,
            "error": self._error,
        }
        try:
            self._status_file.write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            logger.debug("写入 status.json 失败", exc_info=True)

    def _restore_from_disk(self) -> None:
        """启动时从磁盘恢复状态"""
        try:
            if self._result_file.exists():
                self._last_result = json.loads(
                    self._result_file.read_text(encoding="utf-8")
                )
                self._result_file.unlink(missing_ok=True)
                self._cleanup_download_files()
                self._status_file.unlink(missing_ok=True)
                return
        except Exception:
            logger.debug("读取 result.json 失败", exc_info=True)

        try:
            if self._status_file.exists():
                data = json.loads(self._status_file.read_text(encoding="utf-8"))
                if data.get("state") == UpdateState.APPLYING.value:
                    self._state = UpdateState.FAILED.value
                    self._error = "上次更新被中断，请重新检查更新"
                    self._last_result = {
                        "result": "interrupted",
                        "error": "更新过程被中断",
                    }
                    self._persist_status()
                else:
                    self._state = data.get("state", UpdateState.IDLE.value)
                    self._version = data.get("version", "")
                    self._progress = data.get("progress", 0)
                    self._error = data.get("error", "")
        except Exception:
            logger.debug("读取 status.json 失败", exc_info=True)

    def _cleanup_download_files(self) -> None:
        """清理下载的 zip 包（更新完成后调用）"""
        try:
            for p in get_update_cache_dir().glob("download_*.zip*"):
                p.unlink(missing_ok=True)
        except Exception:
            logger.debug("清理下载包失败", exc_info=True)


update_service = UpdateService()

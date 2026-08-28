"""
自动更新服务

负责版本检查、后台下载、状态管理与更新触发。
更新流程: 检查新版本 → 后台下载整包 → CRC 校验 → 触发 updater 替换重启。
更新失败/中断时保留通过校验的更新包，重启后恢复 ready 态，重试无需重新下载。

状态机: idle → downloading → ready → applying → (done|failed|rolled_back)
跨进程持久化: status.json（运行状态 + 版本元数据）+ result.json（updater 写入的最终结果）
"""

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
import zipfile
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx

from app.config.build_utils import (
    BASE_DIR,
    IS_LINUX,
    IS_PACKAGED,
    IS_WINDOWS,
    get_update_cache_dir,
)
from app.config.settings import settings
from app.config.version import get_version, is_newer
from app.services.global_config_service import global_config_service

logger = logging.getLogger(__name__)


def _is_under_systemd() -> bool:
    """检测当前进程是否由 systemd 作为 service 管理。

    systemd 默认 KillMode=control-group 会在主进程退出时连带终止同 cgroup 的 updater，
    且 Restart= 策略会抢占端口，导致自动更新流程不可靠。检测到该环境时引导手动更新。
    """
    if not IS_LINUX:
        return False
    # systemd 拉起 service 时必设 INVOCATION_ID（最可靠标志），nohup/前台运行不会有
    if os.environ.get("INVOCATION_ID"):
        return True
    # 兜底：cgroup 路径含 systemd unit 标识
    try:
        cgroup = Path("/proc/self/cgroup").read_text(encoding="utf-8")
        if ".service" in cgroup or ".slice" in cgroup:
            return True
    except OSError:
        pass
    return False


class UpdateState(str, Enum):
    IDLE = "idle"
    DOWNLOADING = "downloading"
    READY = "ready"
    APPLYING = "applying"
    DONE = "done"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class _TokenExpiredError(Exception):
    """下载更新包时服务端返回 401（token 已失效）"""


def _no_update_result() -> dict:
    current = get_version()
    return {
        "has_update": False,
        "current_version": current,
        "latest_version": current,
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
        self._http_client: Optional[httpx.AsyncClient] = None

        self._latest_info: dict = _no_update_result()
        self._last_result: Optional[dict] = None
        self._pending_result: bool = False
        self._resolve_task: Optional[asyncio.Task] = None
        self._pending_download_resume: bool = False
        self._resume_task: Optional[asyncio.Task] = None

        self._restore_from_disk()

    def initialize_http_client(self) -> None:
        """启动期创建并复用客户端，避免请求中同步加载 CA 证书阻塞事件循环。"""
        if self._http_client is not None and not self._http_client.is_closed:
            return
        ssl_context = httpx.create_ssl_context()
        self._http_client = httpx.AsyncClient(timeout=10, verify=ssl_context)

    async def close_http_client(self) -> None:
        """关闭更新检查使用的持久 HTTP 客户端。"""
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
        self._http_client = None

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self.initialize_http_client()
        assert self._http_client is not None
        return self._http_client

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
                from app.services.marketplace_service import marketplace_service

                # 过期自动重登（connect 含注册兜底），避免把过期 token 拼进下载地址
                token = await marketplace_service.ensure_token(db)
            resp = await self._get_http_client().get(request_url)
            resp.raise_for_status()
            body = resp.json()
            remote = body.get("data")
            if not remote:
                self._latest_info = _no_update_result()
                return self._latest_info
            latest = remote.get("version", "")
            current = get_version()
            has_update = bool(latest and is_newer(latest, current))
            download_url = remote.get("download_url", "")
            if download_url and not download_url.startswith("http"):
                from urllib.parse import urlparse

                parsed = urlparse(check_url)
                download_url = f"{parsed.scheme}://{parsed.netloc}{download_url}"
                if token:
                    qsep = "&" if "?" in download_url else "?"
                    download_url = f"{download_url}{qsep}token={token}"
            info = {
                "has_update": has_update,
                "current_version": current,
                "latest_version": latest or current,
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
        download_alive = (
            self._download_task is not None and not self._download_task.done()
        )
        if (
            self._state in (UpdateState.DOWNLOADING.value, UpdateState.READY.value)
            and self._version == latest
        ):
            if self._state == UpdateState.DOWNLOADING.value and not download_alive:
                # 孤儿 downloading 态（进程重启恢复后无下载任务）：重新拉起下载
                await self._start_download(
                    latest,
                    info["download_url"],
                    info.get("sha256", ""),
                    info.get("file_size", 0),
                )
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
            self._download_task = asyncio.create_task(
                self._do_download_with_relogin(url)
            )

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
                    if resp.status_code == 401:
                        raise _TokenExpiredError("下载凭证已过期(HTTP 401)")
                    resp.raise_for_status()
                    content_length = int(resp.headers.get("content-length", 0))
                    # 服务端 chunked 传输无 Content-Length 时，退回市场元数据大小计算进度
                    total = content_length or self._file_size or 0
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
            # 大小校验只认响应头 Content-Length（元数据大小仅供参考，不作硬校验）
            if content_length > 0 and tmp_path.stat().st_size != content_length:
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
        except _TokenExpiredError:
            # 不在此处置 FAILED：保持 DOWNLOADING，交给 _do_download_with_relogin 重登重试
            tmp_path.unlink(missing_ok=True)
            raise
        except Exception as e:
            logger.exception("下载更新包失败")
            self._error = str(e)
            self._set_state(UpdateState.FAILED)
            tmp_path.unlink(missing_ok=True)

    async def _do_download_with_relogin(self, url: str) -> None:
        """下载更新包，收到 401（token 过期）时重新登录市场并重试一次"""
        try:
            await self._do_download(url)
        except asyncio.CancelledError:
            raise
        except _TokenExpiredError:
            new_url = await self._refresh_download_url()
            if not new_url:
                return
            try:
                await self._do_download(new_url)
            except _TokenExpiredError:
                # 重登后重试仍 401：凭证彻底失效，置 FAILED 收敛状态
                self._error = "重新登录后重试仍返回 401，请检查市场账号状态"
                self._set_state(UpdateState.FAILED)

    async def _refresh_download_url(self) -> Optional[str]:
        """401 后强制重登市场并重建带新 token 的下载地址；失败置 FAILED 返回 None"""
        try:
            from app.config.database import AsyncSessionLocal
            from app.services.marketplace_service import marketplace_service

            async with AsyncSessionLocal() as db:
                await marketplace_service.clear_token(db)
                token = await marketplace_service.connect(db)
            if not token:
                reason = marketplace_service.get_connect_error_msg() or "未知原因"
                logger.warning("更新下载 401 后重新登录市场失败: %s", reason)
                self._error = f"下载凭证过期且重新登录失败: {reason}"
                self._set_state(UpdateState.FAILED)
                return None
        except Exception as e:
            logger.warning("更新下载 401 后重登流程异常: %s", e)
            self._error = f"下载凭证过期，重新登录市场异常: {e}"
            self._set_state(UpdateState.FAILED)
            return None

        info = await self.fetch_latest_version()
        self._latest_info = info
        new_url = info.get("download_url", "")
        if not info.get("has_update") or not new_url:
            self._error = "重新登录后未获取到有效下载地址"
            self._set_state(UpdateState.FAILED)
            return None
        logger.info("市场重登成功，已刷新下载地址重试更新包下载")
        return new_url

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

        current = get_version()
        if not is_newer(self._version, current):
            obsolete_version = self._version or "未知"
            self._discard_obsolete_update(current)
            return {
                "error": (
                    f"更新包版本 v{obsolete_version} 不高于当前版本 v{current}，"
                    "已清理过期更新包"
                )
            }

        zip_path = get_update_cache_dir() / f"download_{self._version}.zip"
        if not zip_path.exists():
            self._error = "更新包文件丢失"
            self._set_state(UpdateState.FAILED)
            return {"error": self._error}

        # systemd 托管环境下自动替换会与进程管理冲突（updater 被连坐终止 + 端口抢占），
        # 引导手动更新，状态保持 READY 不污染
        if _is_under_systemd():
            return {
                "manual_update": True,
                "package_path": str(zip_path),
                "message": (
                    "检测到服务由 systemd 托管，自动重启替换会与进程管理冲突。"
                    "请手动更新：停止服务(systemctl stop) → 解压替换程序文件 → 启动服务(systemctl start)"
                ),
            }

        ext = ".exe" if IS_WINDOWS else ""
        updater_path = BASE_DIR / f"updater{ext}"
        if not updater_path.exists():
            self._error = "updater 未找到，无法自动更新"
            self._set_state(UpdateState.FAILED)
            return {"error": self._error}

        self._set_state(UpdateState.APPLYING)
        self._launch_updater(str(updater_path), str(zip_path))
        threading.Thread(target=self._delayed_exit, daemon=True).start()
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

    def _delayed_exit(self) -> None:
        """延迟退出主进程（daemon 线程），让 apply 响应先返回前端

        用独立线程而非 asyncio.create_task，避免未保存引用的 task 被 GC
        导致协程不执行（Linux 打包环境曾因此主进程不退出）。
        """
        time.sleep(1.5)
        logger.info("主进程退出，交由 updater 接管")
        os._exit(0)

    # ---- 状态查询 ----

    def get_status(self) -> dict:
        current = get_version()
        self._discard_obsolete_update(current)
        return {
            "state": self._state,
            "version": self._version,
            "progress": self._progress,
            "error": self._error,
            "current_version": current,
            "latest_version": self._latest_info.get("latest_version", current),
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
            "sha256": self._sha256,
            "file_size": self._file_size,
            "latest_info": self._latest_info,
        }
        try:
            self._status_file.write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            logger.debug("写入 status.json 失败", exc_info=True)

    def _restore_from_disk(self) -> None:
        """启动时从磁盘恢复状态"""
        result = self._consume_result_file()
        if result is not None:
            self._last_result = result
        try:
            if self._status_file.exists():
                data = json.loads(self._status_file.read_text(encoding="utf-8"))
                self._state = data.get("state", UpdateState.IDLE.value)
                self._version = data.get("version", "")
                self._progress = data.get("progress", 0)
                self._error = data.get("error", "")
                self._sha256 = data.get("sha256", "")
                self._file_size = data.get("file_size", 0)
                self._latest_info = data.get("latest_info") or _no_update_result()
        except Exception:
            logger.debug("读取 status.json 失败", exc_info=True)

        if self._state == UpdateState.APPLYING.value and result is None:
            # 更新正在进行：正常流程中新版主进程先于 updater 的健康检查
            # 启动（result.json 在健康检查通过后才写入），此处不能立即判定
            # 中断，保持 applying 态，由 lifespan 拉起后台轮询等待最终结果
            self._pending_result = True
            return

        if result is not None:
            self._resolve_update_result(result)
            return

        if self._state == UpdateState.DOWNLOADING.value:
            # 上次进程在下载中退出：下载任务随进程消失，状态是孤儿态。
            # 标记待恢复，由 lifespan 在事件循环就绪后重新拉起下载
            self._pending_download_resume = True
            return

        self._discard_obsolete_update(get_version())

    def _discard_obsolete_update(self, current: str) -> bool:
        """清理目标版本不高于当前版本的已就绪更新包。"""
        if self._state != UpdateState.READY.value or is_newer(self._version, current):
            return False

        obsolete_version = self._version or "未知"
        self._version = ""
        self._progress = 0
        self._error = ""
        self._sha256 = ""
        self._file_size = 0
        self._latest_info = _no_update_result()
        self._set_state(UpdateState.IDLE)
        self._cleanup_download_files()
        logger.info(
            "已清理过期更新包: target=%s, current=%s",
            obsolete_version,
            current,
        )
        return True

    def _consume_result_file(self) -> Optional[dict]:
        """读取并清理 updater 写入的 result.json，无文件或读取失败返回 None"""
        try:
            if self._result_file.exists():
                data = json.loads(self._result_file.read_text(encoding="utf-8"))
                self._result_file.unlink(missing_ok=True)
                return data
        except Exception:
            logger.debug("读取 result.json 失败", exc_info=True)
        return None

    def _resolve_update_result(self, result: dict) -> None:
        """根据 updater 的最终结果收敛状态。

        成功：清空更新状态并清理下载包；
        失败/回滚/中断：尝试复用已下载的更新包恢复 READY（重试免重新下载），
        包缺失或校验不通过时置 FAILED 引导重新下载。
        """
        if result.get("result") == "success":
            self._latest_info = _no_update_result()
            self._version = ""
            self._progress = 0
            self._error = ""
            self._sha256 = ""
            self._file_size = 0
            self._set_state(UpdateState.IDLE)
            self._cleanup_download_files()
            self._status_file.unlink(missing_ok=True)
            return
        self._error = result.get("error", "")
        self._recover_download_package()

    def _recover_download_package(self) -> None:
        """更新失败后尝试复用已下载的更新包，免去重新下载。

        包存在且校验通过时恢复 READY，重试入口（横幅/设置页）直接用本地包重试；
        包缺失、版本过期或校验不通过时清理残留并置 FAILED，引导重新下载。
        """
        zip_path = self._find_cached_zip()
        if zip_path is None:
            self._set_state(UpdateState.FAILED)
            return
        version = zip_path.stem.removeprefix("download_")
        if not version or not is_newer(version, get_version()):
            self._cleanup_download_files()
            self._error = "更新包已过期，请重新检查更新"
            self._set_state(UpdateState.FAILED)
            return
        if not self._validate_cached_zip(zip_path):
            self._cleanup_download_files()
            self._error = "更新包校验失败，已清理，请重新下载"
            self._set_state(UpdateState.FAILED)
            return
        self._version = version
        self._progress = 100
        self._error = ""
        self._set_state(UpdateState.READY)
        logger.info("更新包 v%s 校验通过，恢复 ready 态可直接重试更新", version)

    def _find_cached_zip(self) -> Optional[Path]:
        """定位可复用的更新包：优先目标版本文件，其次按修改时间取最新"""
        cache_dir = get_update_cache_dir()
        if self._version:
            expected = cache_dir / f"download_{self._version}.zip"
            if expected.exists():
                return expected
        candidates = [
            p for p in cache_dir.glob("download_*.zip") if p.stem != "download_"
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.stat().st_mtime)

    def _validate_cached_zip(self, zip_path: Path) -> bool:
        """校验更新包完整性：已知 SHA256 时严格比对（防篡改/防损坏），否则退回 zip 校验"""
        try:
            if self._sha256:
                h = hashlib.sha256()
                with open(zip_path, "rb") as f:
                    for block in iter(lambda: f.read(1024 * 1024), b""):
                        h.update(block)
                if h.hexdigest().lower() != self._sha256.lower():
                    logger.warning("更新包 SHA256 校验失败: %s", zip_path)
                    return False
                return True
            with zipfile.ZipFile(zip_path) as zf:
                return zf.testzip() is None
        except Exception:
            logger.warning("更新包校验异常: %s", zip_path, exc_info=True)
            return False

    def start_pending_result_resolver(self) -> None:
        """由 lifespan 在事件循环就绪后调用，拉起更新结果轮询（持有强引用防 GC）"""
        if not self._pending_result or self._resolve_task:
            return
        self._resolve_task = asyncio.create_task(self.resolve_pending_result())

    def start_pending_download_resume(self) -> None:
        """由 lifespan 在事件循环就绪后调用，恢复进程重启前未完成的下载"""
        if not self._pending_download_resume or self._resume_task:
            return
        self._resume_task = asyncio.create_task(self._resume_pending_download())

    async def _resume_pending_download(self) -> None:
        """用持久化的版本元数据重新拉起中断的下载。

        __init__ 阶段无事件循环，无法创建下载任务，故延迟到 lifespan 阶段执行。
        元数据缺失（老版 status.json）或版本已过期时回退 idle，等待下次检查更新。
        """
        self._pending_download_resume = False
        info = self._latest_info
        url = info.get("download_url", "")
        version = self._version
        if (
            not url
            or not version
            or not info.get("has_update")
            or not is_newer(version, get_version())
        ):
            self._progress = 0
            self._set_state(UpdateState.IDLE)
            return
        logger.info("恢复中断的更新包下载: v%s", version)
        await self._start_download(
            version, url, info.get("sha256", ""), info.get("file_size", 0)
        )

    async def resolve_pending_result(self) -> None:
        """后台轮询 updater 的最终更新结果

        新版主进程先于 updater 完成（result.json 在健康检查通过后才写入），
        启动时无法同步读取。轮询超时（健康检查超时 + 30s 余量）仍无结果才
        判定更新被中断，覆盖 updater 异常死亡的场景。
        """
        deadline = time.monotonic() + settings.update_health_check_timeout + 30
        while time.monotonic() < deadline:
            result = self._consume_result_file()
            if result is not None:
                self._last_result = result
                self._resolve_update_result(result)
                return
            await asyncio.sleep(2)
        self._last_result = {"result": "interrupted", "error": "更新过程被中断"}
        self._resolve_update_result(self._last_result)

    def _cleanup_download_files(self) -> None:
        """清理下载的 zip 包（更新完成后调用）"""
        try:
            for p in get_update_cache_dir().glob("download_*.zip*"):
                p.unlink(missing_ok=True)
        except Exception:
            logger.debug("清理下载包失败", exc_info=True)


update_service = UpdateService()

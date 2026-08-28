"""
SSH 远程执行节点处理器（LLM 工具型，同 Shell 节点模式）

无入图执行路径，仅通过工具边连接 LLM，提供 4 个工具：
- ssh_executor: 远程命令执行
- ssh_upload / ssh_download: SFTP 文件传输
- ssh_list_dir: 远程目录列表

连接策略：每次工具调用独立建立 SSH 连接（无状态），用完即关；
paramiko 为同步实现，统一经 asyncio.to_thread 包装，避免阻塞事件循环。
"""

import asyncio
import json
import logging
import posixpath
import stat as stat_module
import time
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Callable, Optional, Sequence

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import StreamWriter
from paramiko import (
    AutoAddPolicy,
    AuthenticationException,
    ECDSAKey,
    Ed25519Key,
    PKey,
    RSAKey,
    SFTPClient,
    SSHClient,
    SSHException,
)
from pydantic import BaseModel, Field

from app.agent_flow.execution_context import get_execution_context
from app.agent_flow.flow_context import FlowState
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.node_handlers.base_handler import (
    BaseNodeConfig,
    BaseNodeHandler,
    NodeVariable,
)
from app.agent_flow.tool_output_truncate import smart_truncate_output
from app.agent_flow.tools.common import (
    MAX_FILE_SIZE,
    validate_file_path,
    validate_writable_path,
)
from app.config.build_utils import get_agent_work_dir, get_temp_dir
from app.models.flow_node import FlowNode

logger = logging.getLogger(__name__)

# 单次列目录返回的最大条目数
_MAX_LIST_ENTRIES = 500

# open_session 建会话超时（秒），与默认连接超时同量级
_SESSION_OPEN_TIMEOUT = 15


class SshNodeConfig(BaseNodeConfig):
    """SSH 节点配置"""

    host: str = Field("", description="远程主机地址（IP 或域名）")
    port: int = Field(22, description="SSH 端口", ge=1, le=65535)
    username: str = Field("", description="登录用户名")
    auth_type: str = Field(
        "password",
        description="认证方式: password（密码认证）或 private_key（私钥认证）",
    )
    password: str = Field("", description="登录密码（auth_type=password 时必填）")
    private_key: str = Field(
        "",
        description="私钥 PEM 内容（auth_type=private_key 时与 private_key_path 二选一，优先使用本字段）",
    )
    private_key_path: str = Field(
        "", description="私钥文件路径（本机绝对路径），未提供私钥内容时使用"
    )
    passphrase: str = Field("", description="私钥口令，无私钥口令时留空")
    connect_timeout: int = Field(10, description="SSH 连接超时秒数", ge=1, le=120)
    command_timeout: int = Field(300, description="默认命令执行超时秒数", ge=1, le=3600)
    max_transfer_mb: int = Field(
        50, description="SFTP 单文件传输大小上限（MB）", ge=1, le=2048
    )
    output_variables: list[NodeVariable] = [
        NodeVariable(name="stdout", type="string"),
        NodeVariable(name="stderr", type="string"),
        NodeVariable(name="exit_code", type="number"),
    ]


# ---- 工具入参模型 ----


class SshExecutorInput(BaseModel):
    """ssh_executor 输入参数"""

    command: str = Field(
        ...,
        description=(
            "要执行的远程命令，在远端用户默认 Shell 中运行；"
            "多行脚本、管道、重定向均可，多条命令用 && 连接"
        ),
    )
    timeout: Optional[int] = Field(
        None,
        ge=1,
        le=3600,
        description="本次命令超时秒数，不传则使用节点配置的默认值",
    )


class SshUploadInput(BaseModel):
    """ssh_upload 输入参数"""

    local_path: str = Field(
        ..., description="本地文件路径（相对路径基于当前工作目录解析）"
    )
    remote_path: str = Field(
        ...,
        description="远程目标绝对路径（POSIX 风格，如 /home/user/app.jar）；父目录不存在时自动创建",
    )


class SshDownloadInput(BaseModel):
    """ssh_download 输入参数"""

    remote_path: str = Field(..., description="远程文件绝对路径（POSIX 风格）")
    local_path: Optional[str] = Field(
        None,
        description="本地保存路径，不传则保存到当前工作目录并保留原文件名",
    )


class SshListDirInput(BaseModel):
    """ssh_list_dir 输入参数"""

    remote_dir: str = Field(".", description="远程目录绝对路径，`.` 为用户主目录")


class SshSetConfigInput(BaseModel):
    """ssh_set_config 输入参数（字段级合并，未传的保留节点原配置）"""

    host: Optional[str] = Field(None, description="远程主机地址（IP 或域名）")
    port: Optional[int] = Field(None, ge=1, le=65535, description="SSH 端口")
    username: Optional[str] = Field(None, description="登录用户名")
    auth_type: Optional[str] = Field(
        None,
        description="认证方式: password 或 private_key；不传则根据提供的凭据自动推导",
    )
    password: Optional[str] = Field(None, description="登录密码（密码认证）")
    private_key: Optional[str] = Field(None, description="私钥 PEM 内容（私钥认证）")
    private_key_path: Optional[str] = Field(
        None, description="私钥文件路径（本机绝对路径），与 private_key 二选一"
    )
    passphrase: Optional[str] = Field(None, description="私钥口令，无私钥口令时留空")


# ---- 同步核心层（均在 asyncio.to_thread 中执行）----


def _decode_output(data: bytes) -> str:
    """三重解码：UTF-8 → GBK → 逐行混合解码（与 Shell 节点保持一致）"""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("gbk")
        except UnicodeDecodeError:
            lines = data.split(b"\n")
            decoded = []
            for line in lines:
                try:
                    decoded.append(line.decode("utf-8"))
                except UnicodeDecodeError:
                    decoded.append(line.decode("gbk", errors="replace"))
            return "\n".join(decoded)


def _load_private_key(cfg: SshNodeConfig) -> PKey:
    """加载私钥：优先内联 PEM 内容，其次私钥文件路径；按 RSA → Ed25519 → ECDSA 依次尝试"""
    key_classes = (RSAKey, Ed25519Key, ECDSAKey)
    passphrase = cfg.passphrase or None
    pem = (cfg.private_key or "").strip()
    if pem:
        errors = []
        for cls in key_classes:
            try:
                return cls.from_private_key(StringIO(pem), password=passphrase)
            except SSHException as e:
                errors.append(f"{cls.__name__}: {e}")
        detail = "; ".join(errors[:3])
        raise ValueError(f"私钥内容解析失败（已尝试 RSA/Ed25519/ECDSA 格式）：{detail}")

    key_path = (cfg.private_key_path or "").strip()
    if key_path:
        path = Path(key_path).expanduser()
        if not path.is_file():
            raise ValueError(f"私钥文件不存在: {path}")
        errors = []
        for cls in key_classes:
            try:
                return cls.from_private_key_file(str(path), password=passphrase)
            except SSHException as e:
                errors.append(f"{cls.__name__}: {e}")
            except OSError as e:
                raise ValueError(f"读取私钥文件失败: {e}") from e
        detail = "; ".join(errors[:3])
        raise ValueError(f"私钥文件解析失败（已尝试 RSA/Ed25519/ECDSA 格式）：{detail}")

    raise ValueError(
        "auth_type=private_key 时必须填写 private_key 内容或 private_key_path"
    )


def _validate_connection_config(cfg: SshNodeConfig) -> Optional[str]:
    """建连前的配置完整性校验，返回错误消息或 None"""
    if not (cfg.host or "").strip():
        return "host 不能为空"
    if not (cfg.username or "").strip():
        return "username 不能为空"
    if cfg.auth_type == "private_key":
        if (
            not (cfg.private_key or "").strip()
            and not (cfg.private_key_path or "").strip()
        ):
            return (
                "auth_type=private_key 时必须填写 private_key 内容或 private_key_path"
            )
    elif not (cfg.password or ""):
        return "auth_type=password 时密码不能为空"
    return None


def _open_ssh_client(cfg: SshNodeConfig) -> SSHClient:
    """建立 SSH 连接（AutoAddPolicy 免 known_hosts 交互）"""
    client = SSHClient()
    client.set_missing_host_key_policy(AutoAddPolicy())
    kwargs = {
        "hostname": cfg.host.strip(),
        "port": cfg.port,
        "username": cfg.username.strip(),
        "timeout": float(cfg.connect_timeout),
        "allow_agent": False,
        "look_for_keys": False,
    }
    if cfg.auth_type == "private_key":
        kwargs["pkey"] = _load_private_key(cfg)
    else:
        kwargs["password"] = cfg.password
    client.connect(**kwargs)
    return client


def _exec_command_sync(client: SSHClient, command: str, timeout: int) -> dict:
    """同步执行远程命令：双通道轮询复用读取 stdout/stderr，
    避免先读 stdout 再读 stderr 在任一缓冲区填满时相互阻塞死锁。

    Returns:
        {exit_code, stdout, stderr}；超时抛 TimeoutError
    """
    transport = client.get_transport()
    channel = transport.open_session(timeout=_SESSION_OPEN_TIMEOUT)
    deadline = time.monotonic() + timeout
    stdout_buf: list[bytes] = []
    stderr_buf: list[bytes] = []
    try:
        channel.exec_command(command)
        while True:
            while channel.recv_ready():
                stdout_buf.append(channel.recv(65536))
            while channel.recv_stderr_ready():
                stderr_buf.append(channel.recv_stderr(65536))
            if (
                channel.exit_status_ready()
                and not channel.recv_ready()
                and not channel.recv_stderr_ready()
            ):
                break
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"命令执行超时（{timeout} 秒），请评估任务耗时后增大 timeout 或拆分任务"
                )
            time.sleep(0.05)
        exit_code = channel.recv_exit_status()
        # 进程结束后排空残余输出
        while channel.recv_ready():
            stdout_buf.append(channel.recv(65536))
        while channel.recv_stderr_ready():
            stderr_buf.append(channel.recv_stderr(65536))
    finally:
        channel.close()

    return {
        "exit_code": exit_code,
        "stdout": _decode_output(b"".join(stdout_buf)),
        "stderr": _decode_output(b"".join(stderr_buf)),
    }


def _sftp_mkdirs(sftp: SFTPClient, remote_dir: str) -> None:
    """递归创建远程目录（已存在跳过；单级失败仅记录，交由上层报真实错误）"""
    current = ""
    for part in [p for p in remote_dir.split("/") if p]:
        current += "/" + part
        try:
            sftp.stat(current)
        except FileNotFoundError:
            try:
                sftp.mkdir(current)
            except (SSHException, OSError) as e:
                logger.warning("创建远程目录失败 %s: %s", current, e)


async def _run_with_client(
    cfg: SshNodeConfig, job: Callable[[SSHClient], dict]
) -> dict:
    """异步封装：配置校验 → to_thread 执行「建连 → 作业 → 关闭」→ 异常映射结构化错误

    Returns:
        job 结果 dict；失败时返回 {success: False, error, error_type} 结构化错误
    """
    config_err = _validate_connection_config(cfg)
    if config_err:
        return {"success": False, "error": config_err, "error_type": "invalid_config"}

    def runner():
        client = _open_ssh_client(cfg)
        try:
            return job(client)
        finally:
            client.close()

    try:
        return await asyncio.to_thread(runner)
    except TimeoutError as e:
        return {"success": False, "error": str(e), "error_type": "timeout"}
    except AuthenticationException:
        return {
            "success": False,
            "error": f"SSH 认证失败：用户名或凭据不正确（{cfg.username}@{cfg.host}:{cfg.port}）",
            "error_type": "auth_failed",
        }
    except ValueError as e:
        # 私钥解析等可预期的配置类错误
        return {"success": False, "error": str(e), "error_type": "invalid_config"}
    except (SSHException, OSError) as e:
        return {
            "success": False,
            "error": f"SSH 连接/传输失败: {e}",
            "error_type": "connection_error",
        }
    except Exception as e:
        logger.exception("SSH 工具调用异常")
        return {"success": False, "error": f"未知错误: {e}", "error_type": "unknown"}


@NodeHandlerRegistry.register("ssh")
class SshNodeHandler(BaseNodeHandler):
    """
    SSH 远程执行节点处理器

    功能：
    1. 远程命令执行（双通道流式读取，超时保护）
    2. SFTP 上传/下载（大小限制 + 本地路径安全校验）
    3. 远程目录列表
    4. 每次调用独立建连，无状态复用
    """

    ConfigClass = SshNodeConfig

    # 由 llm_tool_executor 仅对 Agent 类型注入，用作下载默认目录
    _working_dir: Optional[Path] = None

    def _resolve_working_dir(self) -> Optional[Path]:
        """解析本地默认工作目录：注入值优先，回退 Agent 工作目录（非 Agent 流程返回 None）"""
        if self._working_dir is not None:
            return self._working_dir
        ctx = get_execution_context()
        if ctx and ctx.expanded_flow is not None:
            flow_type = ctx.expanded_flow.flow_type
            flow_id = ctx.expanded_flow.id or ctx.flow_id
            if flow_type == "agent" and flow_id:
                return get_agent_work_dir(flow_id)
        return None

    def _resolve_local_source(self, raw_path: str, limit_bytes: int):
        """解析上传源文件（相对路径基于工作目录），校验安全性/存在性/大小上限

        Returns:
            (绝对路径, 错误消息)，成功时错误消息为 None
        """
        candidate = Path(raw_path.strip()).expanduser()
        if not candidate.is_absolute():
            base = self._resolve_working_dir() or Path.cwd()
            candidate = base / candidate
        try:
            candidate = candidate.resolve()
        except (OSError, RuntimeError) as e:
            return None, f"本地路径解析失败: {e}"
        is_valid, error_msg = validate_file_path(str(candidate))
        if not is_valid:
            return None, f"本地路径校验失败: {error_msg}"
        if not candidate.is_file():
            return None, f"本地文件不存在: {candidate}"
        size_mb = candidate.stat().st_size / 1024 / 1024
        if candidate.stat().st_size > limit_bytes:
            return None, (
                f"文件大小 {size_mb:.1f}MB 超过传输上限（{limit_bytes // 1024 // 1024}MB）"
            )
        return candidate, None

    def _resolve_local_target(self, raw_path: str, default_name: str):
        """解析下载目标路径（缺省目录时用工作目录/临时目录），校验可写安全性

        Returns:
            (绝对路径, 错误消息)，成功时错误消息为 None
        """
        base = self._resolve_working_dir() or Path(get_temp_dir())
        candidate = (
            Path(raw_path.strip()).expanduser() if raw_path else base / default_name
        )
        if not candidate.is_absolute():
            candidate = base / candidate
        try:
            candidate = candidate.resolve()
        except (OSError, RuntimeError) as e:
            return None, f"本地保存路径解析失败: {e}"
        is_valid, error_msg = validate_writable_path(str(candidate))
        if not is_valid:
            return None, f"本地保存路径校验失败: {error_msg}"
        return candidate, None

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        """SSH 节点不参与入图执行（仅工具提供者），直接返回状态"""
        return state

    @classmethod
    def allow_multiple_tool_connections(cls) -> bool:
        """固定工具名，不允许同一 LLM 连接多个 SSH 节点"""
        return False

    async def get_tool(self, node: FlowNode) -> Sequence[BaseTool]:
        """返回工具列表：远程命令执行 + SFTP 上传/下载 + 远程目录列表"""
        cfg: SshNodeConfig = self._get_config(node)
        transfer_limit_bytes = min(MAX_FILE_SIZE, cfg.max_transfer_mb * 1024 * 1024)

        # ---- ssh_executor ----

        async def run_remote_command(
            command: str, timeout: Optional[int] = None
        ) -> dict:
            effective_timeout = timeout or cfg.command_timeout

            def job(client: SSHClient) -> dict:
                result = _exec_command_sync(client, command, effective_timeout)
                return {"success": True, **result}

            result = await _run_with_client(cfg, job)
            if result.get("success"):
                # 统一截断：stdout/stderr 可能很大
                truncated = json.loads(
                    smart_truncate_output(result, prefix="ssh_output")
                )
                result.update(truncated)
            return result

        ssh_executor_tool = StructuredTool(
            name="ssh_executor",
            description=(
                f"在远程主机 {cfg.username}@{cfg.host}:{cfg.port} 上执行 Shell 命令。"
                f"每次调用独立建连，cd 不影响后续调用；受超时限制（默认 {cfg.command_timeout} 秒）。"
                "返回 exit_code/stdout/stderr；大量输出先过滤（| head、| grep 等），"
                "否则会被自动截断。远程命令为真实系统操作，删除/重启等高危命令务必与用户确认后执行。"
            ),
            func=None,
            coroutine=run_remote_command,
            args_schema=SshExecutorInput,
        )

        # ---- ssh_upload ----

        async def upload_file(local_path: str, remote_path: str) -> dict:
            remote_norm = (remote_path or "").strip()
            if not remote_norm.startswith("/"):
                return {
                    "success": False,
                    "error": f"remote_path 必须是绝对路径（收到: {remote_path}）",
                    "error_type": "invalid_params",
                }

            source, src_error = await asyncio.to_thread(
                self._resolve_local_source, local_path, transfer_limit_bytes
            )
            if source is None:
                return {
                    "success": False,
                    "error": src_error,
                    "error_type": "invalid_local_path",
                }

            def job(client: SSHClient) -> dict:
                sftp = client.open_sftp()
                # SFTPClient 建会话不支持超时参数；对底层通道设置套接字超时，
                # 仅约束单次收包间隔（停摆超过 15 秒才中断，不影响慢速持续传输）
                sftp.sock.settimeout(_SESSION_OPEN_TIMEOUT)
                try:
                    parent = posixpath.dirname(remote_norm.rstrip("/")) or "/"
                    _sftp_mkdirs(sftp, parent)
                    sftp.put(str(source), remote_norm)
                    size = sftp.stat(remote_norm).st_size
                    return {
                        "success": True,
                        "local_path": str(source),
                        "remote_path": remote_norm,
                        "size": size,
                    }
                finally:
                    sftp.close()

            return await _run_with_client(cfg, job)

        ssh_upload_tool = StructuredTool(
            name="ssh_upload",
            description=(
                "将本地文件通过 SFTP 上传到远程主机。"
                "自动检查本地文件存在性与大小上限（超限拒绝），远程父目录不存在时自动逐级创建。"
                f"单文件上限 {transfer_limit_bytes // 1024 // 1024}MB。"
                "建议先用本地工具确认文件存在（list_files/file_search）。"
            ),
            func=None,
            coroutine=upload_file,
            args_schema=SshUploadInput,
        )

        # ---- ssh_download ----

        async def save_to_file_manager(local_file: Path) -> Optional[dict]:
            """下载成功后导入文件管理器生成下载链接（与 Shell 节点 upload_to_file_manager 同款逻辑）"""
            import mimetypes

            try:
                from app.config.database import AsyncSessionLocal
                from app.config.settings import settings
                from app.services.file_service import file_service

                content = await asyncio.to_thread(local_file.read_bytes)
                max_bytes = settings.max_upload_size * 1024 * 1024
                if len(content) > max_bytes:
                    return None
                mime_type = (
                    mimetypes.guess_type(str(local_file))[0]
                    or "application/octet-stream"
                )
                async with AsyncSessionLocal() as db:
                    file_obj = await file_service.save_bytes_to_fs(
                        db,
                        content,
                        local_file.name,
                        mime_type,
                        source_type="agent_upload",
                    )
                return {
                    "file_id": file_obj.id,
                    "download_url": f"/api/file/download/{file_obj.id}",
                    "preview_url": f"/{file_obj.file_path}",
                }
            except Exception as e:
                logger.warning("下载文件导入文件管理失败（不影响已保存文件）: %s", e)
                return None

        async def download_file(
            remote_path: str, local_path: Optional[str] = None
        ) -> dict:
            remote_norm = (remote_path or "").strip()
            if not remote_norm.startswith("/"):
                return {
                    "success": False,
                    "error": f"remote_path 必须是绝对路径（收到: {remote_path}）",
                    "error_type": "invalid_params",
                }
            remote_name = posixpath.basename(remote_norm.rstrip("/")) or "download.bin"

            target, tgt_error = await asyncio.to_thread(
                self._resolve_local_target, local_path or "", remote_name
            )
            if target is None:
                return {
                    "success": False,
                    "error": tgt_error,
                    "error_type": "invalid_local_path",
                }

            def job(client: SSHClient) -> dict:
                sftp = client.open_sftp()
                # SFTPClient 建会话不支持超时参数；对底层通道设置套接字超时，
                # 仅约束单次收包间隔（停摆超过 15 秒才中断，不影响慢速持续传输）
                sftp.sock.settimeout(_SESSION_OPEN_TIMEOUT)
                try:
                    st = sftp.stat(remote_norm)
                    if stat_module.S_ISDIR(st.st_mode):
                        return {
                            "success": False,
                            "error": f"目标是目录而非文件，请改用 ssh_list_dir 查看: {remote_norm}",
                            "error_type": "is_directory",
                        }
                    size_mb = st.st_size / 1024 / 1024
                    if st.st_size > transfer_limit_bytes:
                        return {
                            "success": False,
                            "error": (
                                f"文件大小 {size_mb:.1f}MB 超过传输上限"
                                f"（{transfer_limit_bytes // 1024 // 1024}MB）"
                            ),
                            "error_type": "too_large",
                        }
                    target.parent.mkdir(parents=True, exist_ok=True)
                    sftp.get(remote_norm, str(target))
                    return {
                        "success": True,
                        "remote_path": remote_norm,
                        "local_path": str(target),
                        "size": st.st_size,
                    }
                finally:
                    sftp.close()

            result = await _run_with_client(cfg, job)
            if result.get("success"):
                imported = await save_to_file_manager(Path(result["local_path"]))
                if imported:
                    result.update(imported)
                    result["hint"] = (
                        "文件已导入文件管理系统，请把 download_url 提供给用户。"
                    )
                else:
                    result["hint"] = "文件已保存到本地工作区，未能生成下载链接。"
            return result

        ssh_download_tool = StructuredTool(
            name="ssh_download",
            description=(
                "从远程主机下载文件到本地（SFTP）。成功后自动导入文件管理系统并返回 download_url，"
                "需在回复中提供给用户。单文件上限 "
                f"{transfer_limit_bytes // 1024 // 1024}MB；不支持下载目录（先用 ssh_list_dir 确认类型）。"
            ),
            func=None,
            coroutine=download_file,
            args_schema=SshDownloadInput,
        )

        # ---- ssh_list_dir ----

        async def list_remote_dir(remote_dir: str = ".") -> dict:
            target_dir = (remote_dir or ".").strip()

            def job(client: SSHClient) -> dict:
                sftp = client.open_sftp()
                # SFTPClient 建会话不支持超时参数；对底层通道设置套接字超时，
                # 仅约束单次收包间隔（停摆超过 15 秒才中断，不影响慢速持续传输）
                sftp.sock.settimeout(_SESSION_OPEN_TIMEOUT)
                try:
                    entries = sftp.listdir_attr(target_dir)
                finally:
                    sftp.close()

                total = len(entries)
                dirs = [e for e in entries if stat_module.S_ISDIR(e.st_mode)]
                files = [e for e in entries if not stat_module.S_ISDIR(e.st_mode)]
                dirs.sort(key=lambda e: e.filename.lower())
                files.sort(key=lambda e: e.filename.lower())

                lines = [
                    f"Total: {total} entries ({len(dirs)} dirs, {len(files)} files)",
                    "TYPE  SIZE  MODIFIED  NAME",
                ]
                shown = 0
                for entry in dirs + files:
                    if shown >= _MAX_LIST_ENTRIES:
                        lines.append(
                            f"\n(仅显示前 {_MAX_LIST_ENTRIES} 条，共 {total} 条，请缩小范围后重试)"
                        )
                        break
                    is_dir = stat_module.S_ISDIR(entry.st_mode)
                    size_str = "-" if is_dir else f"{entry.st_size}"
                    mtime_str = (
                        datetime.fromtimestamp(entry.st_mtime).strftime(
                            "%Y-%m-%d %H:%M"
                        )
                        if entry.st_mtime
                        else "-"
                    )
                    suffix = "/" if is_dir else ""
                    lines.append(
                        f"{'dir ' if is_dir else 'file'}  "
                        f"{size_str:>12}  {mtime_str}  {entry.filename}{suffix}"
                    )
                    shown += 1
                return {"success": True, "listing": "\n".join(lines)}

            return await _run_with_client(cfg, job)

        ssh_list_dir_tool = StructuredTool(
            name="ssh_list_dir",
            description=(
                "列出远程主机目录内容（名称/类型/大小/修改时间），目录以 / 结尾标识。"
                "用于确认远程路径存在性与定位上传下载位置。"
            ),
            func=None,
            coroutine=list_remote_dir,
            args_schema=SshListDirInput,
        )

        # ---- ssh_set_config ----

        async def set_ssh_config(
            host: Optional[str] = None,
            port: Optional[int] = None,
            username: Optional[str] = None,
            auth_type: Optional[str] = None,
            password: Optional[str] = None,
            private_key: Optional[str] = None,
            private_key_path: Optional[str] = None,
            passphrase: Optional[str] = None,
        ) -> dict:
            updates: dict = {}
            # 路径/地址类字段去空白；密码/私钥/口令按原样保留（PEM 内容空白有意义）
            if host and host.strip():
                updates["host"] = host.strip()
            if port is not None:
                if not (1 <= port <= 65535):
                    return {
                        "success": False,
                        "error": f"port 必须在 1-65535 之间（收到: {port}）",
                        "error_type": "invalid_params",
                    }
                updates["port"] = port
            if username and username.strip():
                updates["username"] = username.strip()
            if auth_type and auth_type.strip():
                auth_norm = auth_type.strip()
                if auth_norm not in ("password", "private_key"):
                    return {
                        "success": False,
                        "error": (
                            f"auth_type 仅支持 password/private_key（收到: {auth_norm}）"
                        ),
                        "error_type": "invalid_params",
                    }
                updates["auth_type"] = auth_norm
            if password:
                updates["password"] = password
            if private_key and private_key.strip():
                updates["private_key"] = private_key.strip()
            if private_key_path and private_key_path.strip():
                updates["private_key_path"] = private_key_path.strip()
            if passphrase:
                updates["passphrase"] = passphrase
            if not updates:
                return {
                    "success": False,
                    "error": "未提供任何要更新的字段",
                    "error_type": "invalid_params",
                }
            # 未显式指定认证方式时，按传入的凭据类型推导
            if "auth_type" not in updates:
                if "password" in updates:
                    updates["auth_type"] = "password"
                elif "private_key" in updates or "private_key_path" in updates:
                    updates["auth_type"] = "private_key"

            merged = cfg.model_copy(update=updates)
            config_err = _validate_connection_config(merged)
            if config_err:
                return {
                    "success": False,
                    "error": f"配置校验失败（原配置未修改）: {config_err}",
                    "error_type": "invalid_config",
                }

            # 就地更新闭包持有的 cfg，本次执行内的后续调用立即生效
            for key, value in updates.items():
                setattr(cfg, key, value)

            # 持久化到节点 base_config（下次执行与前端配置面板均生效）
            persisted = False
            if node.id:
                try:
                    from app.config.database import AsyncSessionLocal

                    async with AsyncSessionLocal() as db:
                        row = await db.get(FlowNode, node.id)
                        if row is not None:
                            existing = (
                                row.base_config
                                if isinstance(row.base_config, dict)
                                else {}
                            )
                            row.base_config = {**existing, **updates}
                            await db.commit()
                            persisted = True
                except Exception as e:
                    logger.warning("ssh_set_config 持久化失败（内存配置已生效）: %s", e)

            return {
                "success": True,
                "host": cfg.host,
                "port": cfg.port,
                "username": cfg.username,
                "auth_type": cfg.auth_type,
                "persisted": persisted,
                "message": "连接配置已更新，后续 SSH 工具调用将使用新配置"
                + (
                    "（已保存到节点）"
                    if persisted
                    else "（节点未保存，仅本次执行生效）"
                ),
            }

        ssh_set_config_tool = StructuredTool(
            name="ssh_set_config",
            description=(
                "设置/更新 SSH 节点的连接配置（主机、端口、用户名、密码、私钥）。"
                "字段级合并，只覆盖传入的字段，未传入的保留原值；"
                "认证方式不传时按提供的凭据自动推导。"
                "未预设主机或需要更换目标主机时先调用本工具，"
                "配置会持久化到节点，之后的 ssh_executor/ssh_upload/ssh_download/"
                "ssh_list_dir 自动使用新配置，无需每次重复传入。"
            ),
            func=None,
            coroutine=set_ssh_config,
            args_schema=SshSetConfigInput,
        )

        return [
            ssh_executor_tool,
            ssh_upload_tool,
            ssh_download_tool,
            ssh_list_dir_tool,
            ssh_set_config_tool,
        ]

    @classmethod
    def get_tool_info(cls, node: FlowNode) -> list[dict]:
        return [
            {"name": "ssh_executor", "description": "在远程主机上执行 Shell 命令"},
            {"name": "ssh_upload", "description": "将本地文件通过 SFTP 上传到远程主机"},
            {
                "name": "ssh_download",
                "description": "从远程主机下载文件到本地并生成下载链接",
            },
            {"name": "ssh_list_dir", "description": "列出远程主机目录内容"},
            {
                "name": "ssh_set_config",
                "description": "设置或更新 SSH 节点的连接配置（主机/凭据），持久化到节点",
            },
        ]

    async def get_system_prompt_hint(self, node: FlowNode) -> Optional[str]:
        """向 LLM 注入 SSH 工具使用说明（含连接目标、输出控制、当前时间）"""
        cfg = self._get_config(node)
        target = f"{cfg.username}@{cfg.host}:{cfg.port}"
        current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if (cfg.host or "").strip():
            target_line = (
                f"你已连接 SSH 节点，目标主机: `{target}`（auth_type={cfg.auth_type}）；"
                "如需更换目标主机可先调用 ssh_set_config 更新"
            )
        else:
            target_line = (
                "SSH 节点尚未预设连接信息，请先调用 ssh_set_config 提供主机与凭据"
                "（host/username/password 或 private_key），配置会持久化到节点"
            )

        lines = [
            "\n\n## SSH 远程操作\n"
            f"{target_line}。\n"
            "- ssh_executor 在远端用户默认 Shell 中执行命令；每次调用独立建连，cd 不影响后续调用\n"
            "- 大量输出先过滤（| head -100、| grep xxx 等），否则结果会被自动截断\n"
            f"- 命令默认超时 {cfg.command_timeout} 秒，长耗时可传更大的 timeout 参数（上限3600秒）；"
            "启动服务/编译等长任务建议 nohup ... & 后台化再用后续命令查日志\n"
            "- ssh_list_dir 查看远程目录结构；ssh_upload/ssh_download 用 SFTP 传文件（"
            f"单文件上限 {min(50, cfg.max_transfer_mb)}MB），下载的文件会自动获得 download_url\n"
            "- 远程路径是 POSIX 风格绝对路径；上传远程父目录不存在时会自动创建\n"
            "- 远程操作是真实系统变更：rm/chmod/systemctl restart 类高危命令，务必先向用户说明并获得确认\n"
            f"\n当前时间: {current_time_str}"
        ]
        return "\n".join(lines)

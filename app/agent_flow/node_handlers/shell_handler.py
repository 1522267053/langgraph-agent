"""
Shell命令执行节点处理器
提供受限的命令执行环境
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
import fnmatch
import json
import os
import platform
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import StreamWriter
from pydantic import BaseModel, Field

from app.agent_flow.flow_context import FlowState
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.node_handlers.base_handler import (
    BaseNodeHandler,
    BaseNodeConfig,
    NodeVariable,
)
from app.agent_flow.tool_output_truncate import smart_truncate_output
from app.config.build_utils import BASE_DIR, get_temp_dir
from app.models.flow_node import FlowNode


class ShellNodeConfig(BaseNodeConfig):
    command: str = ""
    timeout: int = 300
    async_wait: int = 8
    output_variables: list[NodeVariable] = [
        NodeVariable(name="stdout", type="string"),
        NodeVariable(name="stderr", type="string"),
        NodeVariable(name="exit_code", type="number"),
    ]


BLOCKED_COMMANDS = {
    # Windows 危险命令
    "format",
    "diskpart",
    "reg",
    "sc",
    "netsh",
    "bitsadmin",
    "shutdown",
    "reboot",
    "mstsc",
    "eventvwr",
    "wmic",
    "taskkill",
    "schtasks",
    "bcdedit",
    "cipher",
    # Linux 危险命令
    "sudo",
    "su",
    "shutdown",
    "reboot",
    "mkfs",
    "mkswap",
    "killall",
    "systemctl",
    "service",
    "crontab",
    "at",
    "fdisk",
    "parted",
    "gdisk",
    "iptables",
    "ip6tables",
    "sysctl",
    "shred",
    "useradd",
    "usermod",
    "userdel",
    "groupadd",
    "groupmod",
    "groupdel",
}

DANGEROUS_PATTERNS = [
    # windows高危行为
    # 1. 毁灭性删除与格式化
    r"\bdel\s+/f\s+/q\s+(/|\\)",  # 强制删除根目录文件
    r"\bdel\s+.*\s+\*\.\*",  # 删除所有文件
    r"(?<!-)\bformat\s+[A-Za-z]:",  # 格式化磁盘（排除 --format 参数）
    r"\brd\s+/s\s+/q\s+(/|\\)",  # 强制删除根目录树
    r"\bdiskpart\b",  # 磁盘分区工具
    r"\bchkdsk\s+/f\s+/r",  # 磁盘检查与修复 (可能锁盘)
    # 2. 系统配置与服务破坏
    r"\bsc\s+delete\b",  # 删除系统服务
    r"\breg\s+delete\s+HKLM",  # 删除关键注册表项
    r"\breg\s+delete\s+HKCU",  # 删除用户注册表
    r"\bsystem32",  # 涉及 system32 目录的操作
    r"\bsyswow64",  # 涉及 syswow64 目录的操作
    # 3. 远程执行与脚本注入
    r"\bpowershell\s+.*-EncodedCommand",  # 执行编码命令 (混淆攻击)
    r"\b(netsh|bitsadmin).*http",  # 利用系统工具下载 (常用于下载恶意软件)
    r"\bwmic\s+.*process\s+call\s+create",  # 进程创建 (可能被用于提权)
    # linux高危行为
    # 1. 毁灭性删除 (强制删除根目录、家目录、所有文件)
    r"\brm\s+(-[rf]+\s+){0,2}/\b",  # rm -rf /
    r"\brm\s+(-[rf]+\s+){0,2}~",  # rm -rf ~
    r"\brm\s+(-[rf]+\s+){0,2}\*",  # rm -rf * (在根目录或关键目录)
    r"\brm\s+--no-preserve-root",  # 绕过保护机制
    # 2. 磁盘与文件系统破坏 (格式化、底层写入)
    r"\bmkfs\.",  # mkfs.ext4, mkfs.xfs 等
    r"\bdd\s+if=.*\s+of=/dev/",  # dd 写入设备
    r"\bmkswap\b",  # 格式化交换分区
    r"\b>\s*/dev/sd",  # 重定向写入磁盘设备
    r"\b>\s*/dev/hd",  # 重定向写入磁盘设备
    # 3. 系统权限与用户篡改 (锁死系统、提权)
    r"\bchmod\s+(-R\s+)?777\s+/",  # 开放根目录所有权限
    r"\bchmod\s+(-R\s+)?000\s+/",  # 锁死根目录所有权限
    r"\bchown\s+(-R\s+)?[^:]+:[^:]+\s+/",  # 修改根目录归属
    r"\buserdel\s+(-r\s+)?root",  # 删除 root 用户
    r"\bpasswd\s+-d",  # 删除密码
    # 4. 进程查杀与系统关闭 (宕机)
    r"\bkill\s+(-9\s+)?1\b",  # 杀死 init 进程
    r"\bpkill\s+(-9\s+)?init",  # 杀死 init
    r"\b:(){:|:&};:",  #  fork 炸弹
    r"\breboot\s+-f",  # 强制重启
    # 5. 远程代码执行与后门 (安全风险)
    r"\bcurl\b.*\|\s*(ba)?sh",  # curl | sh (远程执行脚本)
    r"\bwget\b.*\|\s*(ba)?sh",  # wget | sh
    r"\bnc\b.*-e\s+(ba)?sh",  # nc 反弹 shell
    r"\bncat\b.*-e\s+(ba)?sh",  # ncat 反弹 shell
    # 6. Windows 服务与用户管理
    r"\bnet\s+stop\b",  # 停止 Windows 服务
    r"\bnet\s+start\b",  # 启动 Windows 服务
    r"\bnet\s+user\b",  # Windows 用户管理
    r"\bnet\s+localgroup\b",  # Windows 用户组管理
    # 7. Linux 进程终止（扩大覆盖面）
    r"\bkill\s+-9\b",  # 强制终止进程
    r"\bkill\s+(-[0-9]+\s+)?(?!1\b)\d+\b",  # kill 任意非 init 进程
    r"\bpkill\s+(?!init\b)",  # pkill 按模式杀进程（排除已有 init 规则）
    # 8. 包管理卸载（防止破坏运行环境）
    r"\bpip\s+uninstall\b",  # Python 包卸载
    r"\bnpm\s+uninstall\b",  # npm 包卸载
    r"\bpoetry\s+remove\b",  # Poetry 依赖移除
    r"\bconda\s+remove\b",  # Conda 包移除
]


DATA_READONLY_COMMANDS = {
    "dir",
    "ls",
    "cat",
    "type",
    "findstr",
    "grep",
    "find",
    "more",
    "less",
    "head",
    "tail",
    "wc",
    "stat",
    "file",
}


def _command_targets_data_dir(command: str) -> bool:
    """检测命令是否引用了项目数据目录（data/）"""
    data_dir_resolved = str((BASE_DIR / "data").resolve())
    if data_dir_resolved in command:
        return True
    if re.search(r'(?:^|[\s"\'>|;])(?:\.?[\\/])?data(?:[\\/]|$|\s)', command):
        return True
    return False


def validate_command(command: str) -> tuple[bool, str]:
    """
    验证命令是否安全

    Args:
        command: 要执行的命令

    Returns:
        (是否安全, 错误消息)
    """
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return False, f"命令包含危险操作: {pattern}"

    first_word = command.strip().split()[0] if command.strip() else ""
    if first_word:
        base_cmd = first_word.split("/")[-1].split("\\")[-1]
        if base_cmd in BLOCKED_COMMANDS:
            return False, f"命令 '{base_cmd}' 在禁止列表中"

    if _command_targets_data_dir(command):
        base_cmd = first_word.split("/")[-1].split("\\")[-1]
        if base_cmd not in DATA_READONLY_COMMANDS:
            return False, f"不允许对数据目录执行写操作（仅支持查看），命令: {command}"

    return True, ""


FORBIDDEN_PATH_PATTERNS = [
    # Windows 系统关键路径
    r"[Cc]:\\[Ww]indows",
    r"[Cc]:\\[Pp]rogram\s+[Ff]iles",
    r"[Cc]:\\[Pp]rogram\s+[Dd]ata",
    r"[Cc]:\\[Pp]rogram\s+[Ff]iles\s*\(x86\)",
    # Linux 系统关键路径
    r"/etc/",
    r"/usr/",
    r"/bin/",
    r"/sbin/",
    r"/boot/",
    r"/dev/",
    r"/proc/",
    r"/sys/",
    r"/lib/",
    r"/lib64/",
]

MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_CONTENT_SIZE = 50 * 1024 * 1024
MAX_FILE_READ_LINES = 100
MAX_SEARCH_RESULTS = 50
MAX_SEARCH_FILE_SIZE = 5 * 1024 * 1024

SKIP_DIR_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "coverage",
    ".coverage",
}


def _is_hidden_path(path: Path) -> bool:
    """检查路径是否包含隐藏文件或隐藏目录（以 . 开头的部分）"""
    return any(part.startswith(".") for part in path.parts)


def _validate_file_path(file_path: str) -> tuple[bool, str]:
    """校验文件路径是否安全

    禁止访问系统关键路径，禁止路径穿越。

    Returns:
        (是否安全, 错误消息)
    """
    path = Path(file_path).resolve()

    # 路径穿越检测
    if ".." in Path(file_path).parts:
        return False, "文件路径不允许包含 '..' 路径穿越"

    # 系统关键路径检测
    path_str = str(path)
    for pattern in FORBIDDEN_PATH_PATTERNS:
        if re.search(pattern, path_str):
            return False, f"不允许访问系统路径: {path_str}"

    return True, ""


def _validate_writable_path(file_path: str) -> tuple[bool, str]:
    """校验文件写入路径是否安全

    在通用路径校验基础上，额外禁止写入项目数据目录（data/ 下存放数据库、向量库等）。
    读取操作不受此限制。

    Returns:
        (是否安全, 错误消息)
    """
    is_valid, error_msg = _validate_file_path(file_path)
    if not is_valid:
        return False, error_msg

    path = Path(file_path).resolve()
    data_dir = (BASE_DIR / "data").resolve()
    if path.is_relative_to(data_dir):
        return False, f"不允许写入数据目录: {path}"

    return True, ""


def _detect_and_read(path: Path) -> tuple[str, str]:
    """读取文件内容，自动检测编码（UTF-8 优先，GBK 回退）

    Returns:
        (文件内容, 使用的编码名称)
    """
    try:
        content = path.read_text(encoding="utf-8")
        return content, "utf-8"
    except UnicodeDecodeError:
        content = path.read_text(encoding="gbk", errors="replace")
        return content, "gbk"


def _atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    """原子写入文件：先写临时文件，再替换目标文件，避免写入中断导致文件损坏"""
    fd, tmp_path_str = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
        Path(tmp_path_str).replace(path)
    except Exception:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise


class ShellToolInput(BaseModel):
    """Shell执行工具输入参数"""

    command: str = Field(..., description="要执行的Shell命令")


class FileReadInput(BaseModel):
    """文件读取工具输入参数"""

    file_path: str = Field(..., description="文件绝对路径")
    offset: Optional[int] = Field(
        None, description="起始行号（从1开始），不传则从头读取"
    )
    limit: Optional[int] = Field(
        None,
        description="读取行数，单次最多 100 行，不传则读取 100 行",
    )


class TextEditInput(BaseModel):
    """文本编辑工具输入参数（精确字符串替换）"""

    file_path: str = Field(..., description="文件绝对路径")
    old_string: str = Field(
        ..., min_length=1, description="要替换的原始文本（必须精确匹配）"
    )
    new_string: str = Field(..., description="替换后的新文本")
    replace_all: bool = Field(False, description="是否替换所有匹配项，默认仅替换第一个")


class FileWriteInput(BaseModel):
    """文件写入工具输入参数"""

    file_path: str = Field(..., description="文件绝对路径")
    content: str = Field(..., description="要写入的文件内容（覆盖或新建）")


class ShellTaskStatusInput(BaseModel):
    """查询后台Shell任务状态"""

    task_id: str = Field(..., description="后台任务ID")


class ShellTaskInputInput(BaseModel):
    """向后台Shell任务发送输入"""

    task_id: str = Field(..., description="后台任务ID")
    input_text: str = Field(..., description="要发送到进程stdin的文本内容")


class ShellTaskCancelInput(BaseModel):
    """终止后台Shell任务"""

    task_id: str = Field(..., description="要终止的后台任务ID")


class FileSearchInput(BaseModel):
    """文件内容搜索工具输入参数"""

    pattern: str = Field(..., description="正则表达式搜索模式")
    path: Optional[str] = Field(
        None, description="搜索目录路径，不传则搜索当前工作目录"
    )
    include: Optional[str] = Field(
        None,
        description="文件类型过滤（如 *.py, *.{ts,tsx}），仅搜索匹配的文件",
    )
    literal_text: bool = Field(
        False,
        description="是否将 pattern 作为纯文本搜索（自动转义正则特殊字符），默认 False",
    )


def _decode_output(data: bytes) -> str:
    """三重解码：UTF-8 → GBK → 逐行混合解码（处理管道输出中 UTF-8/GBK 混合的情况）"""
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


def _apply_shell_output_truncation(result: dict, task) -> None:
    """对 shell 工具返回的 stdout/stderr 应用统一截断，就地修改 result dict

    使用公共截断模块的 JSON 感知截断：stdout 和 stderr 作为 dict 的独立字段，
    会被 smart_truncate_output 内部的 _truncate_dict 分别截断。
    """
    if task.stdout:
        result["stdout"] = task.stdout
    if task.stderr:
        result["stderr"] = task.stderr
    truncated = json.loads(smart_truncate_output(result, prefix="shell_output"))
    result.update(truncated)


def _diff_preview(
    old_string: str, new_string: str, max_lines: int = 6, max_line_width: int = 240
) -> str:
    """生成 text_editor 的 diff 预览，返回 -/+ 格式的紧凑摘要"""
    old_lines = old_string.splitlines()
    new_lines = new_string.splitlines()
    shown_old = old_lines[:max_lines]
    shown_new = new_lines[:max_lines]
    diff_lines = []
    for line in shown_old:
        truncated = (
            line[:max_line_width] + "..." if len(line) > max_line_width else line
        )
        diff_lines.append(f"-{truncated}")
    if len(old_lines) > max_lines:
        diff_lines.append("-...")
    for line in shown_new:
        truncated = (
            line[:max_line_width] + "..." if len(line) > max_line_width else line
        )
        diff_lines.append(f"+{truncated}")
    if len(new_lines) > max_lines:
        diff_lines.append("+...")
    return "\n".join(diff_lines)


@dataclass
class BackgroundShellTask:
    """后台运行的Shell任务"""

    task_id: str
    command: str
    status: str = "running"
    stdout: str = ""
    stderr: str = ""
    return_code: Optional[int] = None
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None
    process: Optional[asyncio.subprocess.Process] = None
    _monitor_task: Optional[asyncio.Task] = field(default=None, repr=False)
    _stdin_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _stdout_bytes: bytearray = field(default_factory=bytearray, repr=False)
    _stderr_bytes: bytearray = field(default_factory=bytearray, repr=False)

    def to_dict(self) -> dict:
        elapsed = None
        if self.end_time and self.start_time:
            elapsed = (self.end_time - self.start_time).total_seconds()
        elif self.start_time:
            elapsed = (datetime.now() - self.start_time).total_seconds()
        result = {
            "task_id": self.task_id,
            "command": self.command,
            "status": self.status,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "return_code": self.return_code,
            "elapsed_seconds": round(elapsed, 2) if elapsed else None,
        }
        return result


_background_tasks: dict[str, BackgroundShellTask] = {}
_task_expire_seconds: int = 300


async def _read_stream(
    stream: asyncio.StreamReader,
    task: BackgroundShellTask,
    stream_name: str,
) -> None:
    """持续读取子进程的 stdout/stderr，累积原始字节到 task 中（最后统一解码避免 UTF-8 截断）"""
    try:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            if stream_name == "stdout":
                task._stdout_bytes.extend(chunk)
            else:
                task._stderr_bytes.extend(chunk)
    except Exception:
        pass


async def _monitor_process(
    task: BackgroundShellTask,
    timeout: float,
) -> None:
    """后台监控协程：读取 stdout/stderr 并等待进程结束，超时则 kill"""
    process = task.process
    if not process:
        task.status = "failed"
        task.end_time = datetime.now()
        return

    try:
        await asyncio.wait_for(
            asyncio.gather(
                _read_stream(process.stdout, task, "stdout"),
                _read_stream(process.stderr, task, "stderr"),
                process.wait(),
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        task.status = "timeout"
        task.return_code = -1
    except Exception:
        task.status = "failed"
        task.return_code = -1
    else:
        task.return_code = process.returncode
        task.status = "completed" if process.returncode == 0 else "failed"
    finally:
        task.stdout = _decode_output(bytes(task._stdout_bytes))
        task.stderr = _decode_output(bytes(task._stderr_bytes))
        task.end_time = datetime.now()
        if process.stdin and not process.stdin.is_closing():
            try:
                process.stdin.close()
                await process.stdin.wait_closed()
            except Exception:
                pass


def _cleanup_expired_tasks() -> None:
    """清理已完成超过 _task_expire_seconds 的任务，释放内存"""
    now = datetime.now()
    expired_ids = []
    for tid, task in _background_tasks.items():
        if (
            task.end_time
            and (now - task.end_time).total_seconds() > _task_expire_seconds
        ):
            expired_ids.append(tid)
    for tid in expired_ids:
        task = _background_tasks.pop(tid, None)
        if task and task._monitor_task and not task._monitor_task.done():
            task._monitor_task.cancel()


async def _force_kill_process_tree(process: asyncio.subprocess.Process) -> None:
    """强制杀掉进程及其子进程树"""
    pid = process.pid
    if not pid:
        return
    try:
        if platform.system() == "Windows":
            proc = await asyncio.create_subprocess_shell(
                f"taskkill /F /T /PID {pid}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)
        else:
            import signal

            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        try:
            process.kill()
        except ProcessLookupError:
            pass


@NodeHandlerRegistry.register("shell")
class ShellNodeHandler(BaseNodeHandler):
    """
    Shell命令执行节点处理器

    功能：
    1. 在受限环境中执行Shell命令
    2. 黑名单机制：禁止预定义的危险命令
    3. 危险操作检测
    4. 超时控制
    5. 捕获标准输出和标准错误
    """

    ConfigClass = ShellNodeConfig

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        cfg = self._get_config(node)

        command_template = cfg.command
        timeout = cfg.timeout

        if not command_template:
            return state

        context = self._resolve_input_variables(cfg.input_variables, state)
        command = self._render_template(command_template, state, context)

        if not command:
            return state

        is_valid, error_msg = validate_command(command)
        if not is_valid:
            state.add_error(node.node_key, error_msg)
            return state

        try:
            result = await self._execute_shell(command, timeout)
            output_names = self._get_output_var_names(
                node, ["stdout", "stderr", "exit_code"]
            )
            stdout_name = output_names[0] if len(output_names) > 0 else "stdout"
            stderr_name = output_names[1] if len(output_names) > 1 else "stderr"
            exit_code_name = output_names[2] if len(output_names) > 2 else "exit_code"
            state.set_node_variable(node.node_key, stdout_name, result["stdout"])
            state.set_node_variable(node.node_key, stderr_name, result["stderr"])
            state.set_node_variable(
                node.node_key, exit_code_name, result["return_code"]
            )
        except asyncio.TimeoutError:
            state.add_error(node.node_key, f"命令执行超时（{timeout}秒）")
        except Exception as e:
            state.add_error(node.node_key, f"命令执行失败: {str(e)}")

        return state

    async def _execute_shell(self, command: str, timeout: float) -> dict:
        """执行Shell命令（Flow 节点专用，stdin 重定向到 DEVNULL 防止交互阻塞）

        Args:
            command: Shell命令字符串
            timeout: 超时时间（秒）

        Returns:
            包含执行结果的字典
        """
        try:
            if platform.system() == "Windows":
                command = f"chcp 65001 >nul 2>&1 && {command}"

            env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
            process = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                shell=True,
                env=env,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise

            return {
                "stdout": _decode_output(stdout),
                "stderr": _decode_output(stderr),
                "return_code": process.returncode,
                "success": process.returncode == 0,
                "command": command,
            }

        except Exception as e:
            return {
                "stdout": "",
                "stderr": str(e),
                "return_code": -1,
                "success": False,
                "command": command,
            }

    @classmethod
    def get_input_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        if config is None:
            config = node.base_config or {}

        raw_command = config.get("command", "")
        if not raw_command:
            return None

        input_vars = config.get("input_variables", [])
        context = {}
        for var in input_vars:
            name = var.get("name", "")
            source = var.get("source", "")
            if name and source:
                context[name] = resolver.resolve_safe(source, state)

        return {
            "command": resolver.render_template(raw_command, state, context),
            "timeout": config.get("timeout", 30),
        }

    @classmethod
    def get_output_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        if config is None:
            config = node.base_config or {}
        output = {}

        output_vars = config.get("output_variables", [])
        if output_vars:
            for var in output_vars:
                name = (
                    var.get("name", "")
                    if isinstance(var, dict)
                    else getattr(var, "name", "")
                )
                if name:
                    value = state.get_node_variable(node.node_key, name)
                    if value is not None:
                        output[name] = value
        else:
            value = state.get_node_variable(node.node_key, "shell_result")
            if value is not None:
                output["shell_result"] = value

        return output if output else None

    @classmethod
    def allow_multiple_tool_connections(cls) -> bool:
        """Shell 节点使用固定工具名，不允许同一 LLM 连接多个 Shell 节点"""
        return False

    async def get_tool(self, node: FlowNode) -> list[BaseTool]:
        """返回工具列表：Shell执行(异步) + 任务状态查询 + 任务输入 + 文件读取 + 文本编辑 + 文件写入"""
        cfg = self._get_config(node)
        timeout = cfg.timeout
        async_wait = cfg.async_wait

        system_type = platform.system()
        system_info = f"当前系统: {system_type}"

        # ---- shell_executor ----

        async def execute_shell(command: str) -> str:
            is_valid, error_msg = validate_command(command)
            if not is_valid:
                return json.dumps(
                    {"error": error_msg, "success": False}, ensure_ascii=False
                )

            _cleanup_expired_tasks()

            actual_command = command
            if platform.system() == "Windows":
                actual_command = f"chcp 65001 >nul 2>&1 && {command}"

            env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
            try:
                process = await asyncio.create_subprocess_shell(
                    actual_command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    shell=True,
                    env=env,
                )
            except Exception as e:
                return json.dumps(
                    {"error": f"启动进程失败: {e}", "success": False},
                    ensure_ascii=False,
                )

            task = BackgroundShellTask(
                task_id=str(uuid.uuid4()),
                command=command,
                process=process,
            )
            _background_tasks[task.task_id] = task

            monitor = asyncio.create_task(_monitor_process(task, timeout))
            task._monitor_task = monitor

            done, _ = await asyncio.wait({monitor}, timeout=async_wait)

            if monitor in done:
                _background_tasks.pop(task.task_id, None)
                result = {"success": True, **task.to_dict()}
                _apply_shell_output_truncation(result, task)
                return json.dumps(result, ensure_ascii=False)

            result = {
                "success": True,
                "async": True,
                "message": f"命令仍在执行中，请使用 shell_task_status 工具查询进度（task_id: {task.task_id}）",
                **task.to_dict(),
            }
            _apply_shell_output_truncation(result, task)
            return json.dumps(result, ensure_ascii=False)

        shell_tool = StructuredTool(
            name="shell_executor",
            description=(
                f"在受限环境中执行Shell命令。{system_info}。"
                f"命令执行等待 {async_wait} 秒，若未完成则返回 task_id，"
                f"之后可用 shell_task_status 查询进度，用 shell_task_input 向进程发送输入。"
                f"禁止危险命令: sudo, rm -rf /, format, diskpart, shutdown, reboot等。"
            ),
            func=None,
            coroutine=execute_shell,
            args_schema=ShellToolInput,
        )

        # ---- shell_task_status ----

        async def query_task_status(task_id: str) -> str:
            _cleanup_expired_tasks()
            task = _background_tasks.get(task_id)
            if not task:
                return json.dumps(
                    {"error": f"任务 {task_id} 不存在或已过期", "success": False},
                    ensure_ascii=False,
                )
            if (
                task.status == "running"
                and task._monitor_task
                and not task._monitor_task.done()
            ):
                await asyncio.wait({task._monitor_task}, timeout=async_wait)
            result = {"success": True, **task.to_dict()}
            if task.stdout or task.stderr:
                _apply_shell_output_truncation(result, task)
            return json.dumps(result, ensure_ascii=False)

        shell_task_status_tool = StructuredTool(
            name="shell_task_status",
            description=(
                "查询后台Shell任务的执行状态和输出。"
                "当 shell_executor 返回 task_id 时使用此工具获取进度。"
                "返回字段: status(running/completed/failed/timeout), stdout, stderr, return_code, elapsed_seconds。"
            ),
            func=None,
            coroutine=query_task_status,
            args_schema=ShellTaskStatusInput,
        )

        # ---- shell_task_input ----

        async def send_task_input(task_id: str, input_text: str) -> str:
            _cleanup_expired_tasks()
            task = _background_tasks.get(task_id)
            if not task:
                return json.dumps(
                    {"error": f"任务 {task_id} 不存在或已过期", "success": False},
                    ensure_ascii=False,
                )
            if task.status != "running":
                return json.dumps(
                    {
                        "error": f"任务已结束（status={task.status}），无法发送输入",
                        "success": False,
                        **task.to_dict(),
                    },
                    ensure_ascii=False,
                )
            process = task.process
            if not process or not process.stdin or process.stdin.is_closing():
                return json.dumps(
                    {"error": "进程 stdin 已关闭，无法发送输入", "success": False},
                    ensure_ascii=False,
                )
            try:
                async with task._stdin_lock:
                    process.stdin.write((input_text + "\n").encode("utf-8"))
                    await process.stdin.drain()
            except Exception as e:
                return json.dumps(
                    {"error": f"发送输入失败: {e}", "success": False},
                    ensure_ascii=False,
                )
            return json.dumps(
                {"success": True, "message": "输入已发送", **task.to_dict()},
                ensure_ascii=False,
            )

        shell_task_input_tool = StructuredTool(
            name="shell_task_input",
            description=(
                "向正在运行的后台Shell任务发送输入（写入进程的stdin）。"
                "当命令需要交互输入（如确认提示、密码等）时使用。"
                "输入内容会自动追加换行符。"
            ),
            func=None,
            coroutine=send_task_input,
            args_schema=ShellTaskInputInput,
        )

        # ---- shell_task_cancel ----

        async def cancel_task(task_id: str) -> str:
            _cleanup_expired_tasks()
            task = _background_tasks.get(task_id)
            if not task:
                return json.dumps(
                    {"error": f"任务 {task_id} 不存在或已过期", "success": False},
                    ensure_ascii=False,
                )
            if task.status != "running":
                return json.dumps(
                    {
                        "error": f"任务已结束（status={task.status}），无法取消",
                        "success": False,
                        **task.to_dict(),
                    },
                    ensure_ascii=False,
                )
            process = task.process
            if not process:
                return json.dumps(
                    {"error": "进程引用丢失", "success": False},
                    ensure_ascii=False,
                )

            if task._monitor_task and not task._monitor_task.done():
                task._monitor_task.cancel()

            if process.stdin and not process.stdin.is_closing():
                try:
                    process.stdin.close()
                except Exception:
                    pass

            try:
                process.kill()
            except ProcessLookupError:
                pass

            try:
                await asyncio.wait_for(asyncio.shield(process.wait()), timeout=5)
            except (asyncio.TimeoutError, Exception):
                await _force_kill_process_tree(process)
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except (asyncio.TimeoutError, Exception):
                    pass

            task.status = "cancelled"
            task.return_code = process.returncode
            task.end_time = datetime.now()

            return json.dumps(
                {"success": True, "message": "任务已取消", **task.to_dict()},
                ensure_ascii=False,
            )

        shell_task_cancel_tool = StructuredTool(
            name="shell_task_cancel",
            description="终止正在运行的后台Shell任务。当命令执行时间过长或不再需要时使用。",
            func=None,
            coroutine=cancel_task,
            args_schema=ShellTaskCancelInput,
        )

        # ---- file_read ----

        async def file_read(
            file_path: str,
            offset: Optional[int] = None,
            limit: Optional[int] = None,
        ) -> str:
            is_valid, error_msg = _validate_file_path(file_path)
            if not is_valid:
                return json.dumps(
                    {"error": error_msg, "success": False}, ensure_ascii=False
                )

            path = Path(file_path).resolve()
            if not path.exists():
                return json.dumps(
                    {"error": f"文件不存在: {file_path}", "success": False},
                    ensure_ascii=False,
                )
            if not path.is_file():
                return json.dumps(
                    {"error": f"路径不是文件: {file_path}", "success": False},
                    ensure_ascii=False,
                )

            file_size = path.stat().st_size
            if file_size > MAX_FILE_SIZE:
                return json.dumps(
                    {
                        "error": f"文件过大（{file_size} 字节），最大支持 {MAX_FILE_SIZE} 字节",
                        "success": False,
                    },
                    ensure_ascii=False,
                )

            try:
                raw, _encoding = _detect_and_read(path)
            except Exception as e:
                return json.dumps(
                    {"error": f"文件读取失败: {e}", "success": False},
                    ensure_ascii=False,
                )

            lines = raw.splitlines()
            total_lines = len(lines)
            actual_limit = (
                min(limit, MAX_FILE_READ_LINES) if limit else MAX_FILE_READ_LINES
            )
            start = (offset - 1) if offset and offset >= 1 else 0
            end = min(start + actual_limit, len(lines))
            selected = lines[start:end]

            content = "\n".join(
                f"{start + i + 1}: {line}" for i, line in enumerate(selected)
            )
            result: dict = {
                "success": True,
                "file_path": str(path),
                "total_lines": total_lines,
                "offset": start + 1,
                "limit": len(selected),
                "content": content,
            }
            if end < total_lines:
                result["has_more"] = True
            return json.dumps(result, ensure_ascii=False)

        file_read_tool = StructuredTool(
            name="file_read",
            description=(
                "读取文件内容，返回带行号的文本(格式如 ```12: 文件内容的一行```)。"
                "单次最多读取 100 行，大文件请多次调用并指定 offset 分段读取。"
                "读取前无需校验文件是否存在，工具会自动处理。"
            ),
            func=None,
            coroutine=file_read,
            args_schema=FileReadInput,
        )

        # ---- text_editor ----

        async def text_editor(
            file_path: str,
            old_string: str,
            new_string: str,
            replace_all: bool = False,
        ) -> str:
            is_valid, error_msg = _validate_writable_path(file_path)
            if not is_valid:
                return json.dumps(
                    {"error": error_msg, "success": False}, ensure_ascii=False
                )

            path = Path(file_path).resolve()
            if not path.exists():
                return json.dumps(
                    {"error": f"文件不存在: {file_path}", "success": False},
                    ensure_ascii=False,
                )

            if old_string == new_string:
                return json.dumps(
                    {
                        "error": "old_string 与 new_string 相同，无需替换",
                        "success": False,
                    },
                    ensure_ascii=False,
                )

            file_size = path.stat().st_size
            if file_size > MAX_FILE_SIZE:
                return json.dumps(
                    {
                        "error": f"文件过大（{file_size} 字节），最大支持 {MAX_FILE_SIZE} 字节",
                        "success": False,
                    },
                    ensure_ascii=False,
                )

            try:
                raw, encoding = _detect_and_read(path)
            except Exception as e:
                return json.dumps(
                    {"error": f"文件读取失败: {e}", "success": False},
                    ensure_ascii=False,
                )

            if old_string not in raw:
                return json.dumps(
                    {
                        "error": "未找到要替换的原始文本（old_string），请检查是否与文件内容完全一致（包括缩进和换行）",
                        "success": False,
                    },
                    ensure_ascii=False,
                )

            count = raw.count(old_string)
            if count > 1 and not replace_all:
                return json.dumps(
                    {
                        "error": f"找到 {count} 处匹配，请缩小 old_string 范围使其唯一匹配，或设置 replace_all=True 替换所有匹配",
                        "success": False,
                    },
                    ensure_ascii=False,
                )

            if replace_all:
                new_raw = raw.replace(old_string, new_string)
            else:
                new_raw = raw.replace(old_string, new_string, 1)

            try:
                _atomic_write(path, new_raw, encoding=encoding)
            except Exception as e:
                return json.dumps(
                    {"error": f"文件写入失败: {e}", "success": False},
                    ensure_ascii=False,
                )

            replaced_count = count if replace_all else 1
            diff = _diff_preview(old_string, new_string)
            return json.dumps(
                {
                    "success": True,
                    "file_path": str(path),
                    "replaced_count": replaced_count,
                    "message": f"成功替换 {replaced_count} 处文本",
                    "diff": diff,
                },
                ensure_ascii=False,
            )

        text_editor_tool = StructuredTool(
            name="text_editor",
            description=(
                "精确替换文件中的文本。"
                "传入 old_string（要替换的原始文本）和 new_string（替换后的新文本），必须精确匹配。"
                "old_string 需与文件内容完全一致（包括缩进、空格和换行）。"
                "如果 old_string 匹配多处且未设置 replace_all，会返回错误提示。"
                "编辑文件前建议先使用 file_read 读取文件内容，确认要替换的文本。"
            ),
            func=None,
            coroutine=text_editor,
            args_schema=TextEditInput,
        )

        # ---- file_write ----

        async def file_write(file_path: str, content: str) -> str:
            is_valid, error_msg = _validate_writable_path(file_path)
            if not is_valid:
                return json.dumps(
                    {"error": error_msg, "success": False}, ensure_ascii=False
                )

            content_size = len(content.encode("utf-8"))
            if content_size > MAX_CONTENT_SIZE:
                return json.dumps(
                    {
                        "error": f"写入内容过大（{content_size} 字节），最大支持 {MAX_CONTENT_SIZE} 字节",
                        "success": False,
                    },
                    ensure_ascii=False,
                )

            path = Path(file_path).resolve()
            existed = path.exists()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(path, content, encoding="utf-8")
            except Exception as e:
                return json.dumps(
                    {"error": f"文件写入失败: {e}", "success": False},
                    ensure_ascii=False,
                )

            action = "覆盖" if existed else "新建"
            return json.dumps(
                {
                    "success": True,
                    "file_path": str(path),
                    "existed": existed,
                    "message": f"文件{action}成功",
                },
                ensure_ascii=False,
            )

        file_write_tool = StructuredTool(
            name="file_write",
            description=(
                "将内容写入文件。如果文件已存在则覆盖，不存在则新建。"
                "父目录不存在时会自动创建。适用于创建新文件或完全重写文件内容。"
                "如果只想修改文件中的部分文本，请使用 text_editor 工具。"
            ),
            func=None,
            coroutine=file_write,
            args_schema=FileWriteInput,
        )

        # ---- file_search ----

        def _is_binary_file(file_path: Path) -> bool:
            """通过读取前 8KB 检测文件是否为二进制文件"""
            try:
                with file_path.open("rb") as f:
                    chunk = f.read(8192)
                return b"\x00" in chunk
            except OSError:
                return True

        def _build_include_args(include: str) -> list[str]:
            """将 include 字符串拆分为 ripgrep --glob 参数列表"""
            patterns = [p.strip() for p in include.split(",")]
            args: list[str] = []
            for pat in patterns:
                args.extend(["--glob", pat])
            return args

        async def _search_with_ripgrep(
            search_root: Path, pattern: str, include: Optional[str], limit: int
        ) -> tuple[list[dict], int, bool]:
            """使用 ripgrep 搜索文件内容，返回 (结果列表, 总匹配数, 是否截断)"""
            args = ["-H", "-n", "--no-heading", pattern]
            if include:
                args.extend(_build_include_args(include))
            args.append(str(search_root))

            actual_cmd = f"rg {' '.join(args)}"
            if platform.system() == "Windows":
                actual_cmd = f"chcp 65001 >nul 2>&1 && {actual_cmd}"

            try:
                process = await asyncio.create_subprocess_shell(
                    actual_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    shell=True,
                )
                stdout, _stderr = await asyncio.wait_for(
                    process.communicate(), timeout=60
                )
            except FileNotFoundError:
                raise
            except Exception:
                raise

            if process.returncode == 1:
                return [], 0, False
            if process.returncode != 0:
                raise RuntimeError(f"rg exited with code {process.returncode}")

            output = stdout.decode("utf-8", errors="replace")
            all_matches: list[dict] = []
            current_file = ""
            file_mod_time: float = 0

            for line in output.splitlines():
                if not line:
                    continue

                parts = line.split(":", 2)
                if len(parts) < 3:
                    continue

                file_path_str, line_num_str, line_text = parts
                try:
                    line_num = int(line_num_str)
                except ValueError:
                    continue

                if file_path_str != current_file:
                    current_file = file_path_str
                    try:
                        file_mod_time = os.path.getmtime(file_path_str)
                    except OSError:
                        file_mod_time = 0

                all_matches.append(
                    {
                        "file_path": file_path_str,
                        "line_number": line_num,
                        "line_content": line_text[:500],
                        "_mod_time": file_mod_time,
                    }
                )

            total = len(all_matches)
            truncated = total > limit
            if truncated:
                all_matches = all_matches[:limit]

            all_matches.sort(key=lambda x: x["_mod_time"], reverse=True)
            for item in all_matches:
                item.pop("_mod_time", None)

            return all_matches, total, truncated

        async def _search_with_regex(
            search_root: Path, pattern: str, include: Optional[str], limit: int
        ) -> tuple[list[dict], int, bool]:
            """纯 Python 正则搜索（ripgrep 不可用时的 fallback）"""
            try:
                compiled_re = re.compile(pattern)
            except re.error:
                return [], 0, False

            include_patterns: list[str] = []
            if include:
                include_patterns = [p.strip() for p in include.split(",")]

            results: list[dict] = []
            total_matches = 0

            try:
                for file_path in search_root.rglob("*"):
                    if not file_path.is_file():
                        continue

                    if any(part in SKIP_DIR_NAMES for part in file_path.parts):
                        continue

                    if _is_hidden_path(file_path):
                        continue

                    if include_patterns:
                        if not any(
                            fnmatch.fnmatch(file_path.name, pat)
                            for pat in include_patterns
                        ):
                            continue

                    try:
                        stat_info = file_path.stat()
                    except OSError:
                        continue
                    if stat_info.st_size > MAX_SEARCH_FILE_SIZE:
                        continue

                    if _is_binary_file(file_path):
                        continue

                    try:
                        raw, _encoding = _detect_and_read(file_path)
                    except Exception:
                        continue

                    mod_time = stat_info.st_mtime
                    lines = raw.splitlines()
                    for line_idx, line in enumerate(lines):
                        if compiled_re.search(line):
                            total_matches += 1
                            results.append(
                                {
                                    "file_path": str(file_path),
                                    "line_number": line_idx + 1,
                                    "line_content": line[:500],
                                    "_mod_time": mod_time,
                                }
                            )
                            if len(results) >= limit:
                                results.sort(key=lambda x: x["_mod_time"], reverse=True)
                                for item in results:
                                    item.pop("_mod_time", None)
                                return results, total_matches, True
            except Exception:
                return results, total_matches, False

            results.sort(key=lambda x: x["_mod_time"], reverse=True)
            for item in results:
                item.pop("_mod_time", None)
            return results, total_matches, False

        async def file_search(
            pattern: str,
            path: Optional[str] = None,
            include: Optional[str] = None,
            literal_text: bool = False,
        ) -> str:
            search_root = Path(path).resolve() if path else BASE_DIR

            is_valid, error_msg = _validate_file_path(str(search_root))
            if not is_valid:
                return f"路径校验失败: {error_msg}"

            if not search_root.exists():
                return f"路径不存在: {search_root}"

            if not search_root.is_dir():
                return f"路径不是目录: {search_root}"

            # literal_text 模式：自动转义正则特殊字符
            search_pattern = re.escape(pattern) if literal_text else pattern

            # 正则表达式预编译校验
            try:
                re.compile(search_pattern)
            except re.error as e:
                return f"正则表达式无效: {e}"

            # 优先使用 ripgrep，失败时 fallback 到纯 Python
            results = []
            total_matches = 0
            truncated = False

            rg_available = shutil.which("rg") is not None
            if rg_available:
                try:
                    results, total_matches, truncated = await _search_with_ripgrep(
                        search_root, search_pattern, include, MAX_SEARCH_RESULTS
                    )
                except Exception:
                    results, total_matches, truncated = await _search_with_regex(
                        search_root, search_pattern, include, MAX_SEARCH_RESULTS
                    )
            else:
                results, total_matches, truncated = await _search_with_regex(
                    search_root, search_pattern, include, MAX_SEARCH_RESULTS
                )

            if not results:
                return "No matches found"

            lines: list[str] = [f"Found {total_matches} matches\n"]
            current_file = ""
            for match in results:
                fp = match["file_path"]
                if fp != current_file:
                    if current_file:
                        lines.append("")
                    current_file = fp
                    lines.append(f"{fp}:")
                lines.append(f"  {match['line_number']}: {match['line_content']}")

            if truncated:
                lines.append(
                    f"\n(Results truncated after {MAX_SEARCH_RESULTS} matches. "
                    "Use a more specific path or pattern.)"
                )
            return "\n".join(lines)

        file_search_tool = StructuredTool(
            name="file_search",
            description=(
                "在指定目录中递归搜索文件内容，使用正则表达式匹配。"
                "返回匹配的文件路径、行号和行内容，按文件修改时间降序排列。"
                "适用于在项目中查找特定模式（如函数定义、变量引用、错误信息等）。"
                "单文件内精确定位请用 file_read 读取后再查看。"
                "搜索含特殊字符的纯文本时建议设置 literal_text=true。"
            ),
            func=None,
            coroutine=file_search,
            args_schema=FileSearchInput,
        )

        return [
            shell_tool,
            shell_task_status_tool,
            shell_task_input_tool,
            shell_task_cancel_tool,
            file_read_tool,
            text_editor_tool,
            file_write_tool,
            file_search_tool,
        ]

    async def get_system_prompt_hint(self, node: FlowNode) -> Optional[str]:
        """返回临时文件目录说明和文件工具使用指南，追加到 LLM system_prompt"""
        temp_dir = get_temp_dir()
        current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return (
            "\n\n## Shell 与文件操作\n"
            "你已连接 Shell 执行节点。先用 file_search 在项目中搜索目标，再用 file_read 读取文件内容，用 text_editor 精确替换；创建新文件用 file_write\n"
            "### 输出控制（重要）\n"
            "- 执行命令前先评估可能的输出量，大量输出务必用 | head、| tail、| grep 等管道过滤\n"
            "- 如果命令输出被截断（返回 _truncated 标记），完整内容已自动保存到临时文件，需要时用 file_read 读取\n"
            "- file_read 单次最多读取 100 行，大文件用 offset 参数分段读取\n"
            "- file_search 递归搜索文件内容（正则匹配），跨文件快速定位目标位置\n"
            "- 禁止用 cat 读取大文件，始终使用 file_read\n"
            f"\n临时文件输出目录: `{temp_dir}`，当前时间: {current_time_str}"
        )

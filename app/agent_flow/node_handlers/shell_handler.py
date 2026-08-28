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
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Literal, Optional, Sequence

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import StreamWriter
from pydantic import BaseModel, Field

from app.agent_flow.execution_context import get_execution_context
from app.agent_flow.flow_context import FlowState
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.node_handlers.base_handler import (
    BaseNodeHandler,
    BaseNodeConfig,
    NodeVariable,
)
from app.agent_flow.tool_output_truncate import smart_truncate_output
from app.agent_flow.tools.common import (
    MAX_FILE_SIZE,
    analyze_line_endings as _analyze_line_endings,
    detect_and_read as _detect_and_read,
    detect_dominant_line_ending as _detect_dominant_line_ending,
    normalize_line_endings as _normalize_line_endings,
    validate_file_path as _validate_file_path,
    validate_writable_path as _validate_writable_path,
)
from app.agent_flow.tools.file_read import FileReadService
from app.config.build_utils import BASE_DIR, get_agent_work_dir, get_temp_dir
from app.config.settings import settings
from app.models.flow_node import FlowNode


class ShellNodeConfig(BaseNodeConfig):
    command: str = ""
    timeout: int = 300
    async_wait: int = 8
    default_workdir: str = Field(
        "",
        description=(
            "默认工作目录（绝对路径，相对路径基于项目根目录解析）。"
            "留空则使用 Agent 工作目录；每次调用 shell_executor 的 workdir 参数可临时覆盖此值"
        ),
    )
    output_variables: list[NodeVariable] = [
        NodeVariable(name="stdout", type="string"),
        NodeVariable(name="stderr", type="string"),
        NodeVariable(name="exit_code", type="number"),
    ]


BLOCKED_COMMANDS = {
    # Windows 毁灭性命令
    "format",
    "diskpart",
    "bcdedit",
    "cipher",
    # Linux 毁灭性命令
    "mkfs",
    "mkswap",
    "fdisk",
    "parted",
    "gdisk",
    "shred",
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
    # 6. Windows 用户管理（防止锁死系统）
    r"\bnet\s+user\b",  # Windows 用户管理
    r"\bnet\s+localgroup\b",  # Windows 用户组管理
    # 7. Linux 进程终止（仅保留杀 init 进程的保护）
    r"\bkill\s+(-9\s+)?1\b",  # 杀死 init 进程
    r"\bpkill\s+(-9\s+)?init",  # 杀死 init
    # 8. PowerShell 高危行为（防御性保留：防止命令注入 PS 语法绕过 cmd 检测）
    r"\bFormat-Volume\b",  # 格式化卷
    r"\bClear-Disk\b",  # 清空磁盘
    r"\bInitialize-Disk\b",  # 初始化磁盘
    r"\bRemove-Item\s+[^;\r\n]*-[Rr]ecurse[^;\r\n]*\s[\"']?[A-Za-z]:\\[\"';\s]*$",  # 递归强删盘根
    r"\bRemove-Item\s+[\"']?[A-Za-z]:\\['\"\s;,]*$",  # 删除盘根（无论是否递归）
    r"\bRemove-Item\s+[\"']?(?:~[/\\]?|/)[\"';\s]*$",  # 强删家目录/根目录
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


def _command_targets_data(command: str) -> bool:
    """检测命令是否引用了数据库文件（langgraph_agent.db 及其 WAL/SHM 文件）"""
    return "langgraph_agent.db" in command


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

    if _command_targets_data(command):
        base_cmd = first_word.split("/")[-1].split("\\")[-1]
        if base_cmd not in DATA_READONLY_COMMANDS:
            return False, f"不允许对数据目录执行写操作（仅支持查看），命令: {command}"

    return True, ""


MAX_CONTENT_SIZE = 50 * 1024 * 1024
MAX_SEARCH_RESULTS = 50
MAX_SEARCH_FILE_SIZE = 5 * 1024 * 1024
MAX_LIST_RESULTS = 100

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


def _atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    """原子写入文件：先写临时文件，再替换目标文件，避免写入中断导致文件损坏

    newline="" 禁用平台换行翻译：Windows 文本模式默认会把 \n 写成 \r\n、
    并把内容中已有的 \r\n 损坏成 \r\r\n；关闭后写入字节与内容完全一致，
    行尾风格完全由调用方通过 normalize_line_endings 显式控制。
    """
    fd, tmp_path_str = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
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
    workdir: Optional[str] = Field(
        None,
        description=(
            "本次命令的工作目录。默认使用当前Agent工作目录；相对路径基于该目录解析。"
            "切换目录时使用此参数，不要依赖cd影响后续调用"
        ),
    )


class TextEditInput(BaseModel):
    """文本编辑工具输入参数（精确字符串替换）"""

    file_path: str = Field(..., description="文件绝对路径")
    old_string: str = Field(
        ..., min_length=1, description="要替换的原始文本（必须精确匹配）"
    )
    new_string: str = Field(..., description="替换后的新文本")
    replace_all: bool = Field(False, description="是否替换所有匹配项，默认仅替换第一个")
    dry_run: bool = Field(
        False,
        description=(
            "预览模式：只匹配并返回 match_count/match_lines/diff，不写入文件；"
            "确认无误后去掉此参数执行替换"
        ),
    )


class FileWriteInput(BaseModel):
    """文件写入工具输入参数"""

    file_path: str = Field(..., description="文件绝对路径")
    content: str = Field(..., description="要写入的文件内容（覆盖或新建）")
    line_ending: Literal["auto", "lf", "crlf"] = Field(
        "auto",
        description=(
            "行尾风格：auto（默认）已有文件跟随其主导行尾、新建文件用 LF；"
            "lf/crlf 强制指定"
        ),
    )


class ShellTaskStatusInput(BaseModel):
    """查询后台Shell任务状态"""

    task_id: str = Field(..., description="后台任务ID")
    wait_time: int = Field(
        8,
        ge=8,
        le=120,
        description="等待时间（秒），最小8秒，最大120秒。等待期间会尝试获取最新输出，超时则返回当前状态。",
    )


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
        None, description="搜索路径（目录或单个文件），不传则搜索当前工作目录"
    )
    include: Optional[str] = Field(
        None,
        description="文件类型过滤（如 *.py, *.{ts,tsx}），仅搜索匹配的文件",
    )
    literal_text: bool = Field(
        False,
        description="是否将 pattern 作为纯文本搜索（自动转义正则特殊字符），默认 False",
    )


class ListFilesInput(BaseModel):
    """文件名匹配工具输入参数（glob 语义）"""

    pattern: str = Field(
        "**/*",
        description="glob 文件名匹配模式，如 **/*.py、src/**/*.ts、*.json",
    )
    path: Optional[str] = Field(
        None, description="搜索目录路径，不传则使用当前工作目录"
    )
    include_dirs: bool = Field(
        False,
        description="是否包含目录，默认仅列出文件",
    )


class UploadToFileManagerInput(BaseModel):
    """上传文件到文件管理系统工具输入参数"""

    file_path: str = Field(
        ...,
        description="要导入文件管理的文件路径，相对当前工作目录（也可用绝对路径）",
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


# ---- Windows 命令执行环境（cmd.exe 直传，不再派生 powershell）----
# 弃用 PowerShell 的原因：无信誉父进程隐藏派生 powershell + 动态多行 -Command
# 是杀软（火绒/360 等）行为引擎的高危画像，导致每次执行弹窗拦截；
# cmd 无 .NET 脚本引擎、无内存执行能力，杀软关注度低。
# 代价与对策：cmd 会把换行符当命令分隔符导致截断丢输出——已在 execute_shell
# 入口硬性拒绝含换行的命令并提示改写为单行。

# Windows 下禁止为控制台程序新建窗口（GUI/托盘宿主无控制台时子进程默认弹黑框）；
# 仅影响控制台创建方式，stdout/stderr 管道捕获不受影响。POSIX 该参数必须缺省。
_SUBPROCESS_WINDOW_FLAGS = (
    {"creationflags": subprocess.CREATE_NO_WINDOW}
    if platform.system() == "Windows"
    else {}
)


async def _create_subprocess(
    command: str,
    *,
    stdin: int,
    cwd: Optional[Path] = None,
) -> asyncio.subprocess.Process:
    """跨平台创建子进程。

    统一经 create_subprocess_shell：Windows 解析为 cmd /c <command>（CPython 只在
    命令外包一层引号，cmd 按"剥离首尾引号"规则后内部引号原样保留；不要改回
    exec 直传——list2cmdline 会把内部双引号转义成 \\" ，cmd 不识别反斜杠转义）。
    POSIX: sh -c。
    """
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    return await asyncio.create_subprocess_shell(
        command,
        stdin=stdin,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=str(cwd) if cwd else None,
        **_SUBPROCESS_WINDOW_FLAGS,
    )


def _diagnose_empty_output(task: "BackgroundShellTask", command: str) -> Optional[str]:
    """returncode=0 但 stdout/stderr 双空时生成诊断提示（仅提示，不自动重试）"""
    if task.return_code != 0:
        return None
    if task.stdout.strip() or task.stderr.strip():
        return None
    if not command.strip():
        return None
    hints = ["命令执行成功（退出码 0）但 stdout 与 stderr 均为空。"]
    stripped = command.strip()
    if re.search(r"\$env:[A-Za-z_]", stripped):
        hints.append("检测到 $env:VAR 写法：这是 PowerShell 语法，cmd 中应使用 %VAR%。")
    if re.search(r"\bGet-ChildItem\b|\bWrite-Output\b|\$PSItem", stripped):
        hints.append(
            "检测到 PowerShell cmdlet/变量：当前经 cmd.exe 执行，应使用 cmd 等价命令。"
        )
    hints.append(
        "若预期有输出，请确认命令是否真的会产生输出，或先用 echo 类简单命令验证环境。"
    )
    return " ".join(hints)


_SHELL_ERR_NOT_FOUND = re.compile(
    r"(command not found|is not recognized|无法识别|不是内部或外部命令)", re.IGNORECASE
)
_SHELL_ERR_PERMISSION = re.compile(
    r"(permission denied|access is denied|拒绝访问|operation not permitted|eacces|eperm)",
    re.IGNORECASE,
)


def _classify_shell_error(
    status: str, return_code: Optional[int], stderr: str
) -> Optional[str]:
    """将任务终态归类为结构化错误类型，便于模型按类型决定 retry/fallback/上报

    Returns:
        timeout / not_found / permission_denied / runtime_error；
        运行中或成功返回 None（成功且双空输出由 to_dict 补充 empty_stdout）
    """
    if status == "timeout":
        return "timeout"
    if return_code in (None, 0):
        return None
    if return_code in (127, 9009) or _SHELL_ERR_NOT_FOUND.search(stderr or ""):
        return "not_found"
    if _SHELL_ERR_PERMISSION.search(stderr or ""):
        return "permission_denied"
    return "runtime_error"


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
    old_string: str, new_string: str, max_lines: int = 200, max_line_width: int = 240
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


def _collect_match_lines(raw: str, starts: list[int], max_lines: int = 10) -> list[int]:
    """将匹配起始偏移换算为 1-based 行号，最多返回 max_lines 个"""
    return sorted(raw.count("\n", 0, s) + 1 for s in starts[:max_lines])


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
    username: str = ""
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
        error_type = _classify_shell_error(self.status, self.return_code, self.stderr)
        if error_type is None and self.return_code == 0:
            if not self.stdout.strip() and not self.stderr.strip():
                error_type = "empty_stdout"
        if error_type:
            result["error_type"] = error_type
        return result


_background_tasks: dict[str, BackgroundShellTask] = {}
_task_expire_seconds: int = 300


async def _notify_tool_start(task: BackgroundShellTask) -> None:
    """任务转后台时通过 WS 通知前端"""
    if not task.username:
        return
    try:
        from app.services.ws_manager import ws_manager

        await ws_manager.broadcast_to_user(
            task.username,
            {
                "type": "tool_output_start",
                "data": {
                    "task_id": task.task_id,
                    "tool_name": "shell_executor",
                    "command": task.command,
                    "stdout": _decode_output(bytes(task._stdout_bytes)),
                    "stderr": _decode_output(bytes(task._stderr_bytes)),
                },
            },
        )
    except Exception:
        pass


async def _notify_tool_end(task: BackgroundShellTask) -> None:
    """任务结束时通过 WS 通知前端"""
    if not task.username:
        return
    try:
        from app.services.ws_manager import ws_manager

        await ws_manager.broadcast_to_user(
            task.username,
            {
                "type": "tool_output_end",
                "data": {
                    "task_id": task.task_id,
                    "status": task.status,
                    "return_code": task.return_code,
                    "elapsed_seconds": task.to_dict().get("elapsed_seconds"),
                },
            },
        )
    except Exception:
        pass


async def _read_stream(
    stream: asyncio.StreamReader | None,
    task: BackgroundShellTask,
    stream_name: str,
) -> None:
    """持续读取子进程的 stdout/stderr，累积原始字节到 task 中（最后统一解码避免 UTF-8 截断）"""
    try:
        if not stream:
            return
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
        # cmd 宿主与用户子进程是树状结构，必须整树清杀防止孤儿进程
        await _force_kill_process_tree(process)
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
        await _notify_tool_end(task)


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


def get_running_tasks() -> list[dict]:
    """获取所有运行中和最近完成的后台任务（供 REST API 调用）

    返回全部未过期任务（含终态），供前端轮询将本地 running 任务更新为终态；
    已结束超过 _task_expire_seconds 的任务由 _cleanup_expired_tasks 清理后不再返回。
    """
    _cleanup_expired_tasks()
    result = []
    for task in _background_tasks.values():
        result.append(
            {
                **task.to_dict(),
                "stdout": _decode_output(bytes(task._stdout_bytes)),
                "stderr": _decode_output(bytes(task._stderr_bytes)),
            }
        )
    return result


def get_task_by_id(task_id: str) -> Optional[dict]:
    """获取单个任务详情（供 REST API 调用）"""
    _cleanup_expired_tasks()
    task = _background_tasks.get(task_id)
    if not task:
        return None
    return {
        **task.to_dict(),
        "stdout": _decode_output(bytes(task._stdout_bytes)),
        "stderr": _decode_output(bytes(task._stderr_bytes)),
    }


async def cancel_task_by_id(task_id: str) -> dict:
    """取消后台任务（供 REST API 调用），返回结果 dict"""
    task = _background_tasks.get(task_id)
    if not task:
        return {"success": False, "error": f"任务 {task_id} 不存在或已过期"}
    if task.status != "running":
        return {
            "success": False,
            "error": f"任务已结束（status={task.status}）",
            **task.to_dict(),
        }

    process = task.process
    if not process:
        return {"success": False, "error": "进程引用丢失"}

    if task._monitor_task and not task._monitor_task.done():
        task._monitor_task.cancel()

    if process.stdin and not process.stdin.is_closing():
        try:
            process.stdin.close()
        except Exception:
            pass

    # 必须整树清杀（taskkill /T）：直接 TerminateProcess 仅杀掉 shell 宿主，
    # 其子进程（python/ping 等）会孤儿化继续运行
    await _force_kill_process_tree(process)

    try:
        await asyncio.wait_for(asyncio.shield(process.wait()), timeout=5)
    except (asyncio.TimeoutError, Exception):
        pass

    task.status = "cancelled"
    task.return_code = process.returncode
    task.end_time = datetime.now()

    return {"success": True, "message": "任务已取消", **task.to_dict()}


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
                **_SUBPROCESS_WINDOW_FLAGS,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)
        else:
            import signal

            os.killpg(os.getpgid(pid), signal.SIGKILL)  # type: ignore
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

    _working_dir: Optional[Path] = None
    # file_read 可自动注入的媒体类型集合（由 llm_tool_executor 按模型能力×适配器注入；空则媒体文件按普通文件处理）
    _media_caps: set = set()

    def _resolve_working_dir(self) -> Optional[Path]:
        """解析当前 Shell 执行的工作目录

        优先级：
        1. self._working_dir（由 llm_tool_executor 仅对 Agent 类型注入）
        2. ExecutionContext 中的 flow 信息（Shell 节点执行路径），仅 Agent 类型生效
        3. 返回 None（Flow 类型，保持默认 cwd）
        """
        if self._working_dir is not None:
            return self._working_dir
        ctx = get_execution_context()
        if ctx and ctx.expanded_flow is not None:
            flow_type = ctx.expanded_flow.flow_type
            flow_id = ctx.expanded_flow.id or ctx.flow_id
            if flow_type == "agent" and flow_id:
                return get_agent_work_dir(flow_id)
        return None

    def _configured_workdir(
        self, cfg: BaseNodeConfig
    ) -> tuple[Optional[Path], Optional[str]]:
        """解析节点级 default_workdir 配置

        Returns:
            (目录, 警告信息)。未配置返回 (None, None)；配置非法时返回 (None, 警告)，
            由调用方回退默认目录，警告仅透出到 system_prompt。
        """
        raw = (getattr(cfg, "default_workdir", "") or "").strip()
        if not raw:
            return None, None
        try:
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                candidate = BASE_DIR / candidate
            candidate = candidate.resolve()
        except (OSError, RuntimeError):
            return None, f"default_workdir 无法解析: {raw}"
        if not candidate.is_dir():
            return None, f"default_workdir 目录不存在: {candidate}"
        is_valid, error_msg = _validate_file_path(str(candidate))
        if not is_valid:
            return None, f"default_workdir 校验失败: {error_msg}"
        return candidate, None

    def _effective_working_dir(self, cfg: BaseNodeConfig) -> Optional[Path]:
        """按优先级解析工作目录：节点 default_workdir > Agent 注入 > ExecutionContext"""
        configured_dir, _warn = self._configured_workdir(cfg)
        if configured_dir is not None:
            return configured_dir
        return self._resolve_working_dir()

    def _resolve_tool_working_dir(
        self, workdir: Optional[str], default_dir: Optional[Path] = None
    ) -> Optional[Path]:
        """解析单次 Shell 工具调用的工作目录。"""
        if default_dir is None:
            default_dir = self._resolve_working_dir()
        if workdir is None or not workdir.strip():
            return default_dir

        try:
            candidate = Path(workdir).expanduser()
            if not candidate.is_absolute():
                candidate = (default_dir or Path.cwd()) / candidate
            candidate = candidate.resolve()
        except (OSError, RuntimeError) as e:
            raise ValueError(f"工作目录解析失败: {e}") from e

        is_valid, error_msg = _validate_file_path(str(candidate))
        if not is_valid:
            raise ValueError(f"工作目录校验失败: {error_msg}")
        if not candidate.exists():
            raise ValueError(f"工作目录不存在: {candidate}")
        if not candidate.is_dir():
            raise ValueError(f"工作目录不是目录: {candidate}")
        return candidate

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
            result = await self._execute_shell(
                command, timeout, self._effective_working_dir(cfg)
            )
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

    async def _execute_shell(
        self, command: str, timeout: float, cwd: Optional[Path] = None
    ) -> dict:
        """执行Shell命令（Flow 节点专用，stdin 重定向到 DEVNULL 防止交互阻塞）

        Windows 经 cmd.exe 单行执行，POSIX 经 sh。

        Args:
            command: Shell命令字符串
            timeout: 超时时间（秒）
            cwd: 工作目录（None 时继承服务进程目录）

        Returns:
            包含执行结果的字典
        """
        try:
            process = await _create_subprocess(
                command, stdin=asyncio.subprocess.DEVNULL, cwd=cwd
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                await _force_kill_process_tree(process)
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except (asyncio.TimeoutError, Exception):
                    pass
                raise

            return {
                "stdout": _decode_output(stdout),
                "stderr": _decode_output(stderr),
                "return_code": process.returncode,
                "success": process.returncode == 0,
                "command": command,
                "cwd": str(cwd) if cwd else os.getcwd(),
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
                name = var.get("name", "") if isinstance(var, dict) else var.name
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

    async def get_tool(self, node: FlowNode) -> Sequence[BaseTool]:
        """返回工具列表：Shell执行(异步) + 任务状态查询 + 任务输入 + 文件读取 + 文本编辑 + 文件写入"""
        cfg = self._get_config(node)
        timeout = cfg.timeout
        async_wait = cfg.async_wait

        # 节点级 default_workdir 优先于 Agent 工作目录，供所有工具闭包共用
        configured_dir, configured_dir_warn = self._configured_workdir(cfg)
        base_working_dir = (
            configured_dir
            if configured_dir is not None
            else self._resolve_working_dir()
        )

        system_type = platform.system()
        system_info = f"当前系统: {system_type}"

        # ---- shell_executor ----

        async def execute_shell(
            command: str, workdir: Optional[str] = None
        ) -> str | dict:
            # Windows cmd 会把换行符当命令分隔符，含换行的命令在首个换行处被截断、
            # 输出静默丢失——直接拒绝并要求改写为单行（多条命令用 && 连接）
            if system_type == "Windows" and "\n" in command:
                return {
                    "error": (
                        "Windows 命令必须为单行（禁止裸换行）：cmd.exe 会把换行符当作"
                        "命令分隔符，导致命令在首个换行处被截断、输出被静默丢弃。"
                        "请将多条独立命令用 && 连成单行；多行 Python 代码先用 file_write "
                        "写入 .py 文件后以 python <文件路径> 执行。"
                    ),
                    "success": False,
                    "error_type": "multiline_command",
                }

            is_valid, error_msg = validate_command(command)
            if not is_valid:
                return {
                    "error": error_msg,
                    "success": False,
                    "error_type": "blocked_command",
                }

            try:
                command_working_dir = self._resolve_tool_working_dir(
                    workdir, base_working_dir
                )
            except ValueError as e:
                return {
                    "error": str(e),
                    "success": False,
                    "error_type": "invalid_workdir",
                }

            if command_working_dir is not None and not command_working_dir.exists():
                return {
                    "error": (
                        f"工作目录不存在: {command_working_dir}"
                        "（可能已被删除、移动或权限变更）。"
                        "请确认目录状态，或用 workdir 参数指定其他有效目录。"
                    ),
                    "success": False,
                    "error_type": "working_dir_missing",
                }

            _cleanup_expired_tasks()

            try:
                process = await _create_subprocess(
                    command, stdin=asyncio.subprocess.PIPE, cwd=command_working_dir
                )
            except Exception as e:
                return {
                    "error": f"启动进程失败: {e}",
                    "success": False,
                    "error_type": "spawn_error",
                }

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
                result = {
                    "success": True,
                    "cwd": str(command_working_dir or Path.cwd()),
                    **task.to_dict(),
                }
                note = _diagnose_empty_output(task, command)
                if note:
                    result["note"] = note
                _apply_shell_output_truncation(result, task)
                return result

            # 任务超过 async_wait 秒仍未完成 → 转为后台任务，通知前端
            try:
                from app.config.database import AsyncSessionLocal
                from app.services.global_config_service import global_config_service

                async with AsyncSessionLocal() as db:
                    task.username = (
                        await global_config_service.get_username(db) or "default"
                    )
            except Exception:
                task.username = "default"
            await _notify_tool_start(task)

            result = {
                "success": True,
                "async": True,
                "cwd": str(command_working_dir or Path.cwd()),
                "message": (
                    f"命令仍在后台执行中（task_id: {task.task_id}）。"
                    f"后台任务支持并发：等待期间可继续调用其他工具或启动新任务。"
                    f"建议用 shell_task_status 设置较大 wait_time（60~120秒）阻塞等待完成；"
                    f"用户可在界面查看实时输出。"
                ),
                **task.to_dict(),
            }
            _apply_shell_output_truncation(result, task)
            return result

        ps_hint = (
            (
                "Windows 上经 cmd.exe 执行：命令必须为单行（含换行会被拒绝），"
                "多条独立命令用 && 连接；多行 Python 先写入 .py 文件再执行；"
                "%VAR% 与 curl（Win10+ 自带）直接可用（无 wget，下载用 curl -o）；"
                "无 head/tail，输出过滤用 findstr 或重定向文件。"
            )
            if system_type == "Windows"
            else ""
        )

        shell_tool = StructuredTool(
            name="shell_executor",
            description=(
                f"在受限环境中执行Shell命令（{system_info}）。"
                f"命令执行等待 {async_wait} 秒，未完成则转为后台任务并返回 task_id；"
                f"后台任务支持并发，等待期间可继续其他工具调用或启动新任务。"
                f"用 shell_task_status 的 wait_time 参数（8~120秒）阻塞等待结果。"
                f"用 shell_task_input 向进程发送输入，用 shell_task_cancel 终止任务。"
                f"可用 workdir 指定本次工作目录；返回的 cwd 字段是实际执行目录。"
                f"每次调用均启动独立进程，cd不会影响后续调用，切换目录请传workdir。"
                f"禁止危险命令: rm -rf /, format, Format-Volume, Clear-Disk, dd写入设备, fork炸弹等。"
                f"{ps_hint}"
            ),
            func=None,
            coroutine=execute_shell,
            args_schema=ShellToolInput,
        )

        # ---- shell_task_status ----

        async def query_task_status(task_id: str, wait_time: int = 8) -> str | dict:
            _cleanup_expired_tasks()
            task = _background_tasks.get(task_id)
            if not task:
                return {"error": f"任务 {task_id} 不存在或已过期", "success": False}
            if (
                task.status == "running"
                and task._monitor_task
                and not task._monitor_task.done()
            ):
                await asyncio.wait({task._monitor_task}, timeout=wait_time)
            result = {"success": True, **task.to_dict()}
            if task.stdout or task.stderr:
                _apply_shell_output_truncation(result, task)
            if task.status == "running":
                result["hint"] = (
                    "任务仍在后台运行中。可选择：用更大的 wait_time（最长120秒）再次调用以继续阻塞等待；"
                    "或先处理其他事务稍后再来查询（后台任务支持并发，不会互相阻塞）。"
                    "同时告知用户任务正在后台执行，界面可查看实时输出。"
                )
            else:
                note = _diagnose_empty_output(task, task.command)
                if note:
                    result["note"] = note
            return result

        shell_task_status_tool = StructuredTool(
            name="shell_task_status",
            description=(
                "查询后台Shell任务的执行状态和输出。"
                "当 shell_executor 返回 task_id 时使用此工具获取进度；支持多次调用与多个任务并发查询。"
                "wait_time 参数指定阻塞等待秒数（8~120秒），长任务建议设置较大值一次性等待完成。"
                "返回字段: status(running/completed/failed/timeout), stdout, stderr, return_code, elapsed_seconds。"
                "失败时附带 error_type(timeout/not_found/permission_denied/runtime_error/empty_stdout)，可据此决定重试或换方案。"
            ),
            func=None,
            coroutine=query_task_status,
            args_schema=ShellTaskStatusInput,
        )

        # ---- shell_task_input ----

        async def send_task_input(task_id: str, input_text: str) -> str | dict:
            _cleanup_expired_tasks()
            task = _background_tasks.get(task_id)
            if not task:
                return {"error": f"任务 {task_id} 不存在或已过期", "success": False}
            if task.status != "running":
                return {
                    "error": f"任务已结束（status={task.status}），无法发送输入",
                    "success": False,
                    **task.to_dict(),
                }
            process = task.process
            if not process or not process.stdin or process.stdin.is_closing():
                return {"error": "进程 stdin 已关闭，无法发送输入", "success": False}
            try:
                async with task._stdin_lock:
                    process.stdin.write((input_text + "\n").encode("utf-8"))
                    await process.stdin.drain()
            except Exception as e:
                return {"error": f"发送输入失败: {e}", "success": False}
            return {"success": True, "message": "输入已发送", **task.to_dict()}

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

        async def cancel_task(task_id: str) -> str | dict:
            _cleanup_expired_tasks()
            task = _background_tasks.get(task_id)
            if not task:
                return {"error": f"任务 {task_id} 不存在或已过期", "success": False}
            if task.status != "running":
                return {
                    "error": f"任务已结束（status={task.status}），无法取消",
                    "success": False,
                    **task.to_dict(),
                }
            process = task.process
            if not process:
                return {"error": "进程引用丢失", "success": False}

            if task._monitor_task and not task._monitor_task.done():
                task._monitor_task.cancel()

            if process.stdin and not process.stdin.is_closing():
                try:
                    process.stdin.close()
                except Exception:
                    pass

            # 必须整树清杀（taskkill /T）：仅杀 shell 宿主会孤儿化其子进程
            await _force_kill_process_tree(process)

            try:
                await asyncio.wait_for(asyncio.shield(process.wait()), timeout=5)
            except (asyncio.TimeoutError, Exception):
                pass

            task.status = "cancelled"
            task.return_code = process.returncode
            task.end_time = datetime.now()

            return {"success": True, "message": "任务已取消", **task.to_dict()}

        shell_task_cancel_tool = StructuredTool(
            name="shell_task_cancel",
            description="终止正在运行的后台Shell任务。当命令执行时间过长或不再需要时使用。",
            func=None,
            coroutine=cancel_task,
            args_schema=ShellTaskCancelInput,
        )

        # ---- file_read（实现在 tools/file_read.py，此处仅构造服务并注册）----

        file_read_service = FileReadService(self._media_caps)
        file_read_tool = file_read_service.build_tool()

        # ---- text_editor ----

        async def text_editor(
            file_path: str,
            old_string: str,
            new_string: str,
            replace_all: bool = False,
            dry_run: bool = False,
        ) -> str | dict:
            is_valid, error_msg = _validate_writable_path(file_path)
            if not is_valid:
                return {"error": error_msg, "success": False}

            path = Path(file_path).resolve()
            if not path.exists():
                return {"error": f"文件不存在: {file_path}", "success": False}

            if old_string == new_string:
                return {
                    "error": "old_string 与 new_string 相同，无需替换",
                    "success": False,
                }

            file_size = path.stat().st_size
            if file_size > MAX_FILE_SIZE:
                return {
                    "error": f"文件过大（{file_size} 字节），最大支持 {MAX_FILE_SIZE} 字节",
                    "success": False,
                }

            try:
                raw, encoding = _detect_and_read(path)
            except Exception as e:
                return {"error": f"文件读取失败: {e}", "success": False}

            # 行尾感知：new_string 统一为文件主导行尾，避免引入混合行尾
            dominant = _detect_dominant_line_ending(raw)
            new_string = _normalize_line_endings(new_string, dominant)
            crlf_count, lf_count = _analyze_line_endings(raw)
            if crlf_count and lf_count:
                ending_note = f"文件混用行尾（CRLF×{crlf_count}/LF×{lf_count}）"
            elif crlf_count:
                ending_note = "文件为 CRLF 行尾"
            else:
                ending_note = ""

            match_mode = "exact"
            match_starts: list[int] = [
                m.start() for m in re.finditer(re.escape(old_string), raw)
            ]
            count = len(match_starts)
            tolerant_pattern: Optional[re.Pattern] = None

            if count == 0:
                # 行尾容错匹配：old_string 统一为 \n 后，将换行翻译为 \r?\n 再匹配
                old_norm = _normalize_line_endings(old_string, "\n")
                tolerant_pattern = re.compile(
                    r"\r?\n".join(re.escape(part) for part in old_norm.split("\n"))
                )
                tolerant_matches = list(tolerant_pattern.finditer(raw))
                if tolerant_matches:
                    match_starts = [m.start() for m in tolerant_matches]
                    count = len(match_starts)
                    match_mode = "line_ending_tolerant"
                else:
                    # 末尾换行差异诊断：old_string 尾部换行在文件中不存在
                    stripped = old_norm.rstrip("\n")
                    if stripped != old_norm and stripped in raw:
                        return {
                            "error": (
                                "找到 0 处匹配：old_string 末尾含换行符，但文件中对应文本"
                                "末尾无换行（可能是文件末行）。请去掉 old_string 末尾的"
                                f"换行后重试。{ending_note}"
                            ),
                            "success": False,
                            "match_count": 0,
                        }
                    diag = "找到 0 处匹配：未找到要替换的原始文本（old_string），请检查是否与文件内容完全一致（包括缩进和换行）"
                    if ending_note:
                        diag += (
                            f"。{ending_note}，old_string 的换行风格可能与文件不一致"
                        )
                    return {"error": diag, "success": False, "match_count": 0}

            match_lines = _collect_match_lines(raw, match_starts)

            if dry_run:
                result = {
                    "success": True,
                    "dry_run": True,
                    "file_path": str(path),
                    "match_count": count,
                    "match_mode": match_mode,
                    "match_lines": match_lines,
                    "message": (
                        f"预览模式：找到 {count} 处匹配，未写入文件。"
                        "diff 为替换后将产生的变化；确认无误后去掉 dry_run 参数执行替换"
                    ),
                    "diff": _diff_preview(old_string, new_string),
                }
                if count > 1 and not replace_all:
                    result["warning"] = (
                        "当前匹配多处且未设置 replace_all，实际执行会报错；"
                        "请缩小 old_string 范围或设置 replace_all=True"
                    )
                if match_mode == "line_ending_tolerant":
                    result["note"] = (
                        "old_string 与文件行尾不一致，将按行尾容错匹配完成替换"
                        + (f"；{ending_note}" if ending_note else "")
                        + "。new_string 会统一为文件主导行尾"
                    )
                return result

            if count > 1 and not replace_all:
                suffix = f"（{ending_note}）" if ending_note else ""
                return {
                    "error": (
                        f"找到 {count} 处匹配（未执行替换），请缩小 old_string 范围使其唯一匹配，"
                        f"或设置 replace_all=True 替换所有匹配{suffix}"
                    ),
                    "success": False,
                    "match_count": count,
                    "match_lines": match_lines,
                }

            if match_mode == "exact":
                if replace_all:
                    new_raw = raw.replace(old_string, new_string)
                else:
                    new_raw = raw.replace(old_string, new_string, 1)
            else:
                max_replace = 0 if replace_all else 1
                new_raw, _ = tolerant_pattern.subn(
                    lambda _m: new_string, raw, count=max_replace
                )

            try:
                _atomic_write(path, new_raw, encoding=encoding)
            except Exception as e:
                return {"error": f"文件写入失败: {e}", "success": False}

            replaced_count = count if replace_all else 1
            diff = _diff_preview(old_string, new_string)
            result = {
                "success": True,
                "file_path": str(path),
                "replaced_count": replaced_count,
                "match_mode": match_mode,
                "message": f"成功替换 {replaced_count} 处文本",
                "diff": diff,
            }
            if match_mode == "line_ending_tolerant":
                result["note"] = (
                    "old_string 与文件行尾不一致，已按行尾容错匹配完成替换"
                    + (f"；{ending_note}" if ending_note else "")
                    + "。new_string 已统一为文件主导行尾"
                )
            return result

        text_editor_tool = StructuredTool(
            name="text_editor",
            description=(
                "精确替换文件中的文本。"
                "传入 old_string（要替换的原始文本）和 new_string（替换后的新文本），必须精确匹配。"
                "old_string 需与文件内容完全一致（包括缩进、空格和换行）。"
                "old_string 与文件仅行尾不一致（CRLF/LF 混用）时会自动容错匹配并在结果中声明 match_mode。"
                "如果 old_string 匹配多处且未设置 replace_all，会返回错误提示。"
                "可传 dry_run=True 预览：只匹配不写入，返回 match_count、match_lines"
                "（各匹配行号）和将产生的 diff，适合大范围替换前先确认。"
                "new_string 会自动统一为文件的主导行尾，无需关心行尾风格。"
                "编辑文件前建议先使用 file_read 读取文件内容，确认要替换的文本。"
            ),
            func=None,
            coroutine=text_editor,
            args_schema=TextEditInput,
        )

        # ---- file_write ----

        async def file_write(
            file_path: str,
            content: str,
            line_ending: Literal["auto", "lf", "crlf"] = "auto",
        ) -> str:
            is_valid, error_msg = _validate_writable_path(file_path)
            if not is_valid:
                return f"路径校验失败: {error_msg}"

            path = Path(file_path).resolve()
            existed = path.exists()

            # 行尾处理：auto 时已有文件跟随其主导行尾，新建文件用 LF
            if line_ending == "crlf":
                target_ending = "\r\n"
            elif line_ending == "lf":
                target_ending = "\n"
            elif existed:
                try:
                    existing_raw, _enc = _detect_and_read(path)
                except Exception:
                    existing_raw = ""
                target_ending = (
                    _detect_dominant_line_ending(existing_raw) if existing_raw else "\n"
                )
            else:
                target_ending = "\n"

            content = _normalize_line_endings(content, target_ending)

            content_size = len(content.encode("utf-8"))
            if content_size > MAX_CONTENT_SIZE:
                return f"写入内容过大（{content_size} 字节），最大支持 {MAX_CONTENT_SIZE} 字节"

            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(path, content, encoding="utf-8")
            except Exception as e:
                return f"文件写入失败: {e}"

            action = "覆盖" if existed else "新建"
            ending_label = "CRLF" if target_ending == "\r\n" else "LF"
            return f"文件{action}成功: {path}（行尾 {ending_label}）"

        file_write_tool = StructuredTool(
            name="file_write",
            description=(
                "将内容写入文件。如果文件已存在则覆盖，不存在则新建。"
                "父目录不存在时会自动创建。适用于创建新文件或完全重写文件内容。"
                "如果只想修改文件中的部分文本，请使用 text_editor 工具。"
                "行尾规则：line_ending=auto（默认）时已有文件自动跟随其主导行尾、"
                "新建文件使用 LF；可显式传 lf 或 crlf 强制指定行尾。"
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
            rg_exe = shutil.which("rg")
            if not rg_exe:
                raise FileNotFoundError("ripgrep (rg) 未安装")

            # 直接以 argv 列表执行 rg，不经过 shell（避免引号解析与编码包装问题）
            cli_args = ["-H", "-n", "--no-heading"]
            if include:
                cli_args.extend(_build_include_args(include))
            cli_args.extend([pattern, str(search_root)])

            process = await asyncio.create_subprocess_exec(
                rg_exe,
                *cli_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **_SUBPROCESS_WINDOW_FLAGS,
            )
            stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=60)

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
                file_iter = (
                    [search_root] if search_root.is_file() else search_root.rglob("*")
                )
                for file_path in file_iter:
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
            search_root = (
                Path(path).resolve() if path else (base_working_dir or BASE_DIR)
            )

            is_valid, error_msg = _validate_file_path(str(search_root))
            if not is_valid:
                return f"路径校验失败: {error_msg}"

            if not search_root.exists():
                return f"路径不存在: {search_root}"

            if not search_root.is_dir() and not search_root.is_file():
                return f"路径不是文件或目录: {search_root}"

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
                "在指定路径（目录递归或单个文件）中搜索文件内容，使用正则表达式匹配。"
                "返回匹配的文件路径、行号和行内容，按文件修改时间降序排列。"
                "适用于在项目中查找特定模式（如函数定义、变量引用、错误信息等）。"
                "查看文件完整内容请用 file_read。"
                "搜索含特殊字符的纯文本时建议设置 literal_text=true。"
            ),
            func=None,
            coroutine=file_search,
            args_schema=FileSearchInput,
        )

        # ---- list_files（文件名 glob 匹配） ----

        async def list_files(
            pattern: str = "**/*",
            path: Optional[str] = None,
            include_dirs: bool = False,
        ) -> str:
            search_root = (
                Path(path).resolve() if path else (base_working_dir or BASE_DIR)
            )

            is_valid, error_msg = _validate_file_path(str(search_root))
            if not is_valid:
                return f"路径校验失败: {error_msg}"
            if not search_root.exists():
                return f"路径不存在: {search_root}"
            if not search_root.is_dir():
                return f"路径不是目录: {search_root}"

            matched: list[Path] = []
            try:
                for p in search_root.glob(pattern):
                    if any(part in SKIP_DIR_NAMES for part in p.parts):
                        continue
                    if _is_hidden_path(p):
                        continue
                    if not include_dirs and p.is_dir():
                        continue
                    matched.append(p)
            except Exception as e:
                return f"匹配失败: {e}"

            if not matched:
                return f"No files matched pattern: {pattern}"

            total = len(matched)
            truncated = total > MAX_LIST_RESULTS
            if truncated:
                matched = matched[:MAX_LIST_RESULTS]

            def _rel(p: Path) -> str:
                try:
                    return str(p.relative_to(search_root))
                except ValueError:
                    return str(p)

            matched.sort(key=_rel)

            lines: list[str] = [f"Found {total} files\n"]
            lines.extend(_rel(p) for p in matched)
            if truncated:
                lines.append(
                    f"\n(Results truncated after {MAX_LIST_RESULTS} files. "
                    "Use a more specific pattern.)"
                )
            return "\n".join(lines)

        list_files_tool = StructuredTool(
            name="list_files",
            description=(
                "按文件名 glob 模式递归匹配文件（而非内容搜索）。"
                "适用于查找特定类型文件（如 **/*.py）、了解目录结构、定位配置文件等。"
                "返回相对路径列表，自动跳过 .git/node_modules 等目录。"
                "按文件内容搜索请改用 file_search。"
            ),
            func=None,
            coroutine=list_files,
            args_schema=ListFilesInput,
        )

        # ---- upload_to_file_manager：将工作目录下生成的文件导入文件管理，返回下载链接 ----
        async def upload_to_file_manager(file_path: str) -> dict:
            import mimetypes

            from app.config.database import AsyncSessionLocal
            from app.config.settings import settings
            from app.services.file_service import file_service

            raw_path = (file_path or "").strip()
            if not raw_path:
                return {"success": False, "error": "file_path 不能为空"}
            candidate = Path(raw_path)
            work_dir = base_working_dir
            if not candidate.is_absolute() and work_dir is not None:
                candidate = Path(work_dir) / raw_path
            if not candidate.exists() or not candidate.is_file():
                return {"success": False, "error": f"文件不存在: {file_path}"}
            try:
                content = await asyncio.to_thread(candidate.read_bytes)
            except Exception as e:
                return {"success": False, "error": f"读取文件失败: {e}"}

            max_bytes = settings.max_upload_size * 1024 * 1024
            if len(content) > max_bytes:
                return {
                    "success": False,
                    "error": (
                        f"文件大小 {len(content) / 1024 / 1024:.1f}MB 超过限制"
                        f"（最大 {settings.max_upload_size}MB）"
                    ),
                }

            mime_type = (
                mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
            )
            try:
                async with AsyncSessionLocal() as db:
                    file_obj = await file_service.save_bytes_to_fs(
                        db,
                        content,
                        candidate.name,
                        mime_type,
                        source_type="agent_upload",
                    )
            except Exception as e:
                return {"success": False, "error": f"保存到文件管理失败: {e}"}

            return {
                "success": True,
                "file_id": file_obj.id,
                "file_name": file_obj.original_name,
                "file_size": file_obj.file_size,
                "mime_type": file_obj.mime_type,
                "preview_url": f"/{file_obj.file_path}",
                "download_url": f"/api/file/download/{file_obj.id}",
            }

        upload_to_file_manager_tool = StructuredTool(
            name="upload_to_file_manager",
            description=(
                "将当前工作目录下已生成的文件导入文件管理系统，返回 download_url 供用户下载。"
                "当你用 shell/file_write 等方式生成了需要交给用户的文件（报表、图片、视频、文档等），"
                "调用此工具获取下载链接，并在回复中把 download_url 提供给用户。"
                "file_path 支持相对工作目录或绝对路径。"
            ),
            func=None,
            coroutine=upload_to_file_manager,
            args_schema=UploadToFileManagerInput,
        )

        tool_list = [
            shell_tool,
            shell_task_status_tool,
            shell_task_input_tool,
            shell_task_cancel_tool,
            file_read_tool,
            text_editor_tool,
            file_write_tool,
            file_search_tool,
            list_files_tool,
            upload_to_file_manager_tool,
        ]
        return tool_list

    @classmethod
    def get_tool_info(cls, node: FlowNode) -> list[dict]:
        info = [
            {"name": "shell_executor", "description": "在受限环境中执行Shell命令"},
            {"name": "shell_task_status", "description": "查询后台Shell任务状态"},
            {"name": "shell_task_input", "description": "向后台Shell任务发送输入"},
            {"name": "shell_task_cancel", "description": "取消后台Shell任务"},
            {
                "name": "file_read",
                "description": "读取文件内容（图片/音频/PDF 多模态注入，xlsx/docx 转文本）",
            },
            {"name": "text_editor", "description": "编辑文件内容"},
            {"name": "file_write", "description": "写入文件"},
            {"name": "file_search", "description": "搜索文件"},
            {"name": "list_files", "description": "按文件名查找文件"},
            {
                "name": "upload_to_file_manager",
                "description": "将生成的文件导入文件管理并返回下载链接",
            },
        ]
        return info

    async def get_system_prompt_hint(self, node: FlowNode) -> Optional[str]:
        """返回临时文件目录说明和文件工具使用指南，追加到 LLM system_prompt"""
        cfg = self._get_config(node)
        configured_dir, configured_warn = self._configured_workdir(cfg)
        working_dir = (
            configured_dir
            if configured_dir is not None
            else self._resolve_working_dir()
        )
        temp_dir = get_temp_dir()
        current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ps_compat_hint = (
            (
                "### Windows 命令环境（cmd.exe）\n"
                "- 命令必须为单行：含裸换行的命令会被直接拒绝（cmd 把换行符当命令分隔符，会截断丢输出）。\n"
                "- 多条独立命令用 && 连成单行（前一条失败则不继续）；不看成败的顺序执行用单个 &。\n"
                '- 多行 Python 代码先用 file_write 写入 .py 文件，再 python <文件路径> 执行；单行可用 python -c "..."。\n'
                "- 环境变量用 %VAR%（不是 $env:VAR）；目录列表用 dir；不要使用 PowerShell cmdlet（Get-ChildItem 等）。\n"
                "- 大量输出先过滤（findstr / 重定向到文件后用 file_read 分段读取），不要依赖 head/tail（cmd 没有）。\n"
                "- curl 接口响应常带 UTF-8 BOM：python 解析管道 JSON 禁止 json.load(sys.stdin)，改用 json.loads(sys.stdin.buffer.read().decode('utf-8-sig'))。\n"
                "- 返回的 exit_code 取自最后一个原生命令（如 python/curl/git）；内部命令（dir/echo 等）固定返回 0。\n"
            )
            if platform.system() == "Windows"
            else ""
        )
        filter_hint = (
            "| findstr / 或重定向到文件后用 file_read 读取"
            if platform.system() == "Windows"
            else "| head / | tail / grep"
        )

        lines = [
            "\n\n## Shell 与文件操作\n"
            "你已连接 Shell 执行节点。先用 file_search 在项目中搜索目标，再用 file_read 读取文件内容，用 text_editor 精确替换；创建新文件用 file_write\n"
            "### 输出控制（重要）\n"
            f"- 执行命令前先评估可能的输出量，大量输出务必先过滤（{filter_hint}），或重定向到文件后用 file_read 分段读取\n"
            "- 如果命令输出被截断（返回 _truncated 标记），完整内容已自动保存到临时文件，需要时用 file_read 读取\n"
            f"- file_read 单次最多读取 {settings.tool_output_max_lines} 行，大文件用 offset 参数分段读取，续读位置见返回的提示信息\n"
            "- file_search 搜索文件内容（正则匹配），支持目录递归或单个文件路径\n"
            "- list_files 按文件名 glob 匹配（如 **/*.py），用于查找文件或了解目录结构\n"
            "- 禁止用 cat 读取大文件，始终使用 file_read\n"
            "- 需要查看图片/PDF/音频时直接用 file_read 读本地路径（自动多模态注入下一轮对话）；xlsx/docx 自动转文本读取；不支持网络 URL、视频和 .xls/.doc 旧格式，禁止用 file_read 读其他二进制文件\n"
            "- Shell 每次调用都是独立进程；切换目录时传入 shell_executor 的 workdir 参数，不要依赖 cd 影响后续调用\n"
            "- 长时间任务超过等待秒数会转后台并返回 task_id：后台任务支持并发与多次长阻塞查询（wait_time 最长120秒），"
            "期间可继续执行其他工具调用\n"
            + ps_compat_hint
            + (
                f"\n临时文件输出目录: `{temp_dir}`（7天后自动清理，勿存放重要数据）。"
                "一次性验证/分析脚本（依赖检查、diff 对比、数据抽样等）一律用 file_write "
                "写到该目录（文件名建议 _check/_tmp 前缀），禁止写入项目源码目录或工作目录，"
                "用完无需手动删除"
            )
        ]
        if working_dir is not None:
            lines.append(
                f"默认工作目录: `{working_dir}`，Shell 未传 workdir 时在此目录下执行，文件操作优先使用此目录"
            )
        if configured_warn:
            lines.append(f"⚠️ {configured_warn}，已回退默认工作目录")
        lines.append(f"当前时间: {current_time_str}")
        return "\n".join(lines)

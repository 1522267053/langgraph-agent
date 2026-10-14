"""
Shell 命令执行工具集（ShellToolKit）

从 shell_handler.get_tool 抽出的 5 个工具（shell_executor / shell_task_status /
shell_task_input / shell_task_cancel / shell_wait）+ 后台任务状态机基础设施。

模块级单例说明：
- _background_tasks 必须保持模块级（REST API get_running_tasks 等与 Agent 会话
  共享同一任务表，不能按 Kit 实例隔离）
- validate_command 族被 ssh_handler / browser 等复用，随状态机一并迁入本模块

依赖方向：tools/* → app/utils/*、app/config/*；审批通过注入回调（approval_cb），
本模块不 import node_handlers。
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Literal, Optional, Sequence

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent_flow.tool_output_truncate import smart_truncate_output
from app.constants.timing import SHELL_TASK_EXPIRE_SECONDS
from app.utils.env_refresh import refresh_windows_env

logger = logging.getLogger(__name__)

# 审批回调签名：(tool_name, tool_args, content_for_pattern) -> None/"approved"/"rejected"/"timeout"
ApprovalCallback = Callable[[str, dict, str], Awaitable[Optional[str]]]


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


class FileDeleteInput(BaseModel):
    """文件删除工具输入参数（删除前自动备份，支持回退恢复）"""

    file_path: str = Field(
        ..., description="要删除的文件绝对路径（仅支持文件，不支持目录）"
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


class ShellWaitInput(BaseModel):
    """通用等待工具输入参数"""

    seconds: int = Field(
        10,
        ge=1,
        le=120,
        description="等待秒数（1~120），等待期间不执行任何操作",
    )


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


BLOCKED_COMMANDS = {
    # Windows 毁灭性命令
    "format",
    "diskpart",
    "bcdedit",
    "cipher",
    # PowerShell 系列宿主（禁止 AI 调用，含 pwsh/powershell_ise；
    # validate_command 会剥离 .exe 后缀后比对，故无需重复列出 .exe 变体）
    "powershell",
    "powershell_ise",
    "pwsh",
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
    r"\bdel\b[^|;&]*\*\.",  # 删除通配符文件（覆盖 *.exe / *.txt / *.* 等）
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
    # 4. PowerShell 系列宿主全量拦截（含 .exe 后缀与绝对路径写法；
    #    全命令串扫描，覆盖直接调用与 &&/&/||/| 链式调用）
    r"\bpowershell(?:_ise)?\.exe\b",
    r"\bpwsh\.exe\b",
    r"(?:^|[&|;]\s*)(?:powershell|powershell_ise|pwsh)\b",
    # linux高危行为
    # 1. 毁灭性删除 (强制删除根目录、家目录、所有文件)
    # 末尾用 (?=\s|$) 而非 \b：`/` 不是 word char，原版 `\b` 在 `/` 后永不匹配
    r"\brm\s+(-[rf]+\s+){0,2}/(?=\s|$)",  # rm -rf /
    r"\brm\s+(-[rf]+\s+){0,2}~",  # rm -rf ~
    # 兼容 `rm -rf ./*` / `rm -rf /*` / `rm -rf *`。路径前缀含 . / / / 空白均可
    r"\brm\s+(-[rf]+\s+){0,2}[/.\s]*\*",  # rm -rf * / rm -rf ./* / rm -rf /*
    r"\brm\s+--no-preserve-root",  # 绕过保护机制
    # 2. 磁盘与文件系统破坏 (格式化、底层写入)
    r"\bmkfs\.",  # mkfs.ext4, mkfs.xfs 等
    r"\bdd\s+if=.*\s+of=/dev/",  # dd 写入设备
    r"\bmkswap\b",  # 格式化交换分区
    r">\s*/dev/sd",  # 重定向写入磁盘设备
    r">\s*/dev/hd",  # 重定向写入磁盘设备
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
    # 兼容 sh / bash / /bin/sh / /bin/bash 路径写法
    r"\bnc\b.*-e\s+(?:(?:/bin/)?(ba)?sh)",  # nc 反弹 shell
    r"\bncat\b.*-e\s+(?:(?:/bin/)?(ba)?sh)",  # ncat 反弹 shell
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
    # 补充: 单引号 + -Recurse 在路径后 + 路径是 ~ 或 / 等原版漏过的组合
    r"\bRemove-Item\b[^|;\r\n]*-[Rr]ecurse\b",  # Remove-Item + 任意 -Recurse
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
        # 剥离 .exe 后缀后比对（如 powershell.exe），防止带后缀写法绕过禁止列表
        if base_cmd.lower().endswith(".exe"):
            base_cmd = base_cmd[:-4]
        if base_cmd in BLOCKED_COMMANDS:
            return False, f"命令 '{base_cmd}' 在禁止列表中"

    if _command_targets_data(command):
        base_cmd = first_word.split("/")[-1].split("\\")[-1]
        if base_cmd.lower().endswith(".exe"):
            base_cmd = base_cmd[:-4]
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

# POSIX 下为子进程创建独立会话（等价 setsid）：子进程成为独立进程组长，
# 超时/取消时 os.killpg 只清杀命令树，不会误伤后端自身所在进程组。
# 若缺失此参数，killpg(getpgid(pid)) 会连 uvicorn 一起 SIGKILL，导致整个服务假死。
# Windows 无此参数（POSIX-only），必须缺省。
_SUBPROCESS_SESSION_FLAGS = (
    {} if platform.system() == "Windows" else {"start_new_session": True}
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
    exec 直传——list2cmdline 会把内部双引号转义成 \\\" ，cmd 不识别反斜杠转义）。
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
        **_SUBPROCESS_SESSION_FLAGS,
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
        hints.append(
            "检测到 $env:VAR 写法：这是 PowerShell 语法（已禁用），cmd 中应使用 %VAR%。"
        )
    if re.search(r"\bGet-ChildItem\b|\bWrite-Output\b|\$PSItem", stripped):
        hints.append(
            "检测到 PowerShell cmdlet/变量：powershell/pwsh 已被禁止调用，"
            "应改用 cmd 等价命令。"
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


# 环境热刷新：进程内只自动刷新一次（Windows 安装器改注册表 PATH 后，
# 已运行进程的 os.environ 不更新，导致刚安装的 node/python 找不到）。
# 首次 not_found 时从注册表重读 PATH 并重试；置位后不再重复刷新重试。
_env_refresh_attempted = False


async def _retry_after_env_refresh(command: str) -> Optional[dict]:
    """
    命令未找到时尝试环境热刷新重试（仅首次、仅 Windows 生效）。

    刷新注册表 PATH 后复检命令：若命令变为可用则重新执行一次并返回结果 dict；
    否则返回 None（调用方沿用原错误输出）。非 Windows / 已刷新过 → 直接 None。
    """
    global _env_refresh_attempted
    if _env_refresh_attempted:
        return None
    _env_refresh_attempted = True
    if not refresh_windows_env():
        return None
    # 提取命令首词复检（cmd 场景 stderr 含 "xxx" 不是内部或外部命令）
    first_word = command.strip().split(" ", 1)[0].strip('"')
    if not shutil.which(first_word):
        return None
    logger.info("命令「%s」在环境刷新后可用，自动重试执行", first_word)
    try:
        process = await _create_subprocess(command, stdin=asyncio.subprocess.DEVNULL)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    except Exception as e:
        logger.warning("环境刷新后重试执行失败: %s", e)
        return None
    return {
        "stdout": _decode_output(stdout),
        "stderr": _decode_output(stderr),
        "return_code": process.returncode,
        "success": process.returncode == 0,
        "command": command,
        "note": (
            "检测到命令在刚安装的运行环境中，已自动刷新 PATH 后重试成功"
            if process.returncode == 0
            else "已自动刷新 PATH 后重试（命令已找到，但执行出错）"
        ),
    }


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


@dataclass
class BackgroundShellTask:
    """后台运行的Shell任务"""

    task_id: str
    command: str
    status: str = "running"
    stdout: str = ""
    stderr: str = ""
    return_code: Optional[int] = None
    # 超时终态细分：idle_timeout（连续 timeout 秒无输出）/ max_duration（总时长到上限）；
    # 非 timeout 终态为 None。供 LLM 区分「卡死」与「长任务到寿」
    timeout_reason: Optional[str] = None
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
        if self.timeout_reason:
            result["timeout_reason"] = self.timeout_reason
        error_type = _classify_shell_error(self.status, self.return_code, self.stderr)
        if error_type is None and self.return_code == 0:
            if not self.stdout.strip() and not self.stderr.strip():
                error_type = "empty_stdout"
        if error_type:
            result["error_type"] = error_type
        return result


# 模块级单例：REST API（get_running_tasks 等）与 Agent 会话共享同一任务表
_background_tasks: dict[str, BackgroundShellTask] = {}


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
    idle_timeout: float,
    max_duration: Optional[float] = None,
) -> None:
    """后台监控协程：读 stdout/stderr 并等待进程结束。

    超时判定（双条件，先到先触发）：
    - 空闲超时 idle_timeout：连续 idle_timeout 秒无任何新输出（stdout/stderr
      字节缓冲区长度均无增长）才杀。服务器/长驻进程持续吐日志永不误杀；
      真正卡死的无输出命令照样杀 → timeout_reason="idle_timeout"
    - 最大时长 max_duration（可选）：总运行时长硬上限，到点必杀防进程泄漏
      → timeout_reason="max_duration"
    """
    process = task.process
    if not process:
        task.status = "failed"
        task.end_time = datetime.now()
        return

    try:
        streams = asyncio.gather(
            _read_stream(process.stdout, task, "stdout"),
            _read_stream(process.stderr, task, "stderr"),
            process.wait(),
        )
        deadline = (
            datetime.now() + timedelta(seconds=max_duration) if max_duration else None
        )
        # 空闲计时基准：输出活动（缓冲区长度变化）即重置。
        # 轮询间隔必须远小于 idle_timeout：若一次睡满整个 idle_timeout，
        # 窗口内到达的输出会被「迟到承认」（窗口结束才重置计时器），
        # 导致最坏 2×idle_timeout 才杀（实测 banner 场景 300→600s）
        idle_poll_interval = 1.0
        last_activity = datetime.now()
        out_len = len(task._stdout_bytes)
        err_len = len(task._stderr_bytes)

        while True:
            now = datetime.now()
            if deadline is not None and (deadline - now).total_seconds() <= 0:
                raise TimeoutError("max_duration")

            done, _pending = await asyncio.wait({streams}, timeout=idle_poll_interval)
            if done:
                # gather 整体完成：进程已退出（含异常路径由 gather 结果抛出）
                streams.result()
                break

            # 未完成：检查输出活动，有新字节则重置空闲计时
            new_out = len(task._stdout_bytes)
            new_err = len(task._stderr_bytes)
            if new_out != out_len or new_err != err_len:
                out_len, err_len = new_out, new_err
                last_activity = datetime.now()
                continue

            if (datetime.now() - last_activity).total_seconds() >= idle_timeout:
                raise TimeoutError("idle_timeout")
    except TimeoutError as exc:
        # cmd 宿主与用户子进程是树状结构，必须整树清杀防止孤儿进程
        await _force_kill_process_tree(process)
        task.status = "timeout"
        task.return_code = -1
        task.timeout_reason = str(exc)
    except asyncio.CancelledError:
        # 取消（用户主动 cancel_task）：不杀进程（由取消方负责），仅标记终态
        raise
    finally:
        task.end_time = datetime.now()
        # 正常退出路径（streams.result() 后 break）此前漏设终态，status 停在默认
        # "running"、return_code 停在 None：同步完成的命令会向 LLM 报 status=running
        # 且 stdout 看似为空，误导模型去调 shell_task_status 追查（而任务已被
        # execute_shell 从表中 pop → 报"不存在或已过期"）。此处统一补齐：
        # 仅当仍是 running 且进程确已退出时按返回码落终态，避免覆盖 timeout/
        # cancelled 分支已设的状态，也避免进程未退出（CancelledError）时误判。
        if task.status == "running" and process.returncode is not None:
            task.return_code = process.returncode
            task.status = "completed" if process.returncode == 0 else "failed"
        task.stdout = _decode_output(bytes(task._stdout_bytes))
        task.stderr = _decode_output(bytes(task._stderr_bytes))
        await _notify_tool_end(task)


def _cleanup_expired_tasks() -> None:
    """清理已完成超过 SHELL_TASK_EXPIRE_SECONDS 的任务，释放内存"""
    now = datetime.now()
    expired_ids = []
    for tid, task in _background_tasks.items():
        if (
            task.end_time
            and (now - task.end_time).total_seconds() > SHELL_TASK_EXPIRE_SECONDS
        ):
            expired_ids.append(tid)
    for tid in expired_ids:
        task = _background_tasks.pop(tid, None)
        if task and task._monitor_task and not task._monitor_task.done():
            task._monitor_task.cancel()


def get_running_tasks() -> list[dict]:
    """获取所有运行中和最近完成的后台任务（供 REST API 调用）

    返回全部未过期任务（含终态），供前端轮询将本地 running 任务更新为终态；
    已结束超过 SHELL_TASK_EXPIRE_SECONDS 的任务由 _cleanup_expired_tasks 清理后不再返回。
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

            # 防御兜底：仅当子进程拥有独立进程组（start_new_session）时才整组清杀；
            # 若与后端自身同组，killpg 会连 uvicorn 一起 SIGKILL，必须退回单杀
            pgid = os.getpgid(pid)
            if pgid != os.getpgid(0):
                os.killpg(pgid, signal.SIGKILL)  # type: ignore
            else:
                process.kill()
    except Exception:
        try:
            process.kill()
        except ProcessLookupError:
            pass


class ShellToolKit:
    """Shell 命令执行工具集（5 个工具 + 后台任务状态机）

    由 shell_handler.get_tool 构造，注入运行时依赖（cfg / 工作目录 / 审批回调）。
    """

    def __init__(
        self,
        cfg,
        node_key: str,
        approval_cb: ApprovalCallback,
        base_working_dir: Optional[Path],
        timeout: int,
        async_wait: int,
        system_info: str,
    ):
        self._cfg = cfg
        self._node_key = node_key
        self._approval = approval_cb
        self._base_working_dir = base_working_dir
        self._timeout = timeout
        self._async_wait = async_wait
        self._system_info = system_info

    async def build_tools(self, resolve_workdir: Callable) -> Sequence[StructuredTool]:
        """构造工具列表。resolve_workdir(workdir, default_dir) 由 handler 注入
        （依赖 handler 的路径校验方法），返回单次调用的实际工作目录。"""
        timeout = self._timeout
        async_wait = self._async_wait
        system_info = self._system_info
        base_working_dir = self._base_working_dir
        cfg = self._cfg
        _approval = self._approval

        # ---- shell_executor ----

        async def execute_shell(
            command: str, workdir: Optional[str] = None
        ) -> str | dict:
            # 审批钩子（基类 _check_and_request_approval 统一处理白名单 + 正则）
            approval_result = await _approval(
                "shell_executor", {"command": command, "workdir": workdir}, command
            )
            # None / "approved" 都视为放行，仅 rejected/timeout 时中断执行
            if approval_result not in (None, "approved"):
                return {
                    "error": (
                        f"用户未批准执行命令（{approval_result}）。"
                        "如需继续，请征得用户同意后重新调用。"
                    ),
                    "success": False,
                    "error_type": "approval_rejected",
                }

            # 2026-09-13 决定：所有平台硬拒绝多行命令（Linux sh 原生支持多行但禁止放开，
            # Windows cmd 换行截断——统一策略简化语义）。请改写为单行（多条命令
            # 用 && 连接），多行 Python 代码先用 file_write 写入 .py 文件再执行。
            if "\n" in command:
                return {
                    "error": (
                        "命令必须为单行（禁止裸换行）：请将多条独立命令用 && 连成单行；"
                        "多行 Python 代码先用 file_write 写入 .py 文件后以 python <文件路径> 执行。"
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
                command_working_dir = resolve_workdir(workdir, base_working_dir)
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

            monitor = asyncio.create_task(
                _monitor_process(task, timeout, cfg.max_duration)
            )
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
                # 命令未找到 → 进程内首次时热刷新注册表 PATH 并重试一次
                # （覆盖"运行中安装 Node/Python 后找不到命令"场景，无需重启）
                if result.get("error_type") == "not_found":
                    retried = await _retry_after_env_refresh(command)
                    if retried is not None:
                        retried["cwd"] = str(command_working_dir or Path.cwd())
                        return retried
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
                "禁止调用 powershell/pwsh（含链式与全路径写法，会被直接拦截）。"
            )
            if system_info.endswith("Windows")
            else ""
        )

        shell_tool = StructuredTool(
            name="shell_executor",
            description=(
                f"在受限环境中执行Shell命令（{system_info}）。"
                f"命令执行等待 {async_wait} 秒，未完成则转为后台任务并返回 task_id；"
                f"后台任务支持并发，等待期间可继续其他工具调用或启动新任务。"
                f"超时按空闲判定：连续 {timeout} 秒无任何输出才判超时并整树清杀"
                f"（timeout_reason=idle_timeout），服务器/长驻进程持续输出不会被误杀；"
                f"总时长硬上限默认 1800 秒（半小时，timeout_reason=max_duration），"
                f"节点配置 max_duration=0 可解除限制。"
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
            # 运行中 task.stdout/stderr 仍是空（_monitor_process 的 finally 块
            # 仅在任务结束时才解码填充）。这里用累积的字节缓冲区实时覆盖，
            # 确保运行中也能拿到当前 stdout/stderr（与 get_task_by_id 一致）。
            task.stdout = _decode_output(bytes(task._stdout_bytes))
            task.stderr = _decode_output(bytes(task._stderr_bytes))
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
                "返回字段: status(running/completed/failed/timeout/cancelled), stdout, stderr, return_code, elapsed_seconds, "
                "timeout_reason(timeout 时：idle_timeout=连续无输出超时 / max_duration=总时长到上限)。"
                "失败时附带 error_type(timeout/not_found/permission_denied/runtime_error/empty_stdout)，可据此决定重试或换方案。"
                "运行中（status=running）的 stdout/stderr 也会实时返回——若含交互提示（如"
                "'Enter your name:'/'Password:'/'Continue? [y/n]'），应据此立即调用 shell_task_input 写入回复文本，"
                "避免流程卡在等待输入而误判为超时。"
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
                "典型用法：先用 shell_task_status 查询 task_id 拿到 stdout（含交互提示如"
                "'Enter your name:'/'Password:'/'Are you sure? [y/n]'），"
                "然后用本工具把 LLM 根据上下文生成的回复文本作为 input_text 写入进程 stdin。"
                "input_text 会自动追加换行符（无需手写 \\n）；多个连续输入请分多次调用本工具。"
                "仅当 shell_task_status 返回 status=running 时本工具才会真正写入 stdin。"
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

        # ---- shell_wait ----

        async def wait_seconds(seconds: int = 10) -> dict:
            await asyncio.sleep(seconds)
            return {
                "success": True,
                "waited_seconds": seconds,
                "message": (
                    f"已等待 {seconds} 秒。若在等待后台任务完成，请用 shell_task_status "
                    "查询最新状态；若在等待文件生成，请用 file_read/list_files 确认结果。"
                ),
            }

        shell_wait_tool = StructuredTool(
            name="shell_wait",
            description=(
                "主动等待指定的秒数（1~120秒），期间不执行任何操作。"
                "适用场景：后台任务/编译/下载需要时间推进、等待服务启动就绪、"
                "等待文件生成、网络请求重试间隔等。"
                "用主动等待代替高频空转轮询，减少无效工具调用；"
                "等待结束后再用 shell_task_status / file_read 等工具确认结果。"
                "等待后台任务时建议直接用 shell_task_status 的 wait_time 参数（可阻塞等待）。"
            ),
            func=None,
            coroutine=wait_seconds,
            args_schema=ShellWaitInput,
        )

        return [
            shell_tool,
            shell_task_status_tool,
            shell_task_input_tool,
            shell_task_cancel_tool,
            shell_wait_tool,
        ]

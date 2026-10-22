"""
文件操作工具集（FileToolsKit）

从 shell_handler.get_tool 抽出的 6 个通用文件工具：text_editor / file_write /
file_delete / file_search / list_files / upload_to_file_manager。

与 shell 状态机无强绑定，未来可被其他节点复用。file_read 仍走 FileReadService
（tools/file_read.py），handler 组装时一并注入。

依赖方向：tools/* → app/utils/*、app/config/*；审批通过注入回调（approval_cb），
本模块不 import node_handlers。
"""

import asyncio
import fnmatch
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Awaitable, Callable, Literal, Optional, Sequence

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent_flow.tools.common import (
    MAX_FILE_SIZE,
    analyze_line_endings as _analyze_line_endings,
    decide_writable_path,
    detect_and_read as _detect_and_read,
    detect_dominant_line_ending as _detect_dominant_line_ending,
    get_file_write_lock,
    normalize_line_endings as _normalize_line_endings,
    validate_file_path as _validate_file_path,
)
from app.agent_flow.tools.shell_toolkit import SUBPROCESS_WINDOW_FLAGS
from app.config.build_utils import BASE_DIR
from app.config.settings import settings
from app.services.agent_file_change_service import (
    agent_file_change_service,
    backup_tool_file,
    record_tool_file_change,
)

from app.agent_flow.tools.base_abs_tool import BaseToolKit

logger = logging.getLogger(__name__)

# 审批回调签名：(tool_name, tool_args, content_for_pattern) -> None/"approved"/"rejected"/"timeout"
ApprovalCallback = Callable[[str, dict, str, Optional[str]], Awaitable[Optional[str]]]


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


class FileSearchInput(BaseModel):
    """文件内容搜索工具输入参数"""

    pattern: str = Field(..., description="正则表达式搜索模式")
    path: Optional[str] = Field(
        None, description="搜索路径（目录或单个文件），不传则搜索当前工作目录"
    )
    include: Optional[str] = Field(
        None,
        description=(
            "文件类型过滤，逗号分隔多模式或 {a,b} 花括号（如 *.py, *.{ts,tsx}），"
            "按文件名匹配；两条搜索路径（ripgrep/Python 回退）行为一致"
        ),
    )
    literal_text: bool = Field(
        False,
        description="是否将 pattern 作为纯文本搜索（自动转义正则特殊字符），默认 False",
    )


class ListFilesInput(BaseModel):
    """文件名匹配工具输入参数（glob 语义）"""

    pattern: str = Field(
        "**/*",
        description=(
            "glob 文件名匹配模式，如 **/*.py、src/**/*.ts、*.json；"
            "支持 {a,b} 花括号展开（bash 语义，如 {README.md,AGENTS.md}）；"
            "不支持正则语法（如 ^ $ | ( )）"
        ),
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


def _diff_payload(old_string: str, new_string: str) -> dict[str, str]:
    """生成 text_editor 的 diff 载荷：直接返回新旧字符串，由前端匹配渲染

    不在后端做行级对齐/截断：工具结果展示层（前端 ToolResultViewer）已用
    jsdiff 从新旧文本计算真正的行级 diff；后端截断会让前端 diff 失真，
    且 old/new 本身就是模型传入的工具参数，长度受工具入参约束。
    """
    return {"old_string": old_string, "new_string": new_string}


def _collect_match_lines(raw: str, starts: list[int], max_lines: int = 10) -> list[int]:
    """将匹配起始偏移换算为 1-based 行号，最多返回 max_lines 个"""
    return sorted(raw.count("\n", 0, s) + 1 for s in starts[:max_lines])


# ---- 花括号展开（bash 语义；list_files pattern 与 file_search include 共用）----

# 展开分支数上限：防 {a,b}×{1..9999} 类模式把展开炸成巨量 glob 调用
MAX_BRACE_EXPANSIONS = 64


def _expand_brace_pattern(pattern: str) -> list[str]:
    """对 glob 模式做 {a,b,...} 花括号展开（bash 语义，支持嵌套）。

    Python 的 pathlib.Path.glob 不支持花括号（bash 专属语法），字面匹配
    导致 `{README.md,AGENTS.md}` 永远零命中；本函数在 glob 前手动展开。
    无花括号时原样返回单元素列表，行为与直接 glob 完全一致。

    展开结果超过 MAX_BRACE_EXPANSIONS 时抛 ValueError（防展开爆炸），
    调用方转为用户可读错误。
    """
    # glob 语法无转义机制，'{' 按字面定位
    open_idx = pattern.find("{")
    if open_idx == -1:
        return [pattern]

    # 匹配配对的 '}'（深度计数，支持嵌套）
    depth = 0
    close_idx = -1
    for i in range(open_idx, len(pattern)):
        if pattern[i] == "{":
            depth += 1
        elif pattern[i] == "}":
            depth -= 1
            if depth == 0:
                close_idx = i
                break
    if close_idx == -1:
        # 不配对（如 "{a,b"）：按字面处理，与 glob 字面语义一致
        return [pattern]

    prefix, body, suffix = (
        pattern[:open_idx],
        pattern[open_idx + 1 : close_idx],
        pattern[close_idx + 1 :],
    )

    # 递归展开：先按顶层逗号切分 body（嵌套花括号整体保留给子层处理）
    parts: list[str] = []
    buf: list[str] = []
    sub_depth = 0
    has_top_comma = False
    for ch in body:
        if ch == "{":
            sub_depth += 1
        elif ch == "}":
            sub_depth -= 1
        if ch == "," and sub_depth == 0:
            has_top_comma = True
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))

    # bash 语义：无顶层逗号（如 "{a}"、"{.ssh}"、"{}"）不展开，保持字面，
    # 避免误剥文件名里真实存在的花括号
    if not has_top_comma:
        return [pattern]

    expanded: list[str] = []
    for part in parts:
        # 嵌套花括号在子层继续展开（suffix 中还可能有花括号）
        for sub in _expand_brace_pattern(part + suffix):
            expanded.append(prefix + sub)
            if len(expanded) > MAX_BRACE_EXPANSIONS:
                raise ValueError(
                    f"花括号展开超过 {MAX_BRACE_EXPANSIONS} 个分支，"
                    "请拆分模式或减少分支"
                )
    return expanded


def _split_include_globs(include: str) -> list[str]:
    """将 file_search 的 include 过滤串拆为叶子 glob 列表。

    处理顺序必须是「先花括号展开、再切逗号」：直接按逗号切分会把
    `*.{ts,tsx}` 拦腰截成 `*.{ts` / `tsx}` 两段坏模式。先展开后，
    花括号内的逗号已被展开消化，剩余顶层逗号才是模式分隔符
    （`*.py, *.{ts,tsx}` → `*.py, *.ts` / `*.py, *.tsx` → 三叶子）。
    展开（含上限）与 ripgrep/Python 回退两条路径共用，保证 include
    过滤行为逐字节一致（此前回退路径 fnmatch 不认花括号，与 ripgrep
    主路径行为分裂）。
    """
    globs: list[str] = []
    seen: set[str] = set()
    for expanded in _expand_brace_pattern(include):
        for piece in expanded.split(","):
            piece = piece.strip()
            # 保序去重：*.{ts,tsx} 展开含隐式 *.py，与显式 *.py 重复
            if piece and piece not in seen:
                seen.add(piece)
                globs.append(piece)
    return globs


# ---- 描述单源（唯一维护点；tool_info 与 build_tools 共用）----
FILE_TOOL_DESCRIPTIONS: dict[str, str] = {
    "text_editor": (
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
    "file_write": (
        "将内容写入文件。如果文件已存在则覆盖，不存在则新建。"
        "父目录不存在时会自动创建。适用于创建新文件或完全重写文件内容。"
        "如果只想修改文件中的部分文本，请使用 text_editor 工具。"
        "行尾规则：line_ending=auto（默认）时已有文件自动跟随其主导行尾、"
        "新建文件使用 LF；可显式传 lf 或 crlf 强制指定行尾。"
    ),
    "file_delete": (
        "删除指定文件。删除前会自动备份原文件，因此该删除操作"
        "可以随对话回退一起恢复（用户回退消息时文件会被还原）。"
        "仅支持文件，不支持目录。"
        "需要删除用户可能希望回退的文件时，务必使用此工具而不是 shell 命令"
        "（del/rm 删除的文件无法回退）。"
    ),
    "file_search": (
        "在指定路径（目录递归或单个文件）中搜索文件内容，使用正则表达式匹配。"
        "返回匹配的文件路径、行号和行内容，按文件修改时间降序排列。"
        "适用于在项目中查找特定模式（如函数定义、变量引用、错误信息等）。"
        "查看文件完整内容请用 file_read。"
        "搜索含特殊字符的纯文本时建议设置 literal_text=true。"
    ),
    "list_files": (
        "按文件名 glob 模式递归匹配文件（而非内容搜索）。"
        "适用于查找特定类型文件（如 **/*.py）、了解目录结构、定位配置文件等。"
        "返回相对路径列表，自动跳过 .git/node_modules 等目录。"
        "pattern 支持 {a,b} 花括号展开（如 {README.md,AGENTS.md}，bash 语义）；"
        "多模式命中同一文件时只报一次。按文件内容搜索请改用 file_search。"
    ),
    "upload_to_file_manager": (
        "将当前工作目录下已生成的文件导入文件管理系统，返回 download_url 供用户下载。"
        "当你用 shell/file_write 等方式生成了需要交给用户的文件（报表、图片、视频、文档等），"
        "调用此工具获取下载链接，并在回复中把 download_url 提供给用户。"
        "file_path 支持相对工作目录或绝对路径。"
    ),
}


class FileToolsKit(BaseToolKit):
    """通用文件操作工具集（6 个工具）

    由 shell_handler.get_tool 构造，注入审批回调与默认工作目录。
    """

    def __init__(
        self,
        approval_cb: ApprovalCallback,
        base_working_dir: Optional[Path],
        sandbox_policy: str = "off",
        writable_roots: Optional[list] = None,
    ):
        self._approval = approval_cb
        self._base_working_dir = base_working_dir
        self._sandbox_policy = sandbox_policy
        self._writable_roots = writable_roots

    async def build_tools(self) -> Sequence[StructuredTool]:
        """构造工具列表（协程仅为与 ShellToolKit.build_tools 签名对齐）"""
        base_working_dir = self._base_working_dir
        _approval = self._approval
        sandbox_policy = self._sandbox_policy
        writable_roots = self._writable_roots

        # ---- 写路径三态校验 + 策略分流（三写工具共用）----

        async def _check_write_path(
            tool_name: str, tool_args: dict, file_path: str
        ) -> dict:
            """写路径安全检查 + 策略分流（含审批，三写工具统一入口）。

            决策逻辑已抽到 common.decide_writable_path（唯一真源，shell 重定向
            写共用同一套分流）；本闭包只负责执行审批回调（回调不进 common，
            维持 tools 内依赖方向）。

            Returns: 放行时返回空 dict；否则返回错误 dict。
            """
            decision = decide_writable_path(
                tool_name, file_path, writable_roots, sandbox_policy
            )
            if decision.action == "reject":
                return {"error": decision.error, "success": False}
            if decision.action == "approve":
                approval_result = await _approval(
                    decision.grant_key or tool_name,
                    tool_args,
                    f"[outside_writable_roots] {file_path}",
                    force_reason=decision.force_reason,
                )
                if approval_result in (None, "approved"):
                    return {}
                is_system_path = decision.grant_key or "".endswith("#system_path")
                return {
                    "error": (
                        f"用户未批准写入{'系统路径' if is_system_path else '白名单外路径'}"
                        f"（{approval_result}）"
                    ),
                    "success": False,
                    "error_type": "approval_rejected",
                }
            return {}

        # ---- text_editor ----

        async def text_editor(
            file_path: str,
            old_string: str,
            new_string: str,
            replace_all: bool = False,
            dry_run: bool = False,
        ) -> str | dict:
            # 写路径安全检查 + 审批（唯一审批点，同 file_write；dry_run 只读不审批）
            if not dry_run:
                check_err = await _check_write_path(
                    "text_editor",
                    {
                        "file_path": file_path,
                        "old_string": old_string,
                        "new_string": new_string,
                    },
                    file_path,
                )
                if check_err:
                    return check_err

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

            # [per-file 锁] read → 匹配 → 校验 → backup → 写入全程互斥：
            # asyncio.gather 并行下两个 text_editor 可先后通过下方 mtime/size
            # TOCTOU 校验（各自的 stat 恰落在彼此写入之间），后写者整体覆写先写
            # 者的编辑且双双报成功。锁保证同文件编辑串行；mtime/size 校验保留，
            # 作为跨进程 / 用户在外部编辑器直接改文件的兜底防线。
            async with await get_file_write_lock(str(path)):
                try:
                    raw, encoding = _detect_and_read(path)
                except Exception as e:
                    return {"error": f"文件读取失败: {e}", "success": False}

                # [TOCTOU] 记录 read 时的 mtime，写入前再次 stat 校验：拦截并发修改
                # 场景。同 LLM 在一条 message 内多次 text_editor 同一文件、或多人协作
                # 编辑同一文件时，本地并发 read-modify-write 会导致最后一次写入覆盖
                # 之前所有改动（前面的替换"看起来成功"但实际被覆盖丢失）。st_mtime 精度
                # 在 Windows NTFS 上是 100ns、Linux ext4 是纳秒级，并发 stat 冲突概率
                # 极低；为极端情况（mtime 巧合一致但文件已被覆盖）兜底，加大小校验。
                mtime_before = path.stat().st_mtime
                size_before = path.stat().st_size

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
                            diag += f"。{ending_note}，old_string 的换行风格可能与文件不一致"
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
                        "diff": _diff_payload(old_string, new_string),
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
                    if tolerant_pattern is None:
                        raise RuntimeError("tolerant_pattern为空")
                    new_raw, _ = tolerant_pattern.subn(
                        lambda _m: new_string, raw, count=max_replace
                    )

                # [TOCTOU] 写入前校验 mtime / size：拦截并发修改，避免 read-modify-write
                # 竞态导致本次写入覆盖之前未合并的修改。校验失败直接 abort，不写文件、
                # 不创建备份（避免创建无用备份污染变更追踪记录）。
                try:
                    current_stat = path.stat()
                    if (
                        current_stat.st_mtime != mtime_before
                        or current_stat.st_size != size_before
                    ):
                        return {
                            "error": (
                                "文件已被并发修改（mtime/size 已变化），当前修改基于的旧版本"
                                "可能已过期。请重新读取文件后再次发起替换，避免覆盖其他"
                                "并发修改。"
                            ),
                            "success": False,
                            "concurrent_modification": True,
                            "mtime_before": mtime_before,
                            "mtime_after": current_stat.st_mtime,
                            "size_before": size_before,
                            "size_after": current_stat.st_size,
                        }
                except FileNotFoundError:
                    return {"error": f"文件不存在: {file_path}", "success": False}

                # 文件变更追踪：写前备份原文件（回退消息时恢复），备份失败不阻断写入
                backup_path = await backup_tool_file(path)
                try:
                    _atomic_write(path, new_raw, encoding=encoding)
                except Exception as e:
                    await agent_file_change_service.discard_backup(backup_path)
                    return {"error": f"文件写入失败: {e}", "success": False}

                await record_tool_file_change(
                    tool_name="text_editor",
                    file_path=str(path),
                    change_type="modify",
                    backup_path=backup_path,
                )

                replaced_count = count if replace_all else 1
                diff = _diff_payload(old_string, new_string)
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
            description=FILE_TOOL_DESCRIPTIONS["text_editor"],
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
            # 写路径安全检查 + 审批（唯一审批点：blocked 拒 / outside 按策略
            # 审批或拒 / allowed 放行；系统路径用独立授权键 file_write#system_path）
            check_err = await _check_write_path(
                "file_write", {"file_path": file_path}, file_path
            )
            if check_err:
                return check_err.get("error", "路径校验失败")

            path = Path(file_path).resolve()

            # [per-file 锁] existed 判断 → 行尾探测 → backup → 写入全程互斥：
            # file_write 是盲写覆盖，与并行 text_editor 交错时会整体冲掉对方
            # 刚落盘的编辑（file_write 自身无 mtime 校验，锁是唯一防线）。
            async with await get_file_write_lock(str(path)):
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
                        _detect_dominant_line_ending(existing_raw)
                        if existing_raw
                        else "\n"
                    )
                else:
                    target_ending = "\n"

                content = _normalize_line_endings(content, target_ending)

                content_size = len(content.encode("utf-8"))
                if content_size > MAX_CONTENT_SIZE:
                    return f"写入内容过大（{content_size} 字节），最大支持 {MAX_CONTENT_SIZE} 字节"

                # 文件变更追踪：写前备份原文件（新建文件无需备份），备份失败不阻断写入
                backup_path = await backup_tool_file(path) if existed else None
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    _atomic_write(path, content, encoding="utf-8")
                except Exception as e:
                    await agent_file_change_service.discard_backup(backup_path)
                    return f"文件写入失败: {e}"

                await record_tool_file_change(
                    tool_name="file_write",
                    file_path=str(path),
                    change_type="modify" if existed else "create",
                    backup_path=backup_path,
                )

                action = "覆盖" if existed else "新建"
                ending_label = "CRLF" if target_ending == "\r\n" else "LF"
                return f"文件{action}成功: {path}（行尾 {ending_label}）"

        file_write_tool = StructuredTool(
            name="file_write",
            description=FILE_TOOL_DESCRIPTIONS["file_write"],
            func=None,
            coroutine=file_write,
            args_schema=FileWriteInput,
        )

        # ---- file_delete ----

        async def file_delete(file_path: str) -> str:
            # 写路径安全检查 + 审批（唯一审批点，同 file_write）
            check_err = await _check_write_path(
                "file_delete", {"file_path": file_path}, file_path
            )
            if check_err:
                return check_err.get("error", "路径校验失败")

            path = Path(file_path).resolve()

            # [per-file 锁] 与并行 text_editor/file_write 互斥，避免「边删边写」
            # 时写回已删除路径或删掉刚写入的内容。
            async with await get_file_write_lock(str(path)):
                if not path.exists():
                    return f"文件不存在: {file_path}"
                if path.is_dir():
                    return (
                        f"不支持删除目录: {file_path}（仅支持文件）。"
                        "如需删除目录，请改用 shell 命令（注意：shell 删除的文件无法随对话回退恢复）"
                    )

                # 删除前备份原文件（回退消息时还原），备份失败不阻断删除
                backup_path = await backup_tool_file(path)
                try:
                    await asyncio.to_thread(os.remove, path)
                except Exception as e:
                    await agent_file_change_service.discard_backup(backup_path)
                    return f"文件删除失败: {e}"

                await record_tool_file_change(
                    tool_name="file_delete",
                    file_path=str(path),
                    change_type="delete",
                    backup_path=backup_path,
                )
                return f"文件删除成功（已备份，对话回退时可恢复）: {path}"

        file_delete_tool = StructuredTool(
            name="file_delete",
            description=FILE_TOOL_DESCRIPTIONS["file_delete"],
            func=None,
            coroutine=file_delete,
            args_schema=FileDeleteInput,
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
            """将 include 字符串拆为叶子 glob 的 ripgrep --glob 参数列表。

            逗号拆分 + 花括号展开与 Python 回退路径共用 _split_include_globs，
            保证两条路径的过滤行为一致（rg 自身虽支持花括号，展开后逐字节对齐）。
            """
            args: list[str] = []
            for pat in _split_include_globs(include):
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
                **SUBPROCESS_WINDOW_FLAGS,
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
                include_patterns = _split_include_globs(include)

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

            # include 展开（逗号拆分 + 花括号）前置校验，超上限时明确报错，
            # 避免异常穿透 rg→fallback 链路
            if include:
                try:
                    _split_include_globs(include)
                except ValueError as e:
                    return f"include 过滤串无效: {e}"

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
            description=FILE_TOOL_DESCRIPTIONS["file_search"],
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
            current_pat = pattern  # 坏模式报错定位用：展开前即原模式
            try:
                try:
                    # bash 语义花括号展开：{README.md,AGENTS.md} → 两模式
                    # 逐一 glob；无花括号时单元素，行为与原实现一致
                    expanded_patterns = _expand_brace_pattern(pattern)
                except ValueError as e:
                    return f"模式展开失败: {e}"

                seen: set[str] = set()
                for pat in expanded_patterns:
                    current_pat = pat
                    for p in search_root.glob(pat):
                        key = str(p)
                        if key in seen:
                            continue
                        seen.add(key)
                        if any(part in SKIP_DIR_NAMES for part in p.parts):
                            continue
                        if _is_hidden_path(p):
                            continue
                        if not include_dirs and p.is_dir():
                            continue
                        matched.append(p)
            except Exception as e:
                # 宽捕获兜底（实测坏模式如 "[" 零命中不抛，但 OSError 等
                # 运行环境异常仍需转为可读错误而非工具崩溃）
                return f"匹配失败（模式: {current_pat}）: {e}"

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
            description=FILE_TOOL_DESCRIPTIONS["list_files"],
            func=None,
            coroutine=list_files,
            args_schema=ListFilesInput,
        )

        # ---- upload_to_file_manager：将工作目录下生成的文件导入文件管理，返回下载链接 ----
        async def upload_to_file_manager(file_path: str) -> dict:
            import mimetypes

            from app.config.database import AsyncSessionLocal
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

            # 文件变更追踪：产物文件记为 create，回退消息时随 File 记录一并移除
            stored_abs = BASE_DIR / file_obj.file_path
            await record_tool_file_change(
                tool_name="upload_to_file_manager",
                file_path=str(stored_abs),
                change_type="create",
                file_id=file_obj.id,
            )

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
            description=FILE_TOOL_DESCRIPTIONS["upload_to_file_manager"],
            func=None,
            coroutine=upload_to_file_manager,
            args_schema=UploadToFileManagerInput,
        )

        return [
            text_editor_tool,
            file_write_tool,
            file_delete_tool,
            file_search_tool,
            list_files_tool,
            upload_to_file_manager_tool,
        ]

    @classmethod
    def tool_info(cls, node=None) -> list[dict]:
        """从描述常量派生（单源：与 build_tools 产出逐字节一致）"""
        return [
            {"name": n, "description": d} for n, d in FILE_TOOL_DESCRIPTIONS.items()
        ]

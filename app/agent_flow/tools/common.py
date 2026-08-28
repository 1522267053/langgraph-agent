"""
文件类 LLM 工具共享原语

从 shell_handler.py 迁出的公共部分：路径安全校验、编码探测读取、大小上限。
消费方：tools/file_read.py 及 shell_handler 内的 text_editor/file_write/file_search。
"""

import re
from pathlib import Path

from app.config.build_utils import BASE_DIR

MAX_FILE_SIZE = 50 * 1024 * 1024

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


def validate_file_path(file_path: str) -> tuple[bool, str]:
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


def validate_writable_path(file_path: str) -> tuple[bool, str]:
    """校验文件写入路径是否安全

    在通用路径校验基础上，额外禁止写入项目数据目录（data/ 下存放数据库、向量库等）。
    读取操作不受此限制。

    Returns:
        (是否安全, 错误消息)
    """
    is_valid, error_msg = validate_file_path(file_path)
    if not is_valid:
        return False, error_msg

    path = Path(file_path).resolve()
    data_dir = (BASE_DIR / "data").resolve()
    if path.is_relative_to(data_dir):
        return False, f"不允许写入数据目录: {path}"

    return True, ""


def has_utf16_bom(sample: bytes) -> bool:
    """检测 UTF-16 BOM（FF FE = LE / FE FF = BE）

    PowerShell Out-File/重定向默认产出 UTF-16 LE 文本，含大量 NUL 字节，
    二进制采样会误判；带显式 BOM 的按文本白名单放行。
    """
    return sample.startswith(b"\xff\xfe") or sample.startswith(b"\xfe\xff")


def detect_and_read(path: Path) -> tuple[str, str]:
    """读取文件内容，自动检测编码

    探测顺序：UTF-16 BOM（FF FE/FE FF）→ UTF-8（有 BOM 用 utf-8-sig 剥离）→ GBK 回退。
    返回的编码名反映真实编码：无 BOM 的 UTF-8 返回 "utf-8" 而非 "utf-8-sig"，
    保证调用方以该编码写回时不会凭空追加 BOM（utf-8-sig codec 写入必带 BOM）。

    Returns:
        (文件内容, 使用的编码名称)
    """
    raw = path.read_bytes()
    # 显式 UTF-16 BOM 优先：UTF-16 文本含大量 NUL 字节，必须按文本处理
    if raw.startswith(b"\xff\xfe"):
        return raw.decode("utf-16"), "utf-16"
    if raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16"), "utf-16-be"
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("gbk", errors="replace"), "gbk"


def analyze_line_endings(text: str) -> tuple[int, int]:
    """统计文本行尾分布

    Returns:
        (CRLF 数, 纯 LF 数)。纯 LF 指未被 CRLF 覆盖的 \n。
    """
    crlf = text.count("\r\n")
    lf_only = text.count("\n") - crlf
    return crlf, lf_only


def detect_dominant_line_ending(text: str) -> str:
    """检测文本的主导行尾，各半或无换行时返回 LF

    Returns:
        "\r\n"（CRLF 严格占多数）或 "\n"
    """
    crlf, lf_only = analyze_line_endings(text)
    return "\r\n" if crlf > lf_only else "\n"


def normalize_line_endings(text: str, target: str = "\n") -> str:
    """将文本行尾统一为目标风格（先把 \r\n 与孤立 \r 归一为 \n，再转换）"""
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    if target == "\r\n":
        return unified.replace("\n", "\r\n")
    return unified

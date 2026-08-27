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

    探测顺序：UTF-16 BOM（FF FE/FE FF）→ UTF-8（utf-8-sig 剥 BOM）→ GBK 回退。
    utf-8-sig 对无 BOM 文件行为与 utf-8 完全一致，有 BOM 时自动剥离，
    避免首行混入隐形 \\ufeff 污染模型上下文。

    Returns:
        (文件内容, 使用的编码名称)
    """
    raw = path.read_bytes()
    # 显式 UTF-16 BOM 优先：UTF-16 文本含大量 NUL 字节，必须按文本处理
    if raw.startswith(b"\xff\xfe"):
        return raw.decode("utf-16"), "utf-16"
    if raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16"), "utf-16-be"
    try:
        return raw.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError:
        return raw.decode("gbk", errors="replace"), "gbk"

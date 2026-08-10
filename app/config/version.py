"""
应用版本常量

版本号单一数据源为项目根目录的 version.json。get_version() 每次调用均重新
读取文件，便于运行期热更新版本号而无须重启进程。
文件定位与 .env 一致，使用 build_utils.BASE_DIR：开发环境为项目根目录，
打包环境为 exe 同级目录（由 copy_runtime_files 复制到位）。
version.json 缺失或损坏时回退到内置默认值，保证启动健壮性。
"""

import json

from app.config.build_utils import BASE_DIR

_DEFAULT_VERSION = "0.0.0"
_VERSION_FILE = BASE_DIR / "version.json"


def get_version() -> str:
    """实时读取 version.json 的版本号（每次调用均重新读文件）"""
    try:
        data = json.loads(_VERSION_FILE.read_text(encoding="utf-8"))
        version = data.get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
    except Exception:
        pass
    return _DEFAULT_VERSION


def parse_version(version_str: str) -> tuple[int, int, int]:
    """解析版本号字符串为元组，如 '0.1.0' → (0, 1, 0)"""
    try:
        parts = version_str.strip().split(".")
        return int(parts[0]), int(parts[1]), int(parts[2])
    except (ValueError, IndexError):
        return (0, 0, 0)


def is_newer(latest: str, current: str) -> bool:
    """判断 latest 版本是否比 current 更新"""
    return parse_version(latest) > parse_version(current)

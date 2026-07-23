"""
应用版本常量
"""

__version__ = "1.0.2"


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

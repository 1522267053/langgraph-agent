"""
打包兼容工具
提供运行时路径解析，兼容开发环境、PyInstaller 和 Nuitka 打包环境
"""

import sys
from pathlib import Path


def _is_nuitka() -> bool:
    """判断是否为 Nuitka 编译环境"""
    return "__compiled__" in dir()


def _is_pyinstaller() -> bool:
    """判断是否为 PyInstaller 打包环境"""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def _is_packaged() -> bool:
    """判断是否为任意打包环境（PyInstaller 或 Nuitka）"""
    return _is_pyinstaller() or _is_nuitka()


def get_base_dir() -> Path:
    """
    获取项目根目录（exe 同级目录，用于存放 .env / uploads / logs 等运行时文件）

    - 开发环境: 项目根目录（main.py 所在目录）
    - PyInstaller --onedir: exe 所在目录
    - Nuitka --standalone: exe 所在目录

    Returns:
        Path: 项目根目录
    """
    if _is_packaged():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def get_internal_dir() -> Path:
    """
    获取打包内部资源目录

    - 开发环境: 等同于项目根目录
    - PyInstaller --onedir: _internal/ 目录（datas 收集的文件存放于此）
    - Nuitka --standalone: exe 同级目录（无 _internal/ 子目录，资源与 exe 平级）

    Returns:
        Path: 内部资源目录
    """
    if _is_pyinstaller():
        return Path(sys._MEIPASS)
    return get_base_dir()


def get_frontend_dist_dir() -> Path:
    """
    获取前端构建产物目录

    - 开发环境: 项目根目录下的 frontend/dist/
    - PyInstaller --onedir: _internal/frontend/dist/
    - Nuitka --standalone: frontend/dist/

    Returns:
        Path: 前端 dist 目录
    """
    return get_internal_dir() / "frontend" / "dist"


def get_env_file() -> Path:
    """
    获取 .env 文件路径

    优先查找 exe 同级的 .env 文件，兼容开发和打包环境

    Returns:
        Path: .env 文件路径
    """
    return get_base_dir() / ".env"


BASE_DIR = get_base_dir()

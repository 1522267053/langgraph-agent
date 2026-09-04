"""
打包兼容工具
提供运行时路径解析，兼容开发环境、PyInstaller 和 Nuitka 打包环境
"""

import sys
from pathlib import Path

# ---- 平台常量（sys.platform）----
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform == "linux"
IS_MACOS = sys.platform == "darwin"


def _is_nuitka() -> bool:
    """判断是否为 Nuitka 编译环境"""
    return "__compiled__" in dir()


def _is_pyinstaller() -> bool:
    """判断是否为 PyInstaller 打包环境"""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def _is_packaged() -> bool:
    """判断是否为任意打包环境（PyInstaller 或 Nuitka）"""
    return _is_pyinstaller() or _is_nuitka()


# ---- 运行环境常量 ----
IS_PACKAGED = _is_packaged()
# Windows 打包：启用系统托盘 + 加载页；Linux 打包走普通 uvicorn
IS_WIN_PACKAGED = IS_PACKAGED and IS_WINDOWS


def get_base_dir() -> Path:
    """
    获取项目根目录（exe 同级目录，用于存放 .env / uploads / logs 等运行时文件）

    - 开发环境: 项目根目录（main.py 所在目录）
    - PyInstaller --onedir: exe 所在目录
    - Nuitka --standalone: exe 所在目录

    Returns:
        Path: 项目根目录
    """
    if IS_PACKAGED:
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


def get_workspace_dir() -> Path:
    """获取全局工作空间根目录（BASE_DIR/workspace/），不存在则自动创建"""
    workspace = BASE_DIR / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def get_temp_dir() -> Path:
    """获取全局临时文件目录（BASE_DIR/workspace/temp/），不存在则自动创建

    供 Flow 和 Agent 共用，存放工具输出截断日志等临时文件，定时清理（7天）。
    """
    temp_dir = get_workspace_dir() / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def get_agent_work_dir(flow_id: int) -> Path:
    """获取指定 Agent 的持久化工作目录（BASE_DIR/workspace/agents/{flow_id}/）

    仅 Agent（flow_type="agent"）使用，作为 Shell 执行的 cwd 和 file_search 默认根目录。
    该目录下的文件不会被定时清理，Agent 被删除时也不自动清理。
    """
    work_dir = get_agents_base_dir() / str(flow_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def get_agents_base_dir() -> Path:
    """获取所有 Agent 工作目录的根目录（BASE_DIR/workspace/agents/），不存在则自动创建"""
    agents_dir = get_workspace_dir() / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    return agents_dir


def get_file_snapshots_dir(session_id: int | None = None) -> Path:
    """获取文件快照备份根目录（BASE_DIR/workspace/file_snapshots/），不存在则自动创建

    存放 Agent 对话中工具写文件前的原文件备份（按会话分子目录），
    回退消息时用于还原文件。独立于 workspace/temp，不参与 7 天临时清理，
    由 scheduler 的快照清理任务按保留期统一清理。
    """
    base = get_workspace_dir() / "file_snapshots"
    target = base / str(session_id) if session_id is not None else base
    target.mkdir(parents=True, exist_ok=True)
    return target


def get_update_cache_dir() -> Path:
    """自动更新缓存目录（workspace/update_cache/）

    存放后台下载的整包、回滚备份、状态标记文件。归到 workspace 下统一管理运行时数据，
    避免污染应用根目录。更新时仅替换 exe + _internal，用户数据（data/uploads/.env）不受影响。
    主程序与 updater 通过 zip_path.parent 共享同一目录。
    """
    cache_dir = get_workspace_dir() / "update_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir

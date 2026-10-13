"""
Windows 环境变量热刷新工具

解决问题：程序运行期间安装了 Node.js/Python 等依赖后，安装器只修改注册表
PATH 并广播 WM_SETTINGCHANGE，已运行进程的 os.environ 不会自动更新，
导致子进程继承旧 PATH 报"不是内部或外部命令"，必须重启程序才恢复。

方案：从注册表（HKLM 系统 PATH + HKCU 用户 PATH）重读最新值合并进当前
进程的 os.environ，并广播 WM_SETTINGCHANGE 保持与系统一致。
非 Windows 平台 no-op（Linux 包管理器安装到 /usr/bin 等既有 PATH 目录，
天生无此问题）。
"""

import ctypes
import logging
import os
import shutil
import sys
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# 进程 PATH 基线：首次刷新时记录，用于识别并保留进程内动态新增的段
_original_path: Optional[str] = None
_env_lock = threading.Lock()

_HKLM_ENV_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
_HKCU_ENV_KEY = r"Environment"


def _split_path(path: str) -> list[str]:
    """按平台分隔符切分 PATH，去掉空段。"""
    return [seg for seg in path.split(os.pathsep) if seg.strip()]


def _read_registry_path(root: int, subkey: str) -> str:
    """读取单个注册表环境键的 Path 值，REG_EXPAND_SZ 手动展开变量。"""
    try:
        import winreg

        with winreg.OpenKey(root, subkey) as key:
            value, _ = winreg.QueryValueEx(key, "Path")
        return os.path.expandvars(value)
    except FileNotFoundError:
        return ""
    except OSError as exc:
        logger.debug("读取注册表 PATH 失败（root=%s）: %s", root, exc)
        return ""


def _broadcast_setting_change() -> None:
    """广播 WM_SETTINGCHANGE，让资源管理器等感知环境变化（尽力而为）。"""
    try:
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "Environment",
            SMTO_ABORTIFHUNG,
            1000,
            ctypes.byref(result),
        )
    except Exception as exc:  # noqa: BLE001 广播失败不影响刷新结果
        logger.debug("广播 WM_SETTINGCHANGE 失败: %s", exc)


def refresh_windows_env() -> bool:
    """
    从注册表重读 PATH 并合并进当前进程 os.environ。

    合并顺序：进程内动态新增段（不在启动基线中的）> 系统 PATH > 用户 PATH，
    与 Windows 标准一致且不丢失运行期临时注入的路径。幂等，可重复调用。

    Returns:
        True 表示执行了刷新；False 表示非 Windows 平台（no-op）。
    """
    global _original_path
    if sys.platform != "win32":
        return False
    with _env_lock:
        current = os.environ.get("PATH", "")
        if _original_path is None:
            _original_path = current
        baseline = set(_split_path(_original_path))
        dynamic = [seg for seg in _split_path(current) if seg not in baseline]
        system = _split_path(_read_registry_path(0x80000002, _HKLM_ENV_KEY))  # HKLM
        user = _split_path(_read_registry_path(0x80000001, _HKCU_ENV_KEY))  # HKCU
        merged = list(dict.fromkeys(dynamic + system + user))
        os.environ["PATH"] = os.pathsep.join(merged)
        _broadcast_setting_change()
        logger.info(
            "已从注册表刷新进程 PATH（动态 %d 段 / 系统 %d 段 / 用户 %d 段）",
            len(dynamic),
            len(system),
            len(user),
        )
        return True


def is_command_available(command: str) -> bool:
    """刷新后复检命令是否可用（基于最新 os.environ 的 shutil.which）。"""
    return bool(command.strip()) and shutil.which(command.strip()) is not None

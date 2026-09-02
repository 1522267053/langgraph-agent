"""
文件系统目录浏览 API

供前端工作路径选择器浏览本地目录使用，只读、仅返回目录条目。
"""

import asyncio
import os
import string
from pathlib import Path
from typing import Optional

from fastapi import APIRouter

from app.schemas.base_schema import ApiResponse
from app.schemas.filesystem_schema import (
    DirectoryEntry,
    DirectoryListResponse,
)

# 平台标记（模块级常量，便于测试时 monkeypatch 模拟 POSIX 环境）
_IS_WINDOWS = os.name == "nt"


class FilesystemApi:
    """文件系统目录浏览 API"""

    def __init__(self):
        self.router = APIRouter(prefix="/api/fs", tags=["文件系统"])
        self._register_routes()

    def _register_routes(self):
        """注册所有路由"""

        @self.router.get(
            "/directories",
            response_model=ApiResponse[DirectoryListResponse],
            summary="浏览本地目录",
        )
        async def list_directories(path: Optional[str] = None):
            """列出指定路径下的子目录；path 为空时 Windows 返回盘符列表，
            Linux/macOS 默认浏览用户 home 目录"""
            if not path or not path.strip():
                if not _IS_WINDOWS:
                    # POSIX 无盘符概念，空路径默认落在用户 home 目录
                    try:
                        path = str(Path.home())
                    except RuntimeError:
                        # HOME 无法解析的极端环境，回退根目录
                        path = "/"
                else:
                    drives = await asyncio.to_thread(_list_drives)
                    return ApiResponse.success(
                        data=DirectoryListResponse(
                            path=None, parent=None, directories=drives
                        ),
                        msg="查询成功",
                    )

            target = Path(path.strip()).expanduser()
            if not target.exists():
                return ApiResponse.error(msg="路径不存在")
            if not target.is_dir():
                return ApiResponse.error(msg="路径不是目录")

            resolved = await asyncio.to_thread(target.resolve)
            directories = await asyncio.to_thread(_list_subdirectories, resolved)

            parent = resolved.parent
            # 盘符根目录（D:\）的上级仍是自身，返回空避免前端死循环
            parent_value = None if parent == resolved else str(parent)

            return ApiResponse.success(
                data=DirectoryListResponse(
                    path=str(resolved), parent=parent_value, directories=directories
                ),
                msg="查询成功",
            )


def _list_drives() -> list[DirectoryEntry]:
    """枚举 Windows 盘符"""
    drives = []
    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:\\")
        try:
            if drive.exists():
                drives.append(DirectoryEntry(name=f"{letter}:", path=str(drive)))
        except OSError:
            continue
    return drives


def _list_subdirectories(path: Path) -> list[DirectoryEntry]:
    """列出目录下的子目录，无权限的条目跳过"""
    entries = []
    try:
        for item in path.iterdir():
            try:
                if item.is_dir():
                    entries.append(DirectoryEntry(name=item.name, path=str(item)))
            except OSError:
                # 无权限/特殊系统条目，跳过
                continue
    except (PermissionError, OSError):
        pass
    entries.sort(key=lambda e: e.name.lower())
    return entries


filesystem_api = FilesystemApi()
router = filesystem_api.router

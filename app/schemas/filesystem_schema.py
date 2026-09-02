"""
文件系统目录浏览 Schema
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class DirectoryEntry(BaseModel):
    """目录条目"""

    name: str = Field(..., description="目录名")
    path: str = Field(..., description="目录绝对路径")


class DirectoryListResponse(BaseModel):
    """目录列表响应"""

    path: Optional[str] = Field(
        default=None, description="当前目录绝对路径（盘符列表时为空）"
    )
    parent: Optional[str] = Field(
        default=None, description="上级目录路径（根/盘符列表时为空）"
    )
    directories: List[DirectoryEntry] = Field(
        default_factory=list, description="子目录列表"
    )

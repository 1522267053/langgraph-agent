"""
文件相关数据模型
"""

from typing import Optional
from pydantic import Field
from app.schemas.base_schema import BaseView


class FileCondition(BaseView):
    """文件查询条件"""

    source_type: Optional[str] = Field(None, description="来源类型：flow/agent")
    flow_id: Optional[int] = Field(None, description="所属流程ID")
    original_name: Optional[str] = Field(None, description="原始文件名")
    mime_type: Optional[str] = Field(None, description="MIME类型过滤，如 image/*,.pdf")


class FileBase(BaseView):
    """文件基础模型"""

    source_type: Optional[str] = Field(None, description="来源类型：flow/agent")
    original_name: Optional[str] = Field(None, description="原始文件名")
    file_path: Optional[str] = Field(None, description="存储路径")
    file_type: Optional[str] = Field(None, description="文件扩展名")
    file_size: Optional[int] = Field(None, description="文件大小(字节)")
    mime_type: Optional[str] = Field(None, description="MIME类型")
    flow_id: Optional[int] = Field(None, description="所属流程ID")


class FileUpdate(FileBase):
    """文件更新模型"""

    pass


class FileView(FileBase):
    """文件视图"""

    download_url: Optional[str] = Field(None, description="下载地址")
    preview_url: Optional[str] = Field(None, description="预览地址")

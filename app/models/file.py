"""
文件模型
"""

from typing import Optional

from sqlalchemy import String, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class File(DbBaseModel):
    """文件表模型"""

    __tablename__ = "file"

    flow_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="所属流程ID"
    )
    source_type: Mapped[str | None] = mapped_column(
        String(20), nullable=True, default="", comment="来源类型：flow/agent"
    )
    original_name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="原始文件名"
    )
    file_path: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="存储路径"
    )
    file_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="", comment="文件扩展名"
    )
    file_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="文件大小(字节)"
    )
    mime_type: Mapped[str] = mapped_column(
        String(100), nullable=False, default="", comment="MIME类型"
    )
    preview_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="预览地址（压缩图等）"
    )

    def __repr__(self) -> str:
        return f"<File(id={self.id}, original_name={self.original_name}, source_type={self.source_type})>"

"""
知识库文档模型
"""

from enum import Enum
from typing import Optional
from sqlalchemy import String, Text, Integer, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class DocumentType(str, Enum):
    """文档类型"""

    TXT = "txt"
    MD = "md"
    DOCX = "docx"
    PDF = "pdf"
    XLSX = "xlsx"


class ProcessingStatus(int, Enum):
    """文档处理状态"""

    PENDING = 0
    PROCESSING = 1
    VECTORIZING = 4
    COMPLETED = 2
    FAILED = 3


class KnowledgeDocument(DbBaseModel):
    """
    知识库文档表模型
    """

    __tablename__ = "knowledge_document"

    knowledge_base_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="所属知识库ID"
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="文档标题")
    content: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="文档内容"
    )
    file_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DocumentType.TXT.value,
        comment="文件类型：txt/md/docx/pdf/xlsx",
    )
    file_path: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="原始文件存储路径"
    )
    word_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="字数"
    )
    segment_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="分段数量"
    )
    processing_status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=ProcessingStatus.PENDING.value,
        comment="处理状态：0=待处理，1=处理中，2=已完成，3=失败，4=向量化中",
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        String(1000), nullable=True, comment="处理失败时的错误信息"
    )

    def __repr__(self) -> str:
        return f"<KnowledgeDocument(id={self.id}, title={self.title}, knowledge_base_id={self.knowledge_base_id})>"

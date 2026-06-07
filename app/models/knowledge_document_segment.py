"""
知识库文档分段模型
"""

from typing import Optional
from sqlalchemy import String, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class KnowledgeDocumentSegment(DbBaseModel):
    """
    知识库文档分段表模型

    三层导航第二层：段落，通过 title_id 反向查找标题，
    通过 segment_index 查找相邻段落
    """

    __tablename__ = "knowledge_document_segment"

    document_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, comment="所属文档ID"
    )
    segment_index: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="分段序号"
    )
    title: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="分段标题"
    )
    title_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, index=True, comment="所属标题ID（反向查找标题）"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="分段内容")
    word_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="字数"
    )
    vector_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="向量存储ID"
    )

    def __repr__(self) -> str:
        return f"<KnowledgeDocumentSegment(id={self.id}, document_id={self.document_id}, index={self.segment_index})>"

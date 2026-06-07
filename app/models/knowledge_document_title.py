"""
知识库文档标题索引模型

文档标题层级结构，用于三层知识库导航的第一层
"""

from typing import Optional
from sqlalchemy import String, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class KnowledgeDocumentTitle(DbBaseModel):
    """
    知识库文档标题索引表

    记录文档的标题层级结构，支持从标题定位到段落范围
    """

    __tablename__ = "knowledge_document_title"

    document_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, comment="所属文档ID"
    )
    title_index: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="文档内标题序号（从0开始）"
    )
    level: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="标题级别 1-6，1为最高级"
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False, comment="标题文本")
    start_segment_index: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="该标题下第一个段落的segment_index"
    )
    end_segment_index: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="该标题下最后一个段落的segment_index"
    )
    vector_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="向量存储ID（预留）"
    )

    def __repr__(self) -> str:
        return (
            f"<KnowledgeDocumentTitle(id={self.id}, document_id={self.document_id}, "
            f"index={self.title_index}, level={self.level}, title={self.title})>"
        )

"""
知识沉淀模型

知识库的 AI 知识沉淀层，LLM 在对话中主动将有价值的知识总结保存于此。
搜索时优先查询沉淀层，未命中再检索原始文档。

两张表：
- knowledge_insight: 沉淀主表
- knowledge_insight_segment: 沉淀与文档段落的关联表（多对多）
"""

from sqlalchemy import String, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class KnowledgeInsight(DbBaseModel):
    __tablename__ = "knowledge_insight"

    knowledge_base_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="所属知识库ID"
    )
    question: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="触发问题/查询"
    )
    answer: Mapped[str] = mapped_column(
        Text, nullable=False, comment="AI生成的知识沉淀内容"
    )
    keywords: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="关键词(逗号分隔)"
    )
    vector_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="向量存储ID，格式 insight_{id}"
    )

    def __repr__(self) -> str:
        return (
            f"<KnowledgeInsight(id={self.id}, "
            f"kb_id={self.knowledge_base_id}, "
            f"question={self.question})>"
        )


class KnowledgeInsightSegment(DbBaseModel):
    __tablename__ = "knowledge_insight_segment"

    insight_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, comment="关联的沉淀ID"
    )
    segment_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, comment="关联的文档段落ID"
    )

    def __repr__(self) -> str:
        return (
            f"<KnowledgeInsightSegment(id={self.id}, "
            f"insight_id={self.insight_id}, "
            f"segment_id={self.segment_id})>"
        )

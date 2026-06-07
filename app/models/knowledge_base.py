"""
知识库模型
"""

from enum import Enum
from typing import Optional
from sqlalchemy import String, Text, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class KnowledgeBaseStatus(int, Enum):
    """知识库状态"""

    DISABLED = 0
    ENABLED = 1


class KnowledgeBase(DbBaseModel):
    """
    知识库表模型
    """

    __tablename__ = "knowledge_base"

    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="知识库名称")
    description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="描述"
    )
    status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=KnowledgeBaseStatus.ENABLED.value,
        comment="状态：0=禁用，1=启用",
    )

    def __repr__(self) -> str:
        return f"<KnowledgeBase(id={self.id}, name={self.name}, status={self.status})>"

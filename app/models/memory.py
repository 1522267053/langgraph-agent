"""
记忆模型

三层记忆架构：
- hot（热）：常驻加载到 LLM system_prompt，只存指针索引
- warm（温）：按需向量检索，存储详细结构化记忆
- cold（冷）：低优先级记忆，可被自动升温
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from sqlalchemy import String, Integer, Text, SmallInteger, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class MemoryType(str, Enum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class MemoryCategory(str, Enum):
    DECISION = "decision"
    PREFERENCE = "preference"
    LESSON = "lesson"
    RELATION = "relation"
    EVENT = "event"
    TASK = "task"
    PROFILE = "profile"
    KNOWLEDGE = "knowledge"
    INSTRUCTION = "instruction"
    OTHER = "other"


class Memory(DbBaseModel):
    __tablename__ = "memory"

    agent_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="所属Agent ID"
    )
    memory_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=MemoryType.COLD.value,
        comment="记忆层级：hot(热)/warm(温)/cold(冷)",
    )
    category: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=MemoryCategory.OTHER.value,
        comment="分类：decision/preference/lesson/relation/event/task/other",
    )
    title: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="记忆标题/摘要"
    )
    content: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="记忆内容"
    )
    keywords: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="关键词(逗号分隔)"
    )
    metadata_: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="扩展元数据"
    )
    source_session_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="来源会话ID"
    )
    importance: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=3, comment="重要程度1-5"
    )
    access_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="访问次数（用于自动升温判断）"
    )
    peak_tier: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=MemoryType.COLD.value,
        comment="记忆达到过的最高层级，用于衰减后快速升温",
    )
    vector_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="向量存储ID"
    )
    last_access_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        default=datetime.now,
        nullable=True,
        comment="最后访问时间（用于衰减判断）",
    )

    def __repr__(self) -> str:
        return f"<Memory(id={self.id}, agent_id={self.agent_id}, tier={self.memory_type}, title={self.title})>"

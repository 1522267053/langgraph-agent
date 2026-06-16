"""
日程管理模型

记录个人日程安排，支持分类、优先级、重复规则、状态流转和提醒推送。
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_model import DbBaseModel


class AgendaCategory(str, Enum):
    """日程分类"""

    WORK = "work"
    LIFE = "life"
    STUDY = "study"
    OTHER = "other"


class AgendaPriority(int, Enum):
    """优先级"""

    LOW = 1
    MEDIUM = 2
    HIGH = 3


class AgendaStatus(int, Enum):
    """日程状态"""

    PENDING = 0
    IN_PROGRESS = 1
    COMPLETED = 2


class AgendaRecurrence(str, Enum):
    """重复规则"""

    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class Agenda(DbBaseModel):
    """
    日程表

    存储个人日程信息，通过 creator_name 字段区分不同用户。
    """

    __tablename__ = "agenda"

    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="标题")
    description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="备注"
    )
    start_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="开始时间"
    )
    end_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="结束时间"
    )
    category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=AgendaCategory.OTHER.value,
        comment="分类：work=工作/life=生活/study=学习/other=其他",
    )
    priority: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=AgendaPriority.MEDIUM.value,
        comment="优先级：1=低/2=中/3=高",
    )
    location: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="地点"
    )
    recurrence: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=AgendaRecurrence.NONE.value,
        comment="重复规则：none/daily/weekly/monthly",
    )
    status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=AgendaStatus.PENDING.value,
        comment="状态：0=待办/1=进行中/2=已完成",
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="完成时间"
    )
    color: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, comment="颜色标签"
    )
    remind_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="提醒时间"
    )
    is_reminded: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        comment="是否已推送提醒：0=未推送/1=已推送",
    )

    def __repr__(self) -> str:
        return (
            f"<Agenda(id={self.id}, title={self.title}, "
            f"status={self.status}, start_time={self.start_time})>"
        )

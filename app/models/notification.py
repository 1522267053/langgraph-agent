"""
统一通知中心模型

所有系统通知（执行完成/失败、等待人工输入、日程提醒、定时提醒等）统一落库，
前端铃铛角标展示未读数，通知页可查看历史并跳转关联对象。
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, Integer, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_model import DbBaseModel


class NotificationType(str, Enum):
    """通知类型"""

    EXECUTION_DONE = "execution_done"  # 执行完成（Flow/Agent）
    EXECUTION_FAILED = "execution_failed"  # 执行失败
    EXECUTION_CANCELLED = "execution_cancelled"  # 执行取消
    HUMAN_INPUT = "human_input"  # 等待人工输入（Flow/Agent 停在检查点）
    AGENDA_REMINDER = "agenda_reminder"  # 日程提醒
    REMINDER = "reminder"  # 定时任务提醒


class NotificationRefType(str, Enum):
    """关联对象类型（前端跳转用）"""

    EXECUTION = "execution"  # ref_id = execution_id（Flow 执行记录）
    SESSION = "session"  # ref_id = session_id（Agent 会话）
    AGENDA = "agenda"  # ref_id = agenda_id（日程）
    TASK = "task"  # ref_id = scheduled_task_id（定时任务）


class Notification(DbBaseModel):
    """
    通知表

    单用户桌面软件，不做用户隔离（与 agenda 的 creator_name 模式一致）。
    """

    __tablename__ = "notification"

    type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment=(
            "类型：execution_done=执行完成/execution_failed=执行失败/"
            "execution_cancelled=执行取消/human_input=等待人工输入/"
            "agenda_reminder=日程提醒/reminder=定时提醒"
        ),
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="标题")
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="正文")
    ref_type: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="关联类型：execution/session/agenda/task（前端跳转用）",
    )
    ref_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="关联ID：execution_id/session_id/agenda_id/task_id",
    )
    is_read: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="已读标记：0=未读/1=已读"
    )
    read_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="已读时间"
    )

    def __repr__(self) -> str:
        return (
            f"<Notification(id={self.id}, type={self.type}, "
            f"title={self.title[:20]}, is_read={self.is_read})>"
        )

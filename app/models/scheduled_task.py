"""
定时任务模型

管理流程/Agent的定时触发配置和执行日志。
"""

from enum import Enum
from typing import Optional
from datetime import datetime
from sqlalchemy import String, Integer, SmallInteger, Text, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class ScheduledTaskTargetType(str, Enum):
    """执行目标类型"""

    SELF = "self"
    FLOW = "flow"
    AGENT = "agent"


class ScheduleType(str, Enum):
    """调度类型"""

    CRON = "cron"
    ONCE = "once"


class TriggerType(int, Enum):
    """触发类型"""

    CRON = 1
    MANUAL = 2


class LogStatus(int, Enum):
    """日志状态"""

    RUNNING = 0
    SUCCESS = 1
    FAILED = 2


class ScheduledTask(DbBaseModel):
    """
    定时任务表

    存储定时任务的调度配置，通过管理页面直接管理。
    """

    __tablename__ = "scheduled_task"

    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="任务名称")
    schedule_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ScheduleType.CRON.value,
        comment="调度类型：cron=循环执行, once=执行一次",
    )
    cron_expression: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="Cron表达式（schedule_type=cron 时使用）"
    )
    run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
        comment="单次执行的运行时间（schedule_type=once 时使用）",
    )
    target_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ScheduledTaskTargetType.FLOW.value,
        comment="执行目标类型：flow=流程/agent=Agent",
    )
    target_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=False, comment="目标flow_id"
    )
    input_data: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="预设输入参数JSON"
    )
    is_enabled: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="是否启用：0=禁用, 1=启用"
    )
    next_run_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="下次执行时间"
    )
    last_run_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="上次执行时间"
    )
    last_run_status: Mapped[Optional[int]] = mapped_column(
        SmallInteger, nullable=True, comment="上次执行状态：0=失败, 1=成功"
    )

    def __repr__(self) -> str:
        return (
            f"<ScheduledTask(id={self.id}, name={self.name}, "
            f"schedule_type={self.schedule_type}, enabled={self.is_enabled})>"
        )


class ScheduledTaskLog(DbBaseModel):
    """
    定时任务执行日志表

    记录每次定时任务触发执行的详细信息。
    """

    __tablename__ = "scheduled_task_log"

    task_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="关联定时任务ID"
    )
    execution_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="关联flow_execution.id（Flow目标时）"
    )
    session_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="关联agent_session.id（Agent目标时）"
    )
    agent_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="目标Agent的flow_id"
    )
    status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=LogStatus.RUNNING.value,
        comment="状态：0=运行中, 1=成功, 2=失败",
    )
    trigger_type: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=TriggerType.CRON.value,
        comment="触发类型：1=定时触发, 2=手动触发",
    )
    start_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, comment="开始时间"
    )
    end_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="结束时间"
    )
    duration_ms: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="执行耗时（毫秒）"
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="错误信息"
    )
    input_snapshot: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="输入参数快照"
    )

    def __repr__(self) -> str:
        return (
            f"<ScheduledTaskLog(id={self.id}, task_id={self.task_id}, "
            f"status={self.status})>"
        )

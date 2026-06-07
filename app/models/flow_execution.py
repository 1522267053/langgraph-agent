"""
流程执行记录模型
"""

from enum import Enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, SmallInteger, Integer, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class ExecutionStatus(int, Enum):
    """执行状态"""

    PENDING = 0
    RUNNING = 1
    SUCCESS = 2
    FAILED = 3
    CANCELLED = 4
    WAITING_HUMAN = 5


class FlowExecution(DbBaseModel):
    """
    流程执行记录表模型

    继承 DbBaseModel，自动拥有：
    - id, creator_id, creator_type, creator_name, create_time
    - modifier_id, modifier_type, modifier_name, modify_time
    - is_delete
    """

    __tablename__ = "flow_execution"

    flow_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="流程ID")
    status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=ExecutionStatus.PENDING.value,
        comment="状态：0=待执行，1=执行中，2=成功，3=失败，4=已取消，5=等待人工输入",
    )
    input_data: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="输入数据(JSON)"
    )
    output_data: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="输出数据(JSON)"
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="错误信息"
    )
    start_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="开始时间"
    )
    end_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="结束时间"
    )

    wait_for: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="等待类型：human_input/tool_result"
    )
    wait_data: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="等待数据详情"
    )
    human_inputs: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="已收到的人工输入"
    )
    files: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="附件文件信息"
    )

    def __repr__(self) -> str:
        return f"<FlowExecution(id={self.id}, flow_id={self.flow_id}, status={self.status})>"

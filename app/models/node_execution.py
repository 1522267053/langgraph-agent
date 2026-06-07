"""
节点执行记录模型
"""

from enum import Enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, SmallInteger, Integer, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class NodeExecutionStatus(int, Enum):
    """节点执行状态"""

    PENDING = 0
    RUNNING = 1
    SUCCESS = 2
    FAILED = 3
    SKIPPED = 4
    CANCELLED = 5


class NodeExecution(DbBaseModel):
    """
    节点执行记录表模型

    继承 DbBaseModel，自动拥有：
    - id, creator_id, creator_type, creator_name, create_time
    - modifier_id, modifier_type, modifier_name, modify_time
    - is_delete
    """

    __tablename__ = "node_execution"

    flow_execution_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="流程执行记录ID"
    )
    node_key: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="节点唯一标识"
    )
    node_type: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="节点类型"
    )
    node_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="节点名称"
    )
    status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=NodeExecutionStatus.PENDING.value,
        comment="状态：0=待执行，1=执行中，2=成功，3=失败，4=跳过，5=已取消",
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
    execution_steps: Mapped[Optional[list]] = mapped_column(
        JSON, nullable=True, comment="执行步骤记录(JSON)"
    )

    def __repr__(self) -> str:
        return f"<NodeExecution(id={self.id}, node_key={self.node_key}, status={self.status})>"

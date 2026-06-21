"""
Webhook 调用记录模型

每次 Webhook 触发时创建一条记录，统一关联 Agent 会话或 Flow 执行记录。
外部系统可通过免认证查询接口按 token 回查调用历史和消息。
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, SmallInteger, Integer, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_model import DbBaseModel
from app.models.flow_execution import ExecutionStatus


class WebhookCallRecord(DbBaseModel):
    """Webhook 调用记录"""

    __tablename__ = "webhook_call_record"

    webhook_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="关联 webhook_config.id"
    )
    flow_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="关联 flow.id（冗余）"
    )
    ref_type: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, comment="引用类型：session(Agent) / execution(Flow)"
    )
    ref_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="引用ID：agent_session.id 或 flow_execution.id"
    )
    input_data: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="本次触发输入数据快照"
    )
    status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=ExecutionStatus.PENDING.value,
        comment="状态：0=待执行，1=执行中，2=成功，3=失败，4=已取消",
    )
    output_data: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="输出数据"
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="错误信息"
    )
    callback_status: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, comment="回调状态：pending/sent/failed/skipped"
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="触发时间"
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="完成时间"
    )

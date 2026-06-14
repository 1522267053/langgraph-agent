"""
Webhook 配置模型

允许外部系统通过 HTTP POST 触发流程执行。
每个 Webhook 关联一个流程，通过唯一 token 进行 URL 认证。
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, JSON, SmallInteger, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_model import DbBaseModel


class WebhookConfig(DbBaseModel):
    """Webhook 配置"""

    __tablename__ = "webhook_config"

    flow_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="关联流程ID")

    name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="Webhook 名称"
    )

    token: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, comment="唯一令牌（URL 认证）"
    )

    description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Webhook 描述"
    )

    input_config: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="默认输入参数模板"
    )

    callback_url: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="执行完成后的回调 URL"
    )

    is_enabled: Mapped[int] = mapped_column(
        SmallInteger, default=1, nullable=False, comment="是否启用：0=禁用，1=启用"
    )

    call_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="调用次数"
    )

    last_call_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="最后调用时间"
    )

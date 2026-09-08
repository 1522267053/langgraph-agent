"""
AI 供应商连接模型

存储用户接入的 AI 供应商连接（API Key、Base URL、默认模型等），
支持多供应商并存，其中一条标记为全局默认连接。
"""

from typing import Optional

from sqlalchemy import String, Text, SmallInteger, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_model import DbBaseModel


class AIProviderConnection(DbBaseModel):
    """AI 供应商连接表

    每条记录代表一个已接入的供应商配置（含 API Key）。
    is_default=1 的记录为全局默认连接，是 global_config 中
    default_provider/default_api_key/default_model/default_base_url/context_length
    的替代数据源。provider_id 通过应用层关联 AIProvider.provider_id（非外键）。
    """

    __tablename__ = "ai_provider_connection"

    provider_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="供应商标识（关联 AIProvider.provider_id）",
    )
    provider_name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="供应商名称（冗余展示）"
    )
    api_key: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="API Key"
    )
    base_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="API 地址覆盖（空则回退 AIProvider.api_url）",
    )
    default_model: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True, comment="该连接的默认模型"
    )
    context_length: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="默认模型上下文窗口（token 数）"
    )
    is_default: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        comment="是否全局默认连接：0=否，1=是（应用层保证全局唯一）",
    )
    is_enabled: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, comment="是否启用：0=禁用，1=启用"
    )
    remark: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="备注"
    )

    def __repr__(self) -> str:
        return (
            f"<AIProviderConnection(id={self.id}, "
            f"provider_id={self.provider_id}, is_default={self.is_default})>"
        )

"""
全局配置模型

存储用户全局配置（API Key、模型、供应商等）
"""

from typing import Optional
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class GlobalConfig(DbBaseModel):
    """
    全局配置表

    以 key-value 形式存储配置项，如：
    - default_provider: 默认供应商
    - default_api_key: 默认 API Key
    - default_model: 默认模型
    - default_base_url: 默认 Base URL
    - initialized: 是否完成初始化 ("true"/"false")
    """

    __tablename__ = "global_config"

    key: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, comment="配置键"
    )
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="配置值")
    description: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="配置说明"
    )

    def __repr__(self) -> str:
        return f"<GlobalConfig(key={self.key})>"

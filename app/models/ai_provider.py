"""
AI 供应商模型

存储从 models.dev 同步的 AI 供应商元数据
"""

from typing import Optional
from sqlalchemy import String, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class AIProvider(DbBaseModel):
    """AI 供应商表

    存储供应商基本信息和适配器类型，数据来源于 models.dev/api.json
    """

    __tablename__ = "ai_provider"

    provider_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        comment="供应商标识",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="供应商名称")
    api_url: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="API 地址"
    )
    doc_url: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="文档地址"
    )
    env_vars: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="所需环境变量列表"
    )
    npm_package: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="npm 包名"
    )
    adapter_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="openai_compatible",
        comment="适配器类型：openai_compatible 或 anthropic",
    )

    def __repr__(self) -> str:
        return f"<AIProvider(provider_id={self.provider_id}, name={self.name})>"

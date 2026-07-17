"""
AI 模型模型

存储从 models.dev 同步的 AI 模型元数据
"""

from typing import Optional
from sqlalchemy import String, Text, JSON, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class AIModel(DbBaseModel):
    """AI 模型表

    存储模型能力、限制、费用等元数据，数据来源于 models.dev/api.json
    provider_id 通过应用层关联 AIProvider.provider_id（非外键）
    """

    __tablename__ = "ai_model"

    model_id: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        index=True,
        comment="模型标识（如 deepseek-v4-pro，跨供应商可能重复）",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="模型名称")
    description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="模型描述"
    )
    provider_id: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="供应商标识（关联 AIProvider.provider_id）"
    )
    modalities: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="输入输出模态"
    )
    limits: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="上下文限制（context/input/output）"
    )
    cost: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="费用（input/output/cache_read/cache_write）"
    )
    reasoning: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="是否支持推理：0=否，1=是"
    )
    tool_call: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="是否支持函数调用：0=否，1=是"
    )
    temperature: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="是否支持温度：0=否，1=是"
    )
    attachment: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="是否支持附件：0=否，1=是"
    )
    open_weights: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="是否开源权重：0=否，1=是"
    )
    is_experimental: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="是否实验性：0=否，1=是"
    )
    structured_output: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        comment="是否支持结构化输出：0=否，1=是",
    )
    reasoning_options: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="推理选项配置"
    )
    knowledge: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, comment="知识截止日期"
    )
    release_date: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, comment="发布日期"
    )
    last_updated: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, comment="最后更新日期"
    )
    family: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="模型家族"
    )
    status: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, comment="模型状态"
    )

    def __repr__(self) -> str:
        return f"<AIModel(model_id={self.model_id}, provider_id={self.provider_id})>"

"""Token 使用记录模型"""

from typing import Optional
from sqlalchemy import String, Integer, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class TokenUsage(DbBaseModel):
    """Token 使用记录表

    每次 LLM 调用完成后写入一条记录，统一存储 token 用量。
    支持按模型、Provider、时间维度统计。
    """

    __tablename__ = "token_usage"

    source_type: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="来源类型：flow/agent"
    )
    source_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="来源ID：flow_execution.id 或 agent_session.id"
    )
    node_key: Mapped[str] = mapped_column(Text, nullable=False, comment="节点标识")

    model: Mapped[str] = mapped_column(
        String(255), nullable=False, default="", comment="模型名称"
    )
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False, default="", comment="供应商标识"
    )

    prompt_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="输入token数"
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="输出token数"
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="总token数"
    )
    cache_read_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="缓存读取token数"
    )
    cache_write_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="缓存写入token数"
    )
    reasoning_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="推理/thinking token数"
    )
    usage_metadata: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="完整 usage_metadata JSON（兜底）"
    )

    def __repr__(self) -> str:
        return (
            f"<TokenUsage(id={self.id}, source={self.source_type}, "
            f"model={self.model}, total={self.total_tokens})>"
        )

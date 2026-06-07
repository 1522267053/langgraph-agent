"""
对话消息模型
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, Integer, JSON, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class ConversationMessage(DbBaseModel):
    """
    对话消息表模型

    存储流程执行过程中的对话历史，支持多轮工具调用和人工协助
    """

    __tablename__ = "conversation_message"

    execution_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, comment="执行记录ID"
    )
    node_key: Mapped[str] = mapped_column(Text, nullable=False, comment="节点标识")

    role: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="消息角色：system/user/assistant/tool"
    )
    thinking: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="思考内容"
    )
    content: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="消息内容"
    )

    tool_calls: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="工具调用请求列表（assistant角色）"
    )
    tool_call_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="工具调用ID（tool角色）"
    )
    name: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="工具名称（tool角色）"
    )
    status: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        default="success",
        comment="工具执行状态：success/error（tool角色）",
    )

    prompt_tokens: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="输入token数"
    )
    completion_tokens: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="输出token数"
    )
    total_tokens: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="总token数"
    )
    sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="消息序号（用于排序）"
    )
    create_time: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, nullable=False, comment="创建时间"
    )

    __table_args__ = (Index("idx_execution_node_seq", "execution_id"),)

    def __repr__(self) -> str:
        return f"<ConversationMessage(id={self.id}, execution={self.execution_id}, role={self.role})>"

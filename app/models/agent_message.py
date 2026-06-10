"""
Agent消息模型
"""

from typing import Optional
from sqlalchemy import String, Integer, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class AgentMessage(DbBaseModel):
    """
    Agent消息表模型

    存储Agent对话的每条消息
    """

    __tablename__ = "agent_message"

    session_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="会话ID")
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="system/user/assistant/tool"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="消息内容")
    original_content: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="原始用户消息内容（未渲染模板的）"
    )
    thinking: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="思考内容"
    )
    tool_calls: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="工具调用（JSON）"
    )
    tool_call_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="工具调用ID"
    )
    status: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        default="success",
        comment="工具执行状态：success/error",
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
        Integer, nullable=False, default=0, comment="排序序号"
    )
    files: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="附件文件列表"
    )

    def __repr__(self) -> str:
        return f"<AgentMessage(id={self.id}, session_id={self.session_id}, role={self.role})>"

"""
工具审批审计日志模型

一行对应一次工具审批的完整生命周期：
register 时写入 asked 快照（outcome=NULL），用户决策/超时/取消后补写 outcome。
独立于 AgentMessage 对话消息：会话消息只隐式记录「用户未批准」错误串，
本表提供结构化的审批原因与精确终态（rejected/timeout/cancelled 可区分），
供审计查询与后续授权策略（如一次性授权）使用。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_model import DbBaseModel


class ToolApprovalLog(DbBaseModel):
    """工具审批审计日志表"""

    __tablename__ = "tool_approval_log"
    __table_args__ = (
        UniqueConstraint("approval_id", name="uq_tool_approval_log_approval_id"),
        Index("idx_tool_approval_log_session", "session_id", "is_delete"),
    )

    session_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="会话ID")
    approval_id: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="审批批次ID（全局唯一，防重复写终态）"
    )
    tool_calls: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="待审批工具调用快照（JSON）"
    )
    approval_needed: Mapped[Optional[list]] = mapped_column(
        JSON, nullable=True, comment="需审批的工具名列表"
    )
    approval_reason: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="审批原因（白名单/危险正则命中说明）"
    )
    outcome: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="审批终态：approved/rejected/timeout/cancelled；NULL=待决",
    )
    decided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="终态写入时间"
    )

    def __repr__(self) -> str:
        return (
            f"<ToolApprovalLog(id={self.id}, approval_id={self.approval_id}, "
            f"outcome={self.outcome})>"
        )

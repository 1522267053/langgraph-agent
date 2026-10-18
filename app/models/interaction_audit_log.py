"""
交互审计日志模型（统一表）

一行对应一次「等待用户交互」的完整生命周期，kind 判别两类交互
（借鉴 dsh asked/decided 事件对思想，同一条日志不同事件类型）：
- kind=approval：工具审批（payload 存 tool_calls/approval_needed/approval_reason）
- kind=question：问题反问（payload 存 question/options/multiple，answers 存用户所选）

register 时写入 asked 快照（outcome=NULL），用户决策/超时/取消后补写终态。
独立于 AgentMessage 对话消息：会话消息只隐式记录结果错误串，
本表提供结构化的原因/选项结构与精确终态，供审计查询。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_model import DbBaseModel


class InteractionAuditLog(DbBaseModel):
    """交互审计日志表（工具审批 + 问题反问统一）"""

    __tablename__ = "interaction_audit_log"
    __table_args__ = (
        UniqueConstraint("item_id", name="uq_interaction_audit_log_item_id"),
        Index("idx_interaction_audit_log_session", "session_id", "is_delete"),
        Index("idx_interaction_audit_log_kind", "kind", "is_delete"),
    )

    session_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Agent 会话ID 或 Flow 执行记录ID（按 scope 判别）"
    )
    scope: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="agent",
        server_default="agent",
        comment="交互域：agent=Agent 会话，flow=Flow 执行记录",
    )
    item_id: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="交互批次ID（approval_id/question_id，全局唯一防重复终态）",
    )
    kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="交互类型：approval=工具审批，question=问题反问",
    )
    outcome: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="终态：approved/rejected/timeout/cancelled/answered；NULL=待决",
    )
    payload: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="asked 类别快照：approval={tool_calls,approval_needed,approval_reason}；"
        "question={question,options,multiple}",
    )
    answers: Mapped[Optional[list]] = mapped_column(
        JSON,
        nullable=True,
        comment="用户所选标签列表（kind=question 且 outcome=answered 时有值）",
    )
    decided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="终态写入时间"
    )

    def __repr__(self) -> str:
        return (
            f"<InteractionAuditLog(id={self.id}, item_id={self.item_id}, "
            f"kind={self.kind}, outcome={self.outcome})>"
        )

"""
会话级工具授权模型

记录用户在审批弹窗选择「批准并记住」后的会话级免审授权：
同 session + 同工具名的后续调用自动放行（借鉴 dsh allowed-once 思想的
本地化变体——授权范围是会话而非单次调用，会话结束随会话删除失效）。
"""

from typing import Optional

from sqlalchemy import Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_model import DbBaseModel


class SessionToolGrant(DbBaseModel):
    """会话级工具授权表"""

    __tablename__ = "session_tool_grant"
    __table_args__ = (
        UniqueConstraint(
            "scope",
            "session_id",
            "tool_name",
            name="uq_session_tool_grant_scope_session_tool",
        ),
        Index("idx_session_tool_grant_session", "session_id", "is_delete"),
    )

    session_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Agent 会话ID 或 Flow 执行记录ID（按 scope 判别）"
    )
    scope: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="agent",
        server_default="agent",
        comment="授权域：agent=Agent 会话级，flow=Flow 执行级（执行结束失效）",
    )
    tool_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="免审工具名（同会话同工具后续调用自动放行）",
    )
    granted_by: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, comment="授权来源的 approval_id（追溯首次批准）"
    )

    def __repr__(self) -> str:
        return (
            f"<SessionToolGrant(id={self.id}, session_id={self.session_id}, "
            f"tool_name={self.tool_name})>"
        )

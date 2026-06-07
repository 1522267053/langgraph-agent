"""
Agent会话模型
"""

from sqlalchemy import String, Integer, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class AgentSession(DbBaseModel):
    """
    Agent会话表模型

    每个会话对应一个Agent的一次对话，支持多轮对话
    """

    __tablename__ = "agent_session"

    flow_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="关联的Agent Flow ID"
    )
    title: Mapped[str] = mapped_column(
        String(255), nullable=False, default="新对话", comment="会话标题"
    )
    status: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, comment="状态：1=活跃，0=已归档"
    )

    def __repr__(self) -> str:
        return (
            f"<AgentSession(id={self.id}, flow_id={self.flow_id}, title={self.title})>"
        )

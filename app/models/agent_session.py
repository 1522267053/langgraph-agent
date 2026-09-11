"""
Agent会话模型
"""

from typing import Optional

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
    gateway_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        default=None,
        comment="创建该会话的 网关 ID（用户聊天创建则为空）",
    )
    parent_session_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, default=None, comment="父Agent会话ID（子Agent会话）"
    )
    parent_node_key: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, default=None, comment="创建该子会话的节点key"
    )
    work_dir: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        default=None,
        comment="项目工作路径（规范化绝对路径），空则使用 Agent 默认工作目录",
    )
    plan_mode: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        nullable=True,
        default=0,
        comment="计划模式：1=开启（只读探索，禁用写操作工具），0/空=关闭",
    )
    chat_model: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
        default=None,
        comment="会话级临时覆盖 LLM 模型 id，空则使用 LLM 节点默认配置",
    )
    chat_provider: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        default=None,
        comment="与 chat_model 配套的供应商 ID；跨供应商时从供应商连接解析 api_key/base_url",
    )

    def __repr__(self) -> str:
        return (
            f"<AgentSession(id={self.id}, flow_id={self.flow_id}, title={self.title})>"
        )

"""
流程边模型
"""

from typing import Optional
from sqlalchemy import String, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class FlowEdge(DbBaseModel):
    """
    流程边表模型（节点连接）

    继承 DbBaseModel，自动拥有：
    - id, creator_id, creator_type, creator_name, create_time
    - modifier_id, modifier_type, modifier_name, modify_time
    - is_delete
    """

    __tablename__ = "flow_edge"

    flow_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="流程ID")
    source_node_key: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="源节点key"
    )
    target_node_key: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="目标节点key"
    )
    source_handle: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="源节点handle ID（条件分支用）"
    )
    target_handle: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="目标节点handle ID"
    )
    condition: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="条件表达式(条件分支用)"
    )
    label: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="边标签"
    )

    def __repr__(self) -> str:
        return f"<FlowEdge(id={self.id}, source={self.source_node_key}, target={self.target_node_key})>"

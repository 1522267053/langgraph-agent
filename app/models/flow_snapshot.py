"""
流程版本快照模型

保存流程（含智能体）在某一时刻的完整拓扑（元数据 + 节点 + 边），
支持自动快照（保存前）和手动快照，可用于回滚恢复。
"""

from typing import Optional
from sqlalchemy import String, Text, JSON, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_model import DbBaseModel


class FlowSnapshot(DbBaseModel):
    """流程版本快照"""

    __tablename__ = "flow_snapshot"

    flow_id: Mapped[int] = mapped_column(nullable=False, comment="关联流程ID")

    snapshot_name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="快照名称"
    )

    snapshot_description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="快照描述"
    )

    snapshot_data: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="快照数据（含 flow_meta + nodes + edges）"
    )

    snapshot_type: Mapped[str] = mapped_column(
        String(20),
        default="auto",
        nullable=False,
        comment="快照类型：auto=自动，manual=手动",
    )

    is_pinned: Mapped[int] = mapped_column(
        SmallInteger,
        default=0,
        nullable=False,
        comment="是否置顶：0=否，1=是（不被自动清理）",
    )

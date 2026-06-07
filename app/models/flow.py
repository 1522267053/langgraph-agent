"""
流程模型
"""

from enum import Enum
from typing import Optional, List, TYPE_CHECKING, ClassVar
from sqlalchemy import String, Text, SmallInteger, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel

if TYPE_CHECKING:
    from app.models.flow_node import FlowNode
    from app.models.flow_edge import FlowEdge


class FlowStatus(int, Enum):
    """流程状态"""

    DRAFT = 0
    PUBLISHED = 1


class FlowType(str, Enum):
    """流程类型"""

    FLOW = "flow"
    AGENT = "agent"


class Flow(DbBaseModel):
    """
    流程表模型

    继承 DbBaseModel，自动拥有：
    - id, creator_id, creator_type, creator_name, create_time
    - modifier_id, modifier_type, modifier_name, modify_time
    - is_delete
    """

    __tablename__ = "flow"

    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="流程名称")
    description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="流程描述"
    )
    flow_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=FlowType.FLOW.value,
        comment="类型：flow=普通流程，agent=智能体",
    )
    status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=FlowStatus.DRAFT.value,
        comment="状态：0=草稿，1=已发布",
    )
    saved_as_card: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        comment="是否已保存为能力卡片：0=否，1=是",
    )
    input_schema: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="输入参数定义(JSON)"
    )
    output_schema: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="输出参数定义(JSON)"
    )
    is_builtin: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        comment="是否内置：0=否，1=是",
    )

    nodes: ClassVar[List["FlowNode"]]
    edges: ClassVar[List["FlowEdge"]]

    def __repr__(self) -> str:
        return f"<Flow(id={self.id}, name={self.name}, status={self.status})>"

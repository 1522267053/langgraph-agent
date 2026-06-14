"""
流程版本快照 Schema
"""

from typing import Optional
from pydantic import BaseModel, Field

from app.schemas.base_schema import BaseView


class FlowSnapshotBase(BaseView):
    """快照基础视图"""

    flow_id: Optional[int] = Field(None, description="关联流程ID")
    snapshot_name: Optional[str] = Field(None, description="快照名称")
    snapshot_description: Optional[str] = Field(None, description="快照描述")
    snapshot_type: Optional[str] = Field(None, description="快照类型")
    is_pinned: Optional[int] = Field(None, description="是否置顶")


class FlowSnapshotCreate(BaseModel):
    """手动创建快照请求"""

    name: str = Field(..., description="快照名称")
    description: Optional[str] = Field(None, description="快照描述")


class FlowSnapshotUpdate(FlowSnapshotBase):
    """更新快照"""

    pass


class FlowSnapshotCondition(BaseModel):
    """快照查询条件"""

    flow_id: Optional[int] = Field(None, description="流程ID")
    snapshot_type: Optional[str] = Field(None, description="快照类型")

"""
流程边相关数据模型
"""

from typing import List, Optional

from pydantic import Field, BaseModel

from app.schemas.base_schema import BaseView


class FlowEdgeBase(BaseView):
    """流程边基础模型"""

    flow_id: Optional[int] = Field(None, description="流程ID")
    source_node_key: Optional[str] = Field(None, description="源节点key")
    target_node_key: Optional[str] = Field(None, description="目标节点key")
    source_handle: Optional[str] = Field(
        None, description="源节点handle ID（条件分支用）"
    )
    target_handle: Optional[str] = Field(None, description="目标节点handle ID")
    condition: Optional[dict] = Field(None, description="条件表达式")
    label: Optional[str] = Field(None, description="边标签")


class FlowEdgeCreate(FlowEdgeBase):
    """创建流程边"""

    flow_id: int = Field(..., description="流程ID")
    source_node_key: str = Field(..., description="源节点key")
    target_node_key: str = Field(..., description="目标节点key")


class FlowEdgeUpdate(FlowEdgeBase):
    """更新流程边"""

    pass


class FlowEdgeBatchSaveReq(BaseModel):
    """批量保存边请求（合并创建和更新）"""

    create: List[FlowEdgeCreate] = Field(default_factory=list, description="待创建的边")
    update: List[FlowEdgeUpdate] = Field(default_factory=list, description="待更新的边")

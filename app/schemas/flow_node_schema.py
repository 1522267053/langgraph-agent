"""
流程节点相关数据模型
"""

from typing import Optional
from pydantic import Field
from app.schemas.base_schema import BaseView


class FlowNodeBase(BaseView):
    """流程节点基础模型"""

    flow_id: Optional[int] = Field(None, description="流程ID")
    node_type: Optional[str] = Field(
        None, description="节点类型：start/end/condition/card"
    )
    node_key: Optional[str] = Field(None, description="节点唯一标识")
    node_name: Optional[str] = Field(None, description="节点名称")
    position_x: Optional[float] = Field(None, description="X坐标")
    position_y: Optional[float] = Field(None, description="Y坐标")
    base_config: Optional[dict] = Field(None, description="节点配置(JSON)")
    ref_flow_id: Optional[int] = Field(None, description="引用的流程ID(能力卡片节点用)")


class FlowNodeCreate(FlowNodeBase):
    """创建流程节点"""

    flow_id: int = Field(..., description="流程ID")
    node_type: str = Field(..., description="节点类型")
    node_key: str = Field(..., description="节点唯一标识")


class FlowNodeUpdate(FlowNodeBase):
    """更新流程节点"""

    pass

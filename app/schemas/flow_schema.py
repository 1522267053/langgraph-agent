"""
流程相关数据模型
"""

from enum import Enum
from typing import Optional, Any
from pydantic import Field, field_validator
from app.schemas.base_schema import BaseView
from app.schemas.flow_node_schema import FlowNodeBase
from app.schemas.flow_edge_schema import FlowEdgeBase


class FieldType(str, Enum):
    """字段数据类型"""

    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"
    FILE_LIST = "file_list"


class FlowIOField(BaseView):
    """流程输入/输出字段定义"""

    name: str = Field(..., description="字段名称")
    type: FieldType = Field(
        ..., description="字段类型：string/number/boolean/object/array/file"
    )
    description: Optional[str] = Field(None, description="字段描述")
    placeholder: Optional[str] = Field(None, description="输入框占位提示文本")
    required: bool = Field(default=False, description="是否必填")
    accept: Optional[str] = Field(
        None, description="允许的文件类型，如 image/*,.pdf,.docx"
    )
    multiple: bool = Field(default=False, description="是否允许多文件")
    max_size: Optional[int] = Field(None, description="最大文件大小(MB)")


class FlowIOSchema(BaseView):
    """流程输入/输出参数定义"""

    fields: list[FlowIOField] = Field(default_factory=list, description="字段列表")


class FlowBase(BaseView):
    """流程基础模型"""

    name: Optional[str] = Field(None, description="流程名称")
    description: Optional[str] = Field(None, description="流程描述")
    flow_type: Optional[str] = Field(
        None, description="类型：flow=普通流程，agent=智能体"
    )
    status: Optional[int] = Field(None, description="状态：0=草稿，1=已发布")
    saved_as_card: Optional[int] = Field(
        None, description="是否已保存为能力卡片：0=否，1=是"
    )
    input_schema: Optional[FlowIOSchema] = Field(None, description="输入参数定义")
    output_schema: Optional[FlowIOSchema] = Field(None, description="输出参数定义")
    is_builtin: Optional[int] = Field(None, description="是否内置：0=否，1=是")
    suggested_prompts: Optional[list[str]] = Field(
        None, description="建议提示词列表"
    )

    @field_validator("input_schema", "output_schema", mode="before")
    @classmethod
    def validate_schema(cls, v: Any) -> Optional[FlowIOSchema]:
        if v is None:
            return None
        if isinstance(v, FlowIOSchema):
            return v
        if isinstance(v, dict):
            return FlowIOSchema(**v)
        return v


class FlowCreate(FlowBase):
    """创建流程"""

    name: str = Field(..., description="流程名称")
    flow_type: Optional[str] = Field(
        None, description="类型：flow=普通流程，agent=智能体"
    )
    is_builtin: Optional[int] = Field(0, description="是否内置：0=否，1=是")


class FlowUpdate(FlowBase):
    """更新流程"""

    pass


class FlowDetail(FlowBase):
    """流程详情（含节点和边）"""

    nodes: Optional[list[FlowNodeBase]] = Field(default=None, description="节点列表")
    edges: Optional[list[FlowEdgeBase]] = Field(default=None, description="边列表")


class VueFlowNodeData(BaseView):
    """Vue Flow 节点数据格式"""

    label: Optional[str] = Field(None, description="节点标签")
    config: Optional[dict] = Field(None, description="节点配置")


class VueFlowNode(BaseView):
    """Vue Flow 节点格式"""

    id: Optional[str] = Field(None, description="节点ID")
    type: Optional[str] = Field(None, description="节点类型")
    position: Optional[dict] = Field(None, description="位置{x, y}")
    data: Optional[VueFlowNodeData] = Field(None, description="节点数据")


class VueFlowEdge(BaseView):
    """Vue Flow 边格式"""

    id: Optional[str] = Field(None, description="边ID")
    source: Optional[str] = Field(None, description="源节点ID")
    target: Optional[str] = Field(None, description="目标节点ID")
    label: Optional[str] = Field(None, description="边标签")
    data: Optional[dict] = Field(None, description="边数据")


class VueFlowGraph(BaseView):
    """Vue Flow 图格式"""

    nodes: list[VueFlowNode] = Field(default_factory=list, description="节点列表")
    edges: list[VueFlowEdge] = Field(default_factory=list, description="边列表")

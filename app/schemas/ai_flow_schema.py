"""
AI 流程生成专用数据模型

提供 AI 模型创建/修改流程的请求/响应 Schema。
工作流：创建流程 → 批量添加节点（获取 node_key）→ 批量添加边。
"""

from typing import Optional, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
    model_serializer,
)

from app.schemas.flow_node_schema import FlowNodeBase
from app.schemas.flow_edge_schema import FlowEdgeBase


VALID_NODE_TYPES = {
    "start",
    "end",
    "condition",
    "card",
    "loop",
    "llm",
    "mcp",
    "knowledge",
    "human",
    "api",
    "skill",
    "python",
    "shell",
    "memory",
    "todo",
    "intent_router",
}


# ---- 创建流程 ----


class AiFlowCreateReq(BaseModel):
    """创建空流程请求"""

    name: str = Field(..., description="流程名称", min_length=1, max_length=255)
    description: Optional[str] = Field(None, description="流程描述")
    flow_type: str = Field("flow", description="类型: flow=普通流程, agent=智能体")
    input_schema: Optional[dict] = Field(
        None, description="输入参数定义（含 fields 列表），创建时一步到位"
    )
    output_schema: Optional[dict] = Field(
        None, description="输出参数定义（含 fields 列表）"
    )

    @field_validator("flow_type")
    @classmethod
    def validate_flow_type(cls, v: str) -> str:
        if v not in ("flow", "agent"):
            raise ValueError("flow_type 仅支持: flow, agent")
        return v


# ---- 批量创建节点 ----


class AiFlowBatchNodeItem(BaseModel):
    """批量创建节点的单个节点定义"""

    node_type: str = Field(..., description="节点类型")
    node_key: Optional[str] = Field(
        None,
        description="节点唯一标识（省略时自动生成，冲突时自动追加序号）",
    )
    node_name: Optional[str] = Field(None, description="节点显示名称")
    position_x: float = Field(0, description="X坐标（UI用）")
    position_y: float = Field(0, description="Y坐标（UI用）")
    base_config: Optional[dict] = Field(None, description="节点配置")
    ref_flow_id: Optional[int] = Field(
        None, description="引用的流程ID（能力卡片节点用）"
    )

    @field_validator("node_type")
    @classmethod
    def validate_node_type(cls, v: str) -> str:
        if v not in VALID_NODE_TYPES:
            raise ValueError(
                f"不支持的节点类型: {v}，有效值: {', '.join(sorted(VALID_NODE_TYPES))}"
            )
        return v


class AiFlowNodesBatchReq(BaseModel):
    """批量创建节点请求"""

    nodes: list[AiFlowBatchNodeItem] = Field(..., description="节点列表", min_length=1)


class AiFlowNodeItemResp(BaseModel):
    """创建成功的节点简要信息"""

    node_key: str
    node_name: Optional[str] = None
    node_type: str


class AiFlowNodesBatchResp(BaseModel):
    """批量创建节点响应"""

    created_nodes: list[AiFlowNodeItemResp]


# ---- 批量配置节点 ----


class AiFlowNodeConfigItem(BaseModel):
    """批量配置节点的单个节点配置"""

    node_key: str = Field(..., description="节点唯一标识")
    node_name: Optional[str] = Field(None, description="节点显示名称")
    base_config: Optional[dict] = Field(
        None, description="节点配置（整体替换，非合并）"
    )
    position_x: Optional[float] = Field(None, description="X坐标")
    position_y: Optional[float] = Field(None, description="Y坐标")


class AiFlowNodesConfigReq(BaseModel):
    """批量配置节点请求"""

    nodes: list[AiFlowNodeConfigItem] = Field(
        ..., description="节点配置列表", min_length=1
    )


# ---- 批量删除节点 ----


class AiFlowNodesDeleteReq(BaseModel):
    """批量删除节点请求（级联删除关联边）"""

    node_keys: list[str] = Field(
        ..., description="要删除的节点 node_key 列表", min_length=1
    )


# ---- 批量创建边 ----


class AiFlowEdgeItem(BaseModel):
    """边定义（用于批量创建）"""

    source_node_key: str = Field(..., description="源节点key")
    target_node_key: str = Field(..., description="目标节点key")
    source_handle: str = Field(
        ...,
        description="源节点handle ID：default（标准输出）, tools（工具输出）, true/false（条件分支）, 或意图路由的动态 intent key",
    )
    target_handle: str = Field(
        ...,
        description="目标节点handle ID：default（标准输入）, tools（工具输入）",
    )
    condition: Optional[dict] = Field(None, description="条件表达式")
    label: Optional[str] = Field(None, description="边标签")

    @field_validator("source_handle", "target_handle")
    @classmethod
    def validate_handle(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("handle ID 不能为空")
        return v

    @model_validator(mode="after")
    def validate_handle_pair(self) -> "AiFlowEdgeItem":
        src = self.source_handle
        tgt = self.target_handle
        if src == "tools":
            if tgt != "tools":
                raise ValueError(
                    "工具边（tools）的 source_handle 和 target_handle 必须同时为 tools"
                )
        elif src in ("true", "false"):
            if tgt != "default":
                raise ValueError(
                    "条件分支边（true/false）的 target_handle 必须为 default"
                )
        elif src == "default":
            if tgt != "default":
                raise ValueError("标准数据流边的 target_handle 必须为 default")
        return self


class AiFlowEdgesBatchReq(BaseModel):
    """批量创建边请求"""

    edges: list[AiFlowEdgeItem] = Field(..., description="边列表", min_length=1)


class AiFlowEdgeItemResp(BaseModel):
    """创建成功的边简要信息"""

    source_node_key: str
    target_node_key: str


class AiFlowEdgesBatchResp(BaseModel):
    """批量创建边响应"""

    created_edges: list[AiFlowEdgeItemResp]


# ---- 批量删除边 ----


class AiFlowEdgeDeleteItem(BaseModel):
    """要删除的边标识"""

    source_node_key: str
    target_node_key: str
    source_handle: str = Field(
        ...,
        description="源节点handle ID：default / tools / true / false / 动态 intent key",
    )

    @field_validator("source_handle")
    @classmethod
    def validate_handle(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("handle ID 不能为空")
        return v


class AiFlowEdgesDeleteReq(BaseModel):
    """批量删除边请求"""

    edges: list[AiFlowEdgeDeleteItem] = Field(
        ..., description="要删除的边列表", min_length=1
    )


# ---- 查看详情 ----


class AiFlowDetailResponse(BaseModel):
    """AI 流程详情响应（含 Mermaid 图）"""

    model_config = ConfigDict(exclude_none=True)

    id: Optional[int] = None
    name: Optional[str] = None
    description: Optional[str] = None
    flow_type: Optional[str] = None
    status: Optional[int] = None
    saved_as_card: Optional[int] = None
    input_schema: Optional[dict] = None
    output_schema: Optional[dict] = None
    nodes: list[FlowNodeBase] = Field(default_factory=list)
    edges: list[FlowEdgeBase] = Field(default_factory=list)
    mermaid: str = Field(default="", description="LangGraph 生成的 Mermaid 流程图代码")

    @model_serializer(mode="wrap")
    def _strip_all_nulls(self, handler) -> dict:
        result = handler(self)
        return _strip_nulls(result)


def _strip_nulls(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_nulls(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_nulls(item) for item in obj]
    return obj

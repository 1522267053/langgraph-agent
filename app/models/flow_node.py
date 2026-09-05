"""
流程节点模型

节点类型相关常量（BASIC_NODE_TYPES / AGENT_ALLOWED_NODE_TYPES /
AGENT_TOOL_NODE_TYPES / AGENT_UNIQUE_NODE_TYPES / TOOL_ONLY_NODE_TYPES /
NODE_SOURCE_HANDLES / NODE_TARGET_HANDLES / NODE_TYPE_LABELS）已统一从
app.constants.node_types.NODE_REGISTRY 派生。新增节点类型时，只需在 NodeType
枚举与 NODE_REGISTRY 同时添加一行即可。

为避免循环引用（constants → models → constants），本模块先定义 NodeType 枚举，
然后再 import NODE_REGISTRY 与派生 helper，最后定义派生常量。
"""

from enum import Enum
from typing import List, Optional
from sqlalchemy import String, Integer, JSON, Float
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel
from app.models.flow import Flow
from app.models.flow_edge import FlowEdge


class NodeType(str, Enum):
    """节点类型枚举（与 app.constants.node_types.NODE_REGISTRY 的 key 保持完全一致）"""

    START = "start"
    END = "end"
    CONDITION = "condition"
    CARD = "card"
    LLM = "llm"
    MCP = "mcp"
    KNOWLEDGE = "knowledge"
    HUMAN = "human"
    API = "api"
    SKILL = "skill"
    PYTHON = "python"
    SHELL = "shell"
    MEMORY = "memory"
    TODO = "todo"
    LOOP = "loop"
    INTENT_ROUTER = "intent_router"
    SUB_AGENT = "sub_agent"
    AGENDA = "agenda"
    SSH = "ssh"
    QUESTION = "question"


# ---- 派生表：从 NODE_REGISTRY 自动生成 ----
from app.constants.node_types import NODE_REGISTRY  # noqa: E402  必须在 NodeType 之后


# Flow 模式可用节点（保持 List[NodeType] 类型，兼容 base_executor_service 的迭代）
BASIC_NODE_TYPES: List[NodeType] = [
    NodeType(k) for k, meta in NODE_REGISTRY.items() if meta.flow
]

# Agent 模式可用节点
AGENT_ALLOWED_NODE_TYPES: List[str] = [
    k for k, meta in NODE_REGISTRY.items() if meta.agent
]

# Agent 工具节点（连接 LLM 的 tools handle）
AGENT_TOOL_NODE_TYPES: set[str] = {
    k for k, meta in NODE_REGISTRY.items() if meta.agent_tool
}

# Agent 中唯一的节点（一个 Agent 流程只能有一个）
AGENT_UNIQUE_NODE_TYPES: set[str] = {
    k for k, meta in NODE_REGISTRY.items() if meta.agent_unique
}

# 仅作为工具节点（不能作为流程主节点）
TOOL_ONLY_NODE_TYPES: set[str] = {
    k for k, meta in NODE_REGISTRY.items() if meta.tool_only
}

# 节点出向 handle
NODE_SOURCE_HANDLES: dict[str, set[str]] = {
    k: set(meta.source) for k, meta in NODE_REGISTRY.items()
}

# 节点入向 handle
NODE_TARGET_HANDLES: dict[str, set[str]] = {
    k: set(meta.target) for k, meta in NODE_REGISTRY.items()
}


def _validate_registry_consistency() -> None:
    """开发期断言：NodeType 枚举与 NODE_REGISTRY 必须一一对应

    在模块导入末尾运行一次，捕获 NodeType 与 NODE_REGISTRY 各自新增/遗漏的情况。
    """
    enum_values = {nt.value for nt in NodeType}
    registry_keys = set(NODE_REGISTRY.keys())
    missing_in_registry = enum_values - registry_keys
    missing_in_enum = registry_keys - enum_values
    if missing_in_registry:
        raise RuntimeError(
            f"NodeType 枚举中以下值未在 NODE_REGISTRY 注册: "
            f"{sorted(missing_in_registry)}"
        )
    if missing_in_enum:
        raise RuntimeError(
            f"NODE_REGISTRY 中以下 key 未在 NodeType 枚举中: "
            f"{sorted(missing_in_enum)}"
        )


_validate_registry_consistency()


class FlowNode(DbBaseModel):
    """
    流程节点表模型

    继承 DbBaseModel，自动拥有：
    - id, creator_id, creator_type, creator_name, create_time
    - modifier_id, modifier_type, modifier_name, modify_time
    - is_delete
    """

    __tablename__ = "flow_node"

    flow_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="流程ID")
    node_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment=(
            "节点类型：start/end/condition/card/loop/llm/mcp/knowledge/human/api/"
            "skill/python/shell/memory/todo/intent_router/sub_agent/agenda/ssh/question"
        ),
    )
    node_key: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="节点唯一标识(用于边连接)"
    )
    node_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="节点名称"
    )
    position_x: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, comment="X坐标(UI用)"
    )
    position_y: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, comment="Y坐标(UI用)"
    )
    base_config: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="节点配置(JSON)"
    )
    ref_flow_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="引用的流程ID(能力卡片节点用)"
    )

    def __repr__(self) -> str:
        return f"<FlowNode(id={self.id}, node_key={self.node_key}, node_type={self.node_type})>"


class ExpandedFlow:
    """展开后的流程数据容器（避免修改 SQLAlchemy 模型的 relationship）"""

    def __init__(self, flow: Flow, nodes: List[FlowNode], edges: List[FlowEdge]):
        self.id = flow.id
        self.name = flow.name
        self.description = flow.description
        self.status = flow.status
        self.saved_as_card = flow.saved_as_card
        self.input_schema = flow.input_schema
        self.output_schema = flow.output_schema
        self.is_builtin = flow.is_builtin
        self.flow_type = flow.flow_type
        self.nodes = nodes
        self.edges = edges

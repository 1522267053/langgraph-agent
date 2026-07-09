"""
流程节点模型
"""

from enum import Enum
from typing import List, Optional
from sqlalchemy import String, Integer, JSON, Float
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel
from app.models.flow import Flow
from app.models.flow_edge import FlowEdge


class NodeType(str, Enum):
    """节点类型"""

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


BASIC_NODE_TYPES: List[NodeType] = [
    NodeType.START,
    NodeType.END,
    NodeType.CONDITION,
    NodeType.CARD,
    NodeType.LOOP,
    NodeType.API,
    NodeType.HUMAN,
    NodeType.SKILL,
    NodeType.KNOWLEDGE,
    NodeType.PYTHON,
    NodeType.SHELL,
    NodeType.MEMORY,
    NodeType.MCP,
    NodeType.TODO,
    NodeType.INTENT_ROUTER,
    NodeType.SUB_AGENT,
    NodeType.AGENDA,
]

AGENT_ALLOWED_NODE_TYPES: List[str] = [
    NodeType.START.value,
    NodeType.END.value,
    NodeType.LLM.value,
    NodeType.MCP.value,
    NodeType.KNOWLEDGE.value,
    NodeType.SKILL.value,
    NodeType.PYTHON.value,
    NodeType.SHELL.value,
    NodeType.MEMORY.value,
    NodeType.TODO.value,
    NodeType.API.value,
    NodeType.CONDITION.value,
    NodeType.INTENT_ROUTER.value,
    NodeType.SUB_AGENT.value,
    NodeType.AGENDA.value,
]

AGENT_TOOL_NODE_TYPES: set[str] = {
    NodeType.MCP.value,
    NodeType.KNOWLEDGE.value,
    NodeType.SKILL.value,
    NodeType.PYTHON.value,
    NodeType.SHELL.value,
    NodeType.MEMORY.value,
    NodeType.TODO.value,
    NodeType.API.value,
    NodeType.SUB_AGENT.value,
    NodeType.AGENDA.value,
}

TOOL_ONLY_NODE_TYPES: set[str] = {
    NodeType.SKILL.value,
    NodeType.MCP.value,
    NodeType.MEMORY.value,
    NodeType.TODO.value,
    NodeType.SUB_AGENT.value,
    NodeType.AGENDA.value,
}

NODE_SOURCE_HANDLES: dict[str, set[str]] = {
    NodeType.START.value: {"default"},
    NodeType.END.value: set(),
    NodeType.CONDITION.value: {"default", "true", "false"},
    NodeType.CARD.value: {"default"},
    NodeType.LOOP.value: {"default"},
    NodeType.LLM.value: {"default"},
    NodeType.MCP.value: {"tools"},
    NodeType.KNOWLEDGE.value: {"default", "tools"},
    NodeType.HUMAN.value: {"default", "tools"},
    NodeType.API.value: {"default", "tools"},
    NodeType.SKILL.value: {"tools"},
    NodeType.PYTHON.value: {"default", "tools"},
    NodeType.SHELL.value: {"tools"},
    NodeType.MEMORY.value: {"tools"},
    NodeType.TODO.value: {"tools"},
    # 意图路由：default + 每个意图 key 动态生成 handle（前端动态生成）
    NodeType.INTENT_ROUTER.value: {"default"},
    NodeType.SUB_AGENT.value: {"tools"},
    NodeType.AGENDA.value: {"tools"},
}

NODE_TARGET_HANDLES: dict[str, set[str]] = {
    NodeType.START.value: set(),
    NodeType.END.value: {"default"},
    NodeType.CONDITION.value: {"default"},
    NodeType.CARD.value: {"default"},
    NodeType.LOOP.value: {"default"},
    NodeType.LLM.value: {"default", "tools"},
    NodeType.MCP.value: set(),
    NodeType.KNOWLEDGE.value: {"default"},
    NodeType.HUMAN.value: {"default"},
    NodeType.API.value: {"default"},
    NodeType.SKILL.value: set(),
    NodeType.PYTHON.value: {"default"},
    NodeType.SHELL.value: set(),
    NodeType.MEMORY.value: set(),
    NodeType.TODO.value: set(),
    NodeType.INTENT_ROUTER.value: {"default"},
    NodeType.SUB_AGENT.value: set(),
    NodeType.AGENDA.value: set(),
}

AGENT_UNIQUE_NODE_TYPES: set[str] = {
    NodeType.START.value,
    NodeType.END.value,
    NodeType.LLM.value,
}


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
        comment="节点类型：start/end/condition/card/loop/llm/mcp/knowledge/human/api",
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

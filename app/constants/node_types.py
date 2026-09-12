"""
节点类型相关常量（单一事实源 NODE_REGISTRY）

新增节点类型时，只需在 NODE_REGISTRY 添加一条记录。
其他派生表（BASIC_NODE_TYPES / AGENT_ALLOWED_NODE_TYPES / AGENT_TOOL_NODE_TYPES /
AGENT_UNIQUE_NODE_TYPES / TOOL_ONLY_NODE_TYPES / NODE_SOURCE_HANDLES /
NODE_TARGET_HANDLES / NODE_TYPE_LABELS）自动从此表生成。

字段说明：
- label: 中文标签（错误信息 / 前端展示）
- source: 出向 handle 集合（节点连出的边）
- target: 入向 handle 集合（节点接收的边）
- flow: Flow 模式是否可用（BASIC_NODE_TYPES 派生）
- agent: Agent 模式是否可用（AGENT_ALLOWED_NODE_TYPES 派生）
- agent_tool: 是否 Agent 工具节点（挂到 LLM 节点的 tools handle）
              对应 AGENT_TOOL_NODE_TYPES
- agent_unique: Agent 中是否唯一（一个 Agent 流程只能有一个）
                对应 AGENT_UNIQUE_NODE_TYPES
- tool_only: 是否仅作为工具节点使用（不能作为流程主节点）
             对应 TOOL_ONLY_NODE_TYPES
"""

from dataclasses import dataclass, field
from typing import Set


@dataclass(frozen=True)
class NodeMeta:
    """单个节点类型的元数据"""

    label: str = field(metadata={"description": "中文标签（错误信息 / 前端展示）"})
    source: Set[str] = field(
        default_factory=set,
        metadata={"description": "出向 handle 集合（节点连出的边）"},
    )
    target: Set[str] = field(
        default_factory=set,
        metadata={"description": "入向 handle 集合（节点接收的边）"},
    )
    flow: bool = field(
        default=False,
        metadata={
            "description": (
                "Flow 模式是否可用；为 True 时进入 BASIC_NODE_TYPES 派生表，"
                "base_executor_service._build_graph 会用它构建 LLM 的 "
                "handler_registry dict（漏掉会导致节点被静默跳过）"
            )
        },
    )
    agent: bool = field(
        default=False,
        metadata={
            "description": ("Agent 模式是否可用；进入 AGENT_ALLOWED_NODE_TYPES 派生表")
        },
    )
    agent_tool: bool = field(
        default=False,
        metadata={
            "description": (
                "是否 Agent 工具节点（挂到 LLM 节点的 tools handle）；"
                "对应 AGENT_TOOL_NODE_TYPES"
            )
        },
    )
    agent_unique: bool = field(
        default=False,
        metadata={
            "description": (
                "Agent 中是否唯一（一个 Agent 流程只能有一个）；"
                "对应 AGENT_UNIQUE_NODE_TYPES"
            )
        },
    )
    tool_only: bool = field(
        default=False,
        metadata={
            "description": (
                "是否仅作为工具节点使用（不能作为流程主节点）；"
                "对应 TOOL_ONLY_NODE_TYPES"
            )
        },
    )


NODE_REGISTRY: dict[str, NodeMeta] = {
    "start": NodeMeta(
        label="开始",
        source={"default"},
        target=set(),
        flow=True,
        agent=True,
        agent_unique=True,
    ),
    "end": NodeMeta(
        label="结束",
        source=set(),
        target={"default"},
        flow=True,
        agent=True,
        agent_unique=True,
    ),
    "condition": NodeMeta(
        label="条件",
        source={"default", "true", "false"},
        target={"default"},
        flow=True,
        agent=True,
    ),
    "card": NodeMeta(
        label="能力卡片",
        source={"default"},
        target={"default"},
        flow=True,
    ),
    "llm": NodeMeta(
        label="大模型调用",
        source={"default"},
        target={"default", "tools"},
        agent=True,
        agent_unique=True,
    ),
    "mcp": NodeMeta(
        label="MCP",
        source={"tools"},
        target=set(),
        flow=True,
        agent=True,
        agent_tool=True,
        tool_only=True,
    ),
    "knowledge": NodeMeta(
        label="知识库",
        source={"default", "tools"},
        target={"default"},
        flow=True,
        agent=True,
        agent_tool=True,
    ),
    "human": NodeMeta(
        label="人类回答",
        source={"default", "tools"},
        target={"default"},
        flow=True,
    ),
    "api": NodeMeta(
        label="API调用",
        source={"default", "tools"},
        target={"default"},
        flow=True,
        agent=True,
        agent_tool=True,
    ),
    "skill": NodeMeta(
        label="技能",
        source={"tools"},
        target=set(),
        flow=True,
        agent=True,
        agent_tool=True,
        tool_only=True,
    ),
    "python": NodeMeta(
        label="Python",
        source={"default", "tools"},
        target={"default"},
        flow=True,
        agent=True,
        agent_tool=True,
    ),
    "shell": NodeMeta(
        label="Shell",
        source={"tools"},
        target=set(),
        flow=True,
        agent=True,
        agent_tool=True,
    ),
    "memory": NodeMeta(
        label="记忆",
        source={"tools"},
        target=set(),
        flow=True,
        agent=True,
        agent_tool=True,
        tool_only=True,
    ),
    "todo": NodeMeta(
        label="任务计划",
        source={"tools"},
        target=set(),
        flow=True,
        agent=True,
        agent_tool=True,
        tool_only=True,
    ),
    "loop": NodeMeta(
        label="循环",
        source={"default"},
        target={"default"},
        flow=True,
    ),
    "intent_router": NodeMeta(
        label="意图路由",
        source={"default"},
        target={"default"},
        flow=True,
        agent=True,
    ),
    "sub_agent": NodeMeta(
        label="子Agent",
        source={"tools"},
        target=set(),
        flow=True,
        agent=True,
        agent_tool=True,
        tool_only=True,
    ),
    "agenda": NodeMeta(
        label="日程",
        source={"tools"},
        target=set(),
        flow=True,
        agent=True,
        agent_tool=True,
        tool_only=True,
    ),
    "ssh": NodeMeta(
        label="SSH",
        source={"tools"},
        target=set(),
        flow=True,
        agent=True,
        agent_tool=True,
        tool_only=True,
    ),
    "question": NodeMeta(
        label="问题反问",
        source={"tools"},
        target=set(),
        flow=True,
        agent=True,
        agent_tool=True,
    ),
    "flow_tool": NodeMeta(
        label="Flow工具",
        source={"tools"},
        target=set(),
        # flow=True：必须进 BASIC_NODE_TYPES，否则 base_executor_service._build_graph
        # 构建 LLM handler_registry 时会漏掉 flow_tool，
        # llm_tool_executor.setup_tool_handlers 静默跳过节点（参考 sub_agent）。
        flow=True,
        agent=True,
        agent_tool=True,
        tool_only=True,
    ),
}


def get_node_meta(node_type: str) -> NodeMeta | None:
    """按节点类型字符串查询元数据，未注册返回 None"""
    return NODE_REGISTRY.get(node_type)


# 中文标签字典（向后兼容：NODE_TYPE_LABELS 派生自 NODE_REGISTRY）
NODE_TYPE_LABELS: dict[str, str] = {k: meta.label for k, meta in NODE_REGISTRY.items()}

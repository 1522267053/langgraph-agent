"""
工具连接解析器

解析流程中节点之间的工具连接关系，为LLM节点获取可用的工具
"""

import logging
import re

from pydantic import BaseModel, Field

from app.agent_flow.flow_context import FlowState
from app.agent_flow.graph_builder import FlowLike
from app.models.flow import Flow
from app.models.flow_edge import FlowEdge
from app.models.flow_node import FlowNode, NodeType

logger = logging.getLogger(__name__)


class LlmToolConfig(BaseModel):
    """LLM节点的工具配置

    工具收集阶段由各 handler 的 get_tool_config 写入。声明式字段使新增
    工具族时漏定义能在类型检查阶段暴露，而非运行时 AttributeError。
    """

    mcp_server_ids: list[int] = Field(
        default_factory=list, description="连接的 MCP 服务器 ID 列表"
    )
    enable_human_assist: bool = Field(default=False, description="是否启用人工协助")
    human_assist_config: dict = Field(default_factory=dict, description="人工协助配置")
    human_node_keys: list[str] = Field(
        default_factory=list, description="Human 节点 key 列表"
    )
    api_node_keys: list[str] = Field(
        default_factory=list, description="API 节点 key 列表"
    )
    api_configs: dict[str, dict] = Field(
        default_factory=dict, description="API 节点配置"
    )
    knowledge_node_keys: list[str] = Field(
        default_factory=list, description="知识库节点 key 列表"
    )
    knowledge_configs: dict[str, dict] = Field(
        default_factory=dict, description="知识库节点配置"
    )
    skill_node_keys: list[str] = Field(
        default_factory=list, description="Skill 节点 key 列表"
    )
    skill_configs: dict[str, dict] = Field(
        default_factory=dict, description="Skill 节点配置"
    )
    memory_node_keys: list[str] = Field(
        default_factory=list, description="Memory 节点 key 列表"
    )
    memory_configs: dict[str, dict] = Field(
        default_factory=dict, description="Memory 节点配置"
    )
    sub_agent_node_keys: list[str] = Field(
        default_factory=list, description="子Agent 节点 key 列表"
    )
    sub_agent_configs: dict[str, dict] = Field(
        default_factory=dict, description="子Agent 节点配置"
    )
    question_node_keys: list[str] = Field(
        default_factory=list, description="问题反问节点 key 列表"
    )
    question_configs: dict[str, dict] = Field(
        default_factory=dict, description="问题反问节点配置"
    )


def get_connected_tool_nodes(
    flow: FlowLike | Flow, llm_node_key: str
) -> list[FlowNode]:
    """
    获取连接到LLM节点的所有工具节点（MCP、Human、API、Knowledge和Skill）

    Args:
        flow: 流程对象
        llm_node_key: LLM节点key

    Returns:
        工具节点列表
    """
    return [node for node, _ in get_connected_tool_edges(flow, llm_node_key)]


def get_connected_tool_edges(
    flow: FlowLike | Flow, llm_node_key: str
) -> list[tuple[FlowNode, FlowEdge]]:
    """
    获取连接到LLM节点的所有工具节点及其边

    按 edge.condition.intent_filters 过滤，仅返回当前意图匹配的工具。

    Args:
        flow: 流程对象
        llm_node_key: LLM节点key

    Returns:
        (工具节点, 边对象) 列表，已按意图条件过滤
    """
    tool_edge_pairs: list[tuple[FlowNode, FlowEdge]] = []
    node_map = {n.node_key: n for n in flow.nodes}

    base_key = re.sub(r"_iter_\d+$", "", llm_node_key)

    for edge in flow.edges:
        if not hasattr(edge, "target_node_key") or not hasattr(edge, "source_handle"):
            continue

        if edge.target_node_key != base_key:
            continue

        if edge.source_handle != "tools":
            continue

        source_node = node_map.get(edge.source_node_key)
        node_type_list = [member.value for member in NodeType]
        if source_node and source_node.node_type in node_type_list:
            tool_edge_pairs.append((source_node, edge))

    return tool_edge_pairs


def filter_tools_by_intent(
    tool_edge_pairs: list[tuple[FlowNode, FlowEdge]],
    state: FlowState,
) -> list[tuple[FlowNode, FlowEdge]]:
    """
    根据边上的 intent_filters 条件过滤工具节点

    边 condition 格式：
        {
            "intent_filters": {
                "router_node_key_1": ["intent_a", "intent_b"],
                "router_node_key_2": ["intent_c"]
            },
            "filter_logic": "and"  # 或 "or"，默认 "and"
        }

    - 同一路由器的多个 intent key 之间是 OR 关系
    - 不同路由器之间由 filter_logic 控制（AND / OR）
    - intent_filters 为空 / condition 为 null → 不过滤（始终启用）

    Args:
        tool_edge_pairs: (工具节点, 边) 列表
        state: 当前流程状态

    Returns:
        过滤后的 (工具节点, 边) 列表
    """
    result: list[tuple[FlowNode, FlowEdge]] = []

    for tool_node, edge in tool_edge_pairs:
        condition = edge.condition
        if not condition or not isinstance(condition, dict):
            result.append((tool_node, edge))
            continue

        intent_filters = condition.get("intent_filters")
        if not intent_filters:
            result.append((tool_node, edge))
            continue

        logic = condition.get("filter_logic", "and")
        match_results: list[bool] = []

        for router_key, allowed_values in intent_filters.items():
            if not allowed_values:
                continue
            var_name = f"_intent_route_{router_key}"
            actual = state.get_variable(var_name, "")
            match_results.append(actual in allowed_values)

        if not match_results:
            # 所有过滤器都为空列表 → 不过滤
            result.append((tool_node, edge))
            continue

        if logic == "or":
            if any(match_results):
                result.append((tool_node, edge))
        else:
            if all(match_results):
                result.append((tool_node, edge))

    return result


def get_node_label(node: FlowNode) -> str:
    """获取节点中文标签"""
    from app.constants.node_types import NODE_TYPE_LABELS

    return NODE_TYPE_LABELS.get(node.node_type, node.node_name or node.node_key)


def resolve_connected_tool_info(
    flow: FlowLike | Flow,
    llm_node_key: str,
) -> list[dict]:
    """
    解析连接到LLM节点的所有工具信息（不执行，仅元数据）

    调用各 handler 的 get_tool_info 获取工具名和描述。

    Args:
        flow: 流程对象
        llm_node_key: LLM节点key

    Returns:
        list[dict]: [{node_key, node_type, node_label, tools: [{name, description}]}]
    """
    tool_nodes = get_connected_tool_nodes(flow, llm_node_key)
    return resolve_tool_node_info(tool_nodes)


def resolve_tool_node_info(tool_nodes: list[FlowNode]) -> list[dict]:
    """解析给定工具节点的工具名称和描述，不依赖已保存的边。"""
    from app.agent_flow.handler_registry import NodeHandlerRegistry

    result: list[dict] = []

    for tool_node in tool_nodes:
        handler_cls = NodeHandlerRegistry.get_handler_class(tool_node.node_type)
        if not handler_cls:
            handler_cls = NodeHandlerRegistry._get_factory_handler_class(
                tool_node.node_type
            )
        if not handler_cls:
            continue

        tools = handler_cls.get_tool_info(tool_node)
        if tools:
            result.append(
                {
                    "node_key": tool_node.node_key,
                    "node_type": tool_node.node_type,
                    "node_label": get_node_label(tool_node),
                    "tools": tools,
                }
            )

    return result


def is_tool_edge(edge) -> bool:
    """
    判断边是否是工具边

    Args:
        edge: 边对象

    Returns:
        是否是工具边
    """
    if not hasattr(edge, "source_handle"):
        return False
    return edge.source_handle == "tools"

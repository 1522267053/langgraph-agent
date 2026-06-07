"""
工具连接解析器

解析流程中节点之间的工具连接关系，为LLM节点获取可用的工具
"""

import re

from app.models.flow_node import FlowNode, NodeType


class FlowLike:
    """流程对象协议"""

    nodes: list[FlowNode]
    edges: list


class LlmToolConfig:
    """LLM节点的工具配置"""

    def __init__(self):
        self.mcp_server_ids: list[int] = []
        self.enable_human_assist: bool = False
        self.human_assist_config: dict = {}
        self.human_node_keys: list[str] = []
        self.api_node_keys: list[str] = []
        self.api_configs: dict[str, dict] = {}
        self.knowledge_node_keys: list[str] = []
        self.knowledge_configs: dict[str, dict] = {}
        self.skill_node_keys: list[str] = []
        self.skill_configs: dict[str, dict] = {}
        self.memory_node_keys: list[str] = []
        self.memory_configs: dict[str, dict] = {}


def get_connected_tool_nodes(flow: FlowLike, llm_node_key: str) -> list[FlowNode]:
    """
    获取连接到LLM节点的所有工具节点（MCP、Human、API、Knowledge和Skill）

    Args:
        flow: 流程对象
        llm_node_key: LLM节点key

    Returns:
        工具节点列表
    """
    tool_nodes = []
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
            tool_nodes.append(source_node)

    return tool_nodes


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

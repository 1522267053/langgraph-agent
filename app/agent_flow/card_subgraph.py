"""
能力卡片子图构建器

将引用的外部流程构建为独立的 LangGraph StateGraph 子图，
供 CardNodeHandler 通过 astream 调用。
"""

from app.agent_flow.subgraph_builder import BaseSubgraphBuilder
from app.models.flow_node import FlowNode


class CardSubgraphBuilder(BaseSubgraphBuilder):
    """能力卡片子图构建器。

    与 BaseSubgraphBuilder 的差异：
    - 过滤嵌套子节点（card_prefix 下一级仍有 __ 的节点属于嵌套子流程）
    """

    def _collect_nodes(self) -> dict[str, FlowNode]:
        """收集子图节点，过滤嵌套子节点（只保留直接子节点）。"""
        card_prefix = f"{self.parent_key}__"
        nodes: dict[str, FlowNode] = {}

        for node in self.sub_nodes:
            remainder = node.node_key.removeprefix(card_prefix)
            if "__" in remainder:
                continue
            nodes[node.node_key] = node

        return nodes

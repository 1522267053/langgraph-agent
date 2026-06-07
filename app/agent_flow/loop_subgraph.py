"""
循环体子图构建器

将循环节点的子图节点/边构建为独立的 LangGraph StateGraph 子图，
供 LoopNodeHandler 通过 ainvoke 调用。
"""

import copy
from typing import Any

from app.agent_flow.subgraph_builder import BaseSubgraphBuilder
from app.agent_flow.flow_context import FlowState
from app.models.flow_node import FlowNode


class LoopSubgraphBuilder(BaseSubgraphBuilder):
    """循环体子图构建器。

    与 BaseSubgraphBuilder 的差异：
    - 执行时为每个节点 key 追加 _iter_{loop_index} 后缀
    - end 节点的 output_variables source 自动重写为带迭代后缀的引用
    - writer 事件携带 loop_index 和 loop_node_name
    """

    def _prepare_node(self, node: FlowNode, state: FlowState) -> FlowNode:
        """为当前迭代创建节点副本，追加 _iter_ 后缀并重写 end 节点 source。"""
        loop_index = (
            state.variables.get("loop_index", 0)
            if isinstance(state.variables, dict)
            else 0
        )

        iter_node = copy.copy(node)
        iter_node.node_key = f"{node.node_key}_iter_{loop_index}"

        if node.node_type == "end" and iter_node.base_config:
            output_vars = iter_node.base_config.get("output_variables", [])
            if output_vars:
                new_output_vars = []
                for var in output_vars:
                    new_var = dict(var)
                    source = new_var.get("source", "")
                    if source and "_iter_" not in source:
                        new_var["source"] = _rewrite_source_for_iteration(
                            source, loop_index
                        )
                    new_output_vars.append(new_var)
                iter_node.base_config = {
                    **iter_node.base_config,
                    "output_variables": new_output_vars,
                }

        return iter_node

    def _get_event_meta(self, node: FlowNode, state: FlowState) -> dict[str, Any]:
        """循环体事件携带 parent_path 和 loop_index。"""
        loop_index = (
            state.variables.get("loop_index", 0)
            if isinstance(state.variables, dict)
            else 0
        )
        return {
            "parent_path": self.parent_path,
            "loop_index": loop_index,
        }


def _rewrite_source_for_iteration(source: str, loop_index: int) -> str:
    """将 source 中的子图节点 key 替换为带迭代后缀的 key。"""
    if not source.startswith("nodes."):
        return source
    parts = source.split(".", 2)
    if len(parts) < 3:
        return source
    node_ref = parts[1]
    if "_iter_" in node_ref:
        return source
    return f"nodes.{node_ref}_iter_{loop_index}.{parts[2]}"

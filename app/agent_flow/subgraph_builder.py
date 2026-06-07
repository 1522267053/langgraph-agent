"""
子图构建器基类

为能力卡片（CardSubgraphBuilder）和循环节点（LoopSubgraphBuilder）提供通用的
StateGraph 子图构建逻辑。子类通过重写钩子方法实现各自的特殊行为。
"""

import logging
from typing import Any, Callable, Optional

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langchain_core.runnables import RunnableConfig

from app.agent_flow.edge_router import wire_edges
from app.agent_flow.flow_context import FlowState
from app.agent_flow.node_handlers.base_handler import BaseNodeHandler
from app.agent_flow.variable_resolver import variable_resolver
from app.models.flow_edge import FlowEdge
from app.models.flow_node import FlowNode

logger = logging.getLogger(__name__)


class BaseSubgraphBuilder:
    """子图构建器基类。

    提供通用的子图构建流程：节点过滤 → 边过滤 → 注册节点 → 连线 → 编译。
    子类通过重写 _collect_nodes、_prepare_node、_get_event_meta 实现差异化。

    Args:
        parent_key: 父节点 key（card_key 或 loop_key）
        sub_nodes: 子图节点列表
        sub_edges: 子图边列表
        handler_map: 节点处理器映射 {node_type: handler}
        llm_handler: LLM 处理器实例（工厂注册的特殊处理）
        parent_node_name: 父节点显示名称
    """

    def __init__(
        self,
        parent_key: str,
        sub_nodes: list[FlowNode],
        sub_edges: list[FlowEdge],
        handler_map: dict[str, BaseNodeHandler],
        llm_handler: Optional[BaseNodeHandler] = None,
        parent_node_name: str = "",
        parent_path: str = "",
    ):
        self.parent_key = parent_key
        self.sub_nodes = sub_nodes
        self.sub_edges = sub_edges
        self.handler_map = handler_map
        self.llm_handler = llm_handler
        self.parent_node_name = parent_node_name
        self.parent_path = parent_path

    def build(self) -> CompiledStateGraph:
        """构建并编译子图 StateGraph。

        Returns:
            编译后的 CompiledStateGraph
        """
        nodes = self._collect_nodes()
        edges = self._collect_edges(nodes)
        start_key, end_key = self._find_terminals(nodes)

        if not start_key:
            raise ValueError(f"[{self.parent_key}] 子图缺少 start 节点")
        if not end_key:
            raise ValueError(f"[{self.parent_key}] 子图缺少 end 节点")

        workflow = StateGraph(FlowState)

        for node_key, node in nodes.items():
            handler = self._get_handler(node.node_type)
            if not handler:
                logger.warning(
                    f"[{self.parent_key}] 子图节点 {node_key}"
                    f"({node.node_type}) 无处理器，跳过"
                )
                continue
            workflow.add_node(node_key, self._make_node_func(handler, node))

        workflow.add_edge(START, start_key)
        wire_edges(workflow, edges, nodes, iteration_guard=False)
        workflow.add_edge(end_key, END)

        return workflow.compile()

    # ---- 可重写钩子 ----

    def _collect_nodes(self) -> dict[str, FlowNode]:
        """收集子图节点。默认保留全部节点，子类可覆写过滤逻辑。"""
        return {n.node_key: n for n in self.sub_nodes}

    def _prepare_node(self, node: FlowNode, state: FlowState) -> FlowNode:
        """在执行前转换节点（如循环添加 _iter_ 后缀）。默认原样返回。"""
        return node

    def _get_event_meta(self, node: FlowNode, state: FlowState) -> dict[str, Any]:
        """生成 writer 事件的额外元数据。默认返回 parent_path。"""
        return {"parent_path": self.parent_path}

    # ---- 通用实现 ----

    def _collect_edges(self, nodes: dict[str, FlowNode]) -> list[FlowEdge]:
        """过滤工具边和无效边，返回有效边列表。"""
        edges: list[FlowEdge] = []
        for edge in self.sub_edges:
            if hasattr(edge, "source_handle") and edge.source_handle == "tools":
                continue
            if edge.source_node_key not in nodes or edge.target_node_key not in nodes:
                parent_prefix = f"{self.parent_key}__"
                src_nested = "__" in edge.source_node_key.removeprefix(parent_prefix)
                tgt_nested = "__" in edge.target_node_key.removeprefix(parent_prefix)
                if not (src_nested and tgt_nested):
                    logger.warning(
                        f"[{self.parent_key}] 无效边: "
                        f"{edge.source_node_key} -> {edge.target_node_key}，"
                        f"可用节点: {list(nodes.keys())}"
                    )
                continue
            edges.append(edge)
        return edges

    def _find_terminals(
        self, nodes: dict[str, FlowNode]
    ) -> tuple[Optional[str], Optional[str]]:
        """查找 start 和 end 节点 key。"""
        start_key = None
        end_key = None
        for key, node in nodes.items():
            if node.node_type == "start":
                start_key = key
            elif node.node_type == "end":
                end_key = key
        return start_key, end_key

    def _make_node_func(self, handler: BaseNodeHandler, node: FlowNode) -> Callable:
        """创建节点执行函数闭包，封装 input/output 收集和事件发射。"""

        async def node_func(
            state: FlowState,
            writer,
            *,
            config: Optional[RunnableConfig] = None,
        ):
            effective_node = self._prepare_node(node, state)

            input_content = None
            if handler:
                input_content = handler.get_input_content(
                    effective_node, state, variable_resolver
                )

            meta = self._get_event_meta(effective_node, state)
            if writer:
                writer(
                    {
                        "type": "sub_node_start",
                        "node_key": effective_node.node_key,
                        "node_type": effective_node.node_type,
                        "node_name": effective_node.node_name,
                        "input_data": input_content,
                        **meta,
                    }
                )

            try:
                result = await handler.execute(
                    effective_node, state, config=config, writer=writer
                )
            except Exception as exc:
                if writer:
                    writer(
                        {
                            "type": "sub_node_done",
                            "node_key": effective_node.node_key,
                            "node_type": effective_node.node_type,
                            "node_name": effective_node.node_name,
                            "error": str(exc),
                            **meta,
                        }
                    )
                raise

            output_content = None
            if handler and isinstance(result, FlowState):
                output_content = handler.get_output_content(
                    effective_node, result, variable_resolver
                )

            if writer:
                writer(
                    {
                        "type": "sub_node_done",
                        "node_key": effective_node.node_key,
                        "node_type": effective_node.node_type,
                        "node_name": effective_node.node_name,
                        "output_data": output_content,
                        **meta,
                    }
                )

            if not isinstance(result, FlowState):
                result["visited_nodes"] = [effective_node.node_key]
                return result

            return _state_to_dict(result, effective_node.node_key)

        return node_func

    def _get_handler(self, node_type: str) -> Optional[BaseNodeHandler]:
        """获取节点处理器，LLM 优先使用注入的 llm_handler。"""
        if node_type == "llm" and self.llm_handler:
            return self.llm_handler
        return self.handler_map.get(node_type)


def _state_to_dict(result: FlowState, node_key: str) -> dict[str, Any]:
    """将 FlowState 转换为 LangGraph 节点返回的 dict。"""
    return {
        "output_data": result.output_data,
        "variables": result.variables,
        "errors": result.errors,
        "current_node": result.current_node,
        "iteration_count": result.iteration_count,
        "max_iterations": result.max_iterations,
        "visited_nodes": [node_key],
        "conversation_messages": result.conversation_messages,
    }

"""
边路由工具函数

为 StateGraph 提供统一的边路由逻辑，支持条件节点路由、表达式条件边和普通边。
主图和子图共用，通过 iteration_guard 参数控制是否启用迭代保护。
"""

from simpleeval import simple_eval, EvalWithCompoundTypes
from langgraph.graph import END, StateGraph

from app.agent_flow.flow_context import FlowState
from app.models.flow_edge import FlowEdge
from app.models.flow_node import FlowNode


def wire_edges(
    workflow: StateGraph,
    edges: list[FlowEdge],
    nodes: dict[str, FlowNode],
    *,
    iteration_guard: bool = False,
) -> None:
    """将边列表添加到 StateGraph，自动按源节点分组并选择路由策略。

    对同一目标有多条无条件入边的情况，使用 add_edge([sources], target) 创建
    NamedBarrierValue 屏障，确保所有前驱完成后才触发目标节点。

    Args:
        workflow: LangGraph StateGraph 实例
        edges: 已过滤的边列表（不含工具边和嵌套子图边）
        nodes: 节点映射 {node_key: FlowNode}
        iteration_guard: 是否启用迭代保护（主图为 True，子图为 False）
    """
    edges_by_source: dict[str, list[FlowEdge]] = {}
    seen_edges: set[tuple[str, str]] = set()

    for edge in edges:
        source = edge.source_node_key
        target = edge.target_node_key
        edge_id = (source, target)
        if edge_id in seen_edges:
            continue
        seen_edges.add(edge_id)
        if source not in edges_by_source:
            edges_by_source[source] = []
        edges_by_source[source].append(edge)

    # ---- 先处理条件路由和表达式条件边（立即添加，不参与屏障） ----
    # ---- 同时记录条件路由的目标节点集合，用于判断互斥分支 ----
    unconditional_edges: list[tuple[str, str]] = []
    # 条件路由器 → 其直接目标节点集合（互斥分支的来源）
    router_targets_map: dict[str, set[str]] = {}

    from app.models.flow_node import NodeType

    for source_key, edge_list in edges_by_source.items():
        source_node = nodes.get(source_key)

        if source_node and source_node.node_type == NodeType.CONDITION.value:
            _add_condition_edges(
                workflow, source_key, edge_list, iteration_guard=iteration_guard
            )
            router_targets_map[source_key] = {e.target_node_key for e in edge_list}
        elif source_node and source_node.node_type == NodeType.INTENT_ROUTER.value:
            _add_intent_route_edges(
                workflow, source_key, edge_list, iteration_guard=iteration_guard
            )
            router_targets_map[source_key] = {e.target_node_key for e in edge_list}
        elif any(e.condition for e in edge_list):
            _add_conditional_edges(
                workflow, source_key, edge_list, iteration_guard=iteration_guard
            )
            router_targets_map[source_key] = {e.target_node_key for e in edge_list}
        else:
            for edge in edge_list:
                unconditional_edges.append((source_key, edge.target_node_key))

    # ---- 按目标分组 ----
    # ---- 多入边默认创建屏障，但互斥分支（同一条件路由器的所有目标汇聚）应逐条添加 ----
    target_sources: dict[str, list[str]] = {}
    for source_key, target_key in unconditional_edges:
        target_sources.setdefault(target_key, []).append(source_key)

    for target_key, sources in target_sources.items():
        if len(sources) > 1:
            sources_set = set(sources)
            # 检查这些源是否全部来自同一个条件路由器（互斥分支）
            is_mutually_exclusive = any(
                targets == sources_set for targets in router_targets_map.values()
            )
            if is_mutually_exclusive:
                for s in sources:
                    workflow.add_edge(s, target_key)
            else:
                workflow.add_edge(sources, target_key)
        else:
            workflow.add_edge(sources[0], target_key)


def _add_condition_edges(
    workflow: StateGraph,
    source_key: str,
    edges: list[FlowEdge],
    *,
    iteration_guard: bool = False,
) -> None:
    """条件节点路由：根据 _condition_branch 变量值匹配 source_handle。"""

    def route_func(state: FlowState) -> str:
        if iteration_guard and state.iteration_count >= state.max_iterations:
            return END

        branch = state.get_variable("_condition_branch", "true")

        for edge in edges:
            if edge.source_handle == branch:
                if iteration_guard and edge.target_node_key in state.visited_nodes:
                    state.iteration_count += 1
                return edge.target_node_key

        for edge in edges:
            if not edge.source_handle or edge.source_handle == "default":
                if iteration_guard and edge.target_node_key in state.visited_nodes:
                    state.iteration_count += 1
                return edge.target_node_key

        return END

    targets: dict[str, str] = {
        edge.target_node_key: edge.target_node_key for edge in edges
    }
    targets[END] = END

    workflow.add_conditional_edges(source_key, route_func, targets)


def _add_intent_route_edges(
    workflow: StateGraph,
    source_key: str,
    edges: list[FlowEdge],
    *,
    iteration_guard: bool = False,
) -> None:
    """意图路由节点路由：根据 _intent_route 变量值匹配 source_handle

    source_handle 命名约定：每个意图的 key 作为 handle id，default 作为兜底。
    """

    def route_func(state: FlowState) -> str:
        if iteration_guard and state.iteration_count >= state.max_iterations:
            return END

        chosen = state.get_variable("_intent_route", "default")

        # 1. 精确匹配 intent key
        for edge in edges:
            if edge.source_handle == chosen:
                if iteration_guard and edge.target_node_key in state.visited_nodes:
                    state.iteration_count += 1
                return edge.target_node_key

        # 2. fallback default
        for edge in edges:
            if not edge.source_handle or edge.source_handle == "default":
                if iteration_guard and edge.target_node_key in state.visited_nodes:
                    state.iteration_count += 1
                return edge.target_node_key

        return END

    targets: dict[str, str] = {
        edge.target_node_key: edge.target_node_key for edge in edges
    }
    targets[END] = END

    workflow.add_conditional_edges(source_key, route_func, targets)


def _add_conditional_edges(
    workflow: StateGraph,
    source_key: str,
    edges: list[FlowEdge],
    *,
    iteration_guard: bool = False,
) -> None:
    """表达式条件边路由：按条件优先级排序，simpleeval 求值匹配。"""

    sorted_edges = sorted(edges, key=lambda e: 0 if e.condition else 1)

    def route_func(state: FlowState) -> str:
        if iteration_guard and state.iteration_count >= state.max_iterations:
            return END

        for edge in sorted_edges:
            condition = edge.condition
            if not condition:
                if iteration_guard and edge.target_node_key in state.visited_nodes:
                    state.iteration_count += 1
                return edge.target_node_key

            condition_type = condition.get("type", "expression")
            if condition_type != "expression":
                continue

            expression = condition.get("expression", "true")
            try:
                evaluator = EvalWithCompoundTypes(
                    names={
                        "variables": state.variables,
                        "input": state.input_data,
                        "output": state.output_data,
                        "iteration_count": state.iteration_count,
                        "max_iterations": state.max_iterations,
                    }
                )
                if bool(simple_eval(expression, evaluator)):
                    if iteration_guard and edge.target_node_key in state.visited_nodes:
                        state.iteration_count += 1
                    return edge.target_node_key
            except Exception:
                continue

        return END

    targets: dict[str, str] = {
        edge.target_node_key: edge.target_node_key for edge in edges
    }
    targets[END] = END

    workflow.add_conditional_edges(source_key, route_func, targets)

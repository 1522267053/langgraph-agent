"""
执行服务基类

提供 FlowExecutorService 和 AgentExecutorService 的公共功能：
1. 获取流程详情（节点和边）
2. 构建执行图
3. 流程节点验证
"""

from typing import Any, List, Optional, Set

from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_flow.execution_context import ExecutionContext, set_execution_context
from app.agent_flow.graph_builder import GraphBuilder
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.mysql_checkpointer import AsyncMySQLSaver
from app.config.database import AsyncSessionLocal
from app.models.flow import Flow, FlowType
from app.models.flow_edge import FlowEdge
from app.models.flow_node import BASIC_NODE_TYPES, ExpandedFlow, FlowNode, NodeType


class BaseExecutorService:
    """
    执行服务基类

    提供流程执行的公共方法，子类可重写特定行为
    """

    def __init__(self, checkpointer: Optional[AsyncMySQLSaver] = None):
        self._checkpointer = checkpointer or AsyncMySQLSaver()

    async def _get_flow_with_details(
        self, db: AsyncSession, flow_id: int, flow_type: Optional[FlowType] = None
    ) -> Optional[Flow]:
        """
        获取Flow及其节点和边

        Args:
            db: 数据库会话
            flow_id: 流程ID
            flow_type: 流程类型过滤（可选）

        Returns:
            Flow对象（包含nodes和edges），不存在则返回None
        """
        if flow_type:
            query = select(Flow).where(
                Flow.id == flow_id,
                Flow.flow_type == flow_type.value,
                Flow.is_delete == 0,
            )
        else:
            query = select(Flow).where(Flow.id == flow_id, Flow.is_delete == 0)

        result = await db.execute(query)
        flow = result.scalar_one_or_none()
        if not flow:
            return None

        nodes_query = select(FlowNode).where(
            FlowNode.flow_id == flow_id, FlowNode.is_delete == 0
        )
        nodes_result = await db.execute(nodes_query)
        flow.nodes = list(nodes_result.scalars().all())

        edges_query = select(FlowEdge).where(
            FlowEdge.flow_id == flow_id, FlowEdge.is_delete == 0
        )
        edges_result = await db.execute(edges_query)
        flow.edges = list(edges_result.scalars().all())

        return flow

    def _build_graph(
        self,
        flow: Any,
        execution_id: int,
        conversation_service: Any,
        session_id: Optional[int] = None,
    ) -> CompiledStateGraph:
        """
        构建 StateGraph

        Args:
            flow: 流程对象（Flow 或 ExpandedFlow）
            execution_id: 执行记录ID
            conversation_service: 对话服务
            session_id: 会话ID（Agent场景使用）

        Returns:
            CompiledStateGraph: 编译后的图
        """
        builder = GraphBuilder(flow)
        # 注册非llm节点
        for node_type in BASIC_NODE_TYPES:
            handler = NodeHandlerRegistry.create(node_type.value)
            if handler:
                builder.register_handler(node_type.value, handler)

        node_type_list = [member.value for member in BASIC_NODE_TYPES]

        llm_kwargs: dict = {
            "flow": flow,
            "db_session_factory": AsyncSessionLocal,
            "execution_id": execution_id,
            "conversation_service": conversation_service,
            "handler_registry": {
                node_type: NodeHandlerRegistry.create(node_type)
                for node_type in node_type_list
            },
        }
        if session_id is not None:
            llm_kwargs["session_id"] = session_id
        # 创建llm节点
        llm_handler = NodeHandlerRegistry.create(NodeType.LLM.value, **llm_kwargs)
        if llm_handler:
            builder.register_handler(NodeType.LLM.value, llm_handler)

        # 设置执行上下文（供循环节点获取 handler 和 expanded_flow）
        ctx = ExecutionContext()
        ctx.handler_map = dict(builder._node_handlers)
        ctx.llm_kwargs = llm_kwargs
        ctx.expanded_flow = flow
        ctx.execution_id = execution_id
        ctx.flow_id = flow.id
        set_execution_context(ctx)

        graph = builder.build(checkpointer=self._checkpointer)
        return graph

    def _validate_flow_nodes(self, flow: Flow) -> None:
        """
        验证流程节点（start/end存在性）

        Args:
            flow: 流程对象

        Raises:
            ValueError: 缺少必要节点时抛出
        """
        start_node = next(
            (n for n in flow.nodes if n.node_type == NodeType.START.value), None
        )
        if not start_node:
            raise ValueError("流程缺少开始节点")

        end_node = next(
            (n for n in flow.nodes if n.node_type == NodeType.END.value), None
        )
        if not end_node:
            raise ValueError("流程缺少结束节点")

    async def _expand_card_nodes(
        self, db: AsyncSession, flow: Flow, visited_flows: Optional[Set[int]] = None
    ) -> ExpandedFlow:
        """
        预处理流程节点（保留循环子节点的边去重）

        Card 节点由 CardNodeHandler 在运行时动态加载执行，不再静态展开。
        Loop 节点由 LoopNodeHandler 在执行时动态构建子图。
        此方法仅做循环子边去重，确保循环节点的子图边正确保留。

        Args:
            db: 数据库异步会话
            flow: 流程对象
            visited_flows: 未使用（保留接口兼容）

        Returns:
            ExpandedFlow: 展开后的流程数据容器
        """
        loop_prefixes = {
            f"{n.node_key}__" for n in flow.nodes if n.node_type == NodeType.LOOP.value
        }

        non_loop_node_keys = {n.node_key for n in flow.nodes if "__" not in n.node_key}

        deduplicated_edges: List[FlowEdge] = []
        seen_edge_ids: set[tuple[str, str, str]] = set()
        for edge in flow.edges:
            edge_id = (
                edge.source_node_key,
                edge.target_node_key,
                getattr(edge, "source_handle", None) or "",
            )
            if edge_id in seen_edge_ids:
                continue
            seen_edge_ids.add(edge_id)

            source = edge.source_node_key
            target = edge.target_node_key

            is_sub_edge = any(
                source.startswith(p) or target.startswith(p) for p in loop_prefixes
            )

            if is_sub_edge:
                deduplicated_edges.append(edge)
            elif source in non_loop_node_keys and target in non_loop_node_keys:
                deduplicated_edges.append(edge)

        return ExpandedFlow(flow, list(flow.nodes), deduplicated_edges)

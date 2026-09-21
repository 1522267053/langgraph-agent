"""
LangGraph 图构建器
将流程定义转换为 StateGraph
"""

import logging
from datetime import datetime
from typing import Callable, Optional, Protocol

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import StreamWriter
from langchain_core.runnables import RunnableConfig
from sqlalchemy import select

from app.agent_flow.flow_context import FlowState
from app.agent_flow.edge_router import wire_edges
from app.models.flow_node import FlowNode, NodeType
from app.models.flow_edge import FlowEdge

from app.agent_flow.node_handlers.base_handler import BaseNodeHandler

logger = logging.getLogger(__name__)


class FlowLike(Protocol):
    """流程对象协议（支持 Flow 和 ExpandedFlow）"""

    nodes: list[FlowNode]
    edges: list[FlowEdge]


class GraphBuilder:
    """
    LangGraph 图构建器
    将数据库中的流程定义转换为可执行的 StateGraph
    """

    def __init__(self, flow: FlowLike):
        self.flow = flow
        self.nodes: dict[str, FlowNode] = {}
        self.edges: list[FlowEdge] = []
        self._node_handlers: dict[str, BaseNodeHandler] = {}
        self._compiled_state_graph: Optional[CompiledStateGraph] = None
        self._build_node_map()
        self._build_edge_list()

    def _build_node_map(self) -> None:
        """构建节点映射"""
        if not self.flow.nodes:
            return
        for node in self.flow.nodes:
            self.nodes[node.node_key] = node

    def _build_edge_list(self) -> None:
        """构建边列表，过滤掉工具边和循环子图边"""
        if not self.flow.edges:
            return
        for edge in self.flow.edges:
            if hasattr(edge, "source_handle") and edge.source_handle == "tools":
                continue
            if "__" in edge.source_node_key or "__" in edge.target_node_key:
                continue
            self.edges.append(edge)

    def register_handler(self, node_type: str, handler: "BaseNodeHandler") -> None:
        """注册节点处理器"""
        self._node_handlers[node_type] = handler

    @staticmethod
    async def _mark_node_running(node_key: str) -> None:
        """节点开始执行时立即标记 NodeExecution 为 RUNNING

        [为什么在此处] 执行器原先只在 `updates` 流事件里写 RUNNING，而 LangGraph
        的 updates 模式**仅在节点执行完成后**产出事件——RUNNING 与 SUCCESS 被连着
        写入（同一 tick，无真实等待），DB 中 RUNNING 存活时间≈0，执行期间节点恒为
        Pending，前端看不到「执行中」标记。node_func 是 LangGraph 调用每个节点的
        唯一收口点，在此写入才能覆盖「节点正在执行」的真实时间窗。
        子图路径（subgraph_runner）已按自定义事件正确实现，本方法使主路径对齐。

        [取消竞态防护] 写入前检查中断标志：若执行已被取消（_cancel_running_nodes
        已将 Pending/Running 置为 CANCELLED），本次写入会「复活」已取消的记录，
        故直接跳过。检查与写入之间的窗口极小（内存标志 + 单条 UPDATE），
        且即使越过，后续 _process_node_update 会用 CANCELLED 覆盖，风险可接受。

        [容错] 任何失败仅 warning，绝不阻断节点执行（对齐 _update_node_execution_status）
        """
        from app.agent_flow.execution_context import get_execution_context

        try:
            ctx = get_execution_context()
            execution_id = ctx.execution_id if ctx else 0
            # Agent 模式 / 无执行记录场景（execution_id<=0）直接跳过
            if execution_id <= 0:
                return

            from app.services.interrupt_service import interrupt_service

            if interrupt_service.is_flow_interrupted(execution_id):
                logger.debug(
                    f"执行已取消，跳过 RUNNING 标记: execution_id={execution_id}, "
                    f"node_key={node_key}"
                )
                return

            from app.config.database import AsyncSessionLocal
            from app.models.node_execution import NodeExecution, NodeExecutionStatus

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(NodeExecution)
                    .where(
                        NodeExecution.flow_execution_id == execution_id,
                        NodeExecution.node_key == node_key,
                    )
                    .order_by(NodeExecution.id.desc())
                )
                ne = result.scalars().first()
                if ne and ne.status in (
                    NodeExecutionStatus.CANCELLED.value,
                    NodeExecutionStatus.SUCCESS.value,
                    NodeExecutionStatus.FAILED.value,
                ):
                    # 已处于终态（取消/已完成）时不回退为 RUNNING
                    return
                if ne:
                    ne.status = NodeExecutionStatus.RUNNING.value
                    ne.start_time = datetime.now()
                    await db.commit()
        except Exception as e:
            logger.warning(f"标记节点 RUNNING 失败（不影响执行）: {node_key} - {e}")

    def _create_node_function(self, node: FlowNode) -> Callable:
        """
        创建节点执行函数

        支持 RunnableConfig 和 StreamWriter 参数，以便 LangGraph 可以传递流式输出能力
        """
        node_type = node.node_type
        handler = self._node_handlers.get(node_type)
        node_key = node.node_key

        async def node_func(
            state: FlowState,
            writer: StreamWriter,
            *,
            config: Optional[RunnableConfig] = None,
        ) -> dict:
            if not handler:
                return {"visited_nodes": [node_key]}

            # 节点开始执行即标记 RUNNING（执行期间前端可见「执行中」）
            await GraphBuilder._mark_node_running(node_key)

            result = await handler.execute(node, state, config=config, writer=writer)
            if not isinstance(result, FlowState):
                result["visited_nodes"] = [node_key]
                return result

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

        return node_func

    def _find_node_keys(self) -> tuple[str | None, str | None]:
        """
        查找开始和结束节点key

        只查找主流程的 start/end 节点（节点 key 不包含 "__" 前缀分隔符）。
        子流程展开后的节点 key 格式为 "card_key__sub_node_key"。
        """
        start_node_key = None
        end_node_key = None
        for node_key, node in self.nodes.items():
            if "__" in node_key:
                continue
            if node.node_type == NodeType.START:
                start_node_key = node_key
            elif node.node_type == NodeType.END:
                end_node_key = node_key
        return start_node_key, end_node_key

    def build(self, checkpointer=None) -> CompiledStateGraph:
        """
        构建 CompiledStateGraph

        Args:
            checkpointer: 可选的 checkpointer 实例（用于持久化状态）

        Returns:
            CompiledStateGraph: 编译后的图
        """
        workflow = StateGraph(FlowState)

        start_node_key, end_node_key = self._find_node_keys()

        for node_key, node in self.nodes.items():
            if node.node_type == NodeType.MCP.value:
                continue
            if "__" in node_key:
                continue
            workflow.add_node(node_key, self._create_node_function(node))

        if not start_node_key:
            raise ValueError("流程缺少开始节点")

        if not end_node_key:
            raise ValueError("流程缺少结束节点")

        workflow.add_edge(START, start_node_key)

        wire_edges(workflow, self.edges, self.nodes, iteration_guard=True)

        workflow.add_edge(end_node_key, END)

        self._compiled_state_graph = workflow.compile(checkpointer=checkpointer)
        return self._compiled_state_graph

    def get_graph_mermaid(self) -> str:
        """获取图的 Mermaid 图表"""
        if self._compiled_state_graph:
            return self._compiled_state_graph.get_graph().draw_mermaid()
        else:
            return "图为空"

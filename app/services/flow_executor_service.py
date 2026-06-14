"""
流程执行服务模块

本模块负责流程的实际执行和执行记录管理，是流程编排系统的核心执行引擎。

主要职责:
    1. 流程启动: 根据流程ID加载流程定义并开始执行
    2. 节点调度: 使用 GraphBuilder 构建 StateGraph 并执行
    3. 能力卡片展开: 内联展开 card 类型节点引用的子流程
    4. 状态管理: 跟踪流程执行状态和节点执行状态
    5. 执行记录: 持久化执行过程数据，支持执行历史查询
    6. 异常处理: 捕获执行异常并记录错误信息
    7. 流式执行: 支持 SSE 实时推送执行事件
    8. 多轮人工交互: 使用 LangGraph interrupt 机制

执行流程:
    1. 加载流程定义（节点和边）
    2. 内联展开所有 card 类型节点（递归加载子流程）
    3. 创建流程执行记录
    4. 初始化执行上下文（FlowContext）
    5. 使用 GraphBuilder 构建 StateGraph（配置 checkpointer）
    6. 执行 StateGraph 并更新节点执行状态
    7. 完成/中断后更新流程执行状态

重构说明:
    - _execute_graph_stream: 公共图执行方法，统一处理首次执行和恢复执行
    - 状态从 checkpoint 恢复，本地 context 仅用于辅助
    - execution_steps 在查询时从 conversation_message 实时构建，不存储
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Any, AsyncGenerator, Dict

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import AsyncSessionLocal
from app.models.flow import Flow, FlowType
from app.models.flow_node import ExpandedFlow, FlowNode, NodeType
from app.models.flow_execution import FlowExecution, ExecutionStatus
from app.models.node_execution import NodeExecution, NodeExecutionStatus
from app.agent_flow.flow_context import FlowContext, FlowState
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.flow_event import FlowEventFactory
from app.agent_flow.variable_resolver import variable_resolver
from app.services.base_executor_service import BaseExecutorService
from app.services.conversation_service import ConversationService
from app.services.interrupt_service import interrupt_service
from app.utils.message_utils import deserialize_content


logger = logging.getLogger(__name__)

_STATE_FIELDS = (
    "input_data",
    "output_data",
    "variables",
    "errors",
    "visited_nodes",
    "iteration_count",
)


class FlowExecutorService(BaseExecutorService):
    """
    流程执行服务

    使用 GraphBuilder 构建 LangGraph StateGraph 并执行流程。
    使用 LangGraph interrupt 机制实现多轮人工交互。
    """

    def __init__(self):
        super().__init__()
        self._conversation_service = ConversationService()

    # ---- 公共方法 ----

    def _validate_flow_structure(self, flow: Flow) -> None:
        """
        验证流程结构完整性

        Args:
            flow: 流程对象

        Raises:
            ValueError: 流程结构不完整时抛出
        """
        self._validate_flow_nodes(flow)

        if not flow.input_schema or not flow.input_schema.get("fields"):
            raise ValueError("流程缺少输入参数配置")

        end_nodes = [n for n in flow.nodes if n.node_type == NodeType.END.value]
        for end_node in end_nodes:
            output_vars = (end_node.base_config or {}).get("output_variables", [])
            if not output_vars:
                raise ValueError(
                    f"结束节点[{end_node.node_name or end_node.node_key}]缺少输出变量配置"
                )

    async def create_execution(
        self,
        db: AsyncSession,
        flow_id: int,
        input_data: Optional[dict] = None,
        files: Optional[list] = None,
    ) -> FlowExecution:
        """
        创建执行记录（不执行流程）

        Args:
            db: 数据库异步会话
            flow_id: 流程ID
            input_data: 输入数据
            files: 附件文件信息

        Returns:
            FlowExecution: 流程执行记录
        """
        flow = await self._get_flow_with_details(db, flow_id)
        if not flow:
            raise ValueError(f"流程不存在: {flow_id}")

        expanded_flow = await self._expand_card_nodes(db, flow)

        execution = FlowExecution(
            flow_id=flow_id,
            status=ExecutionStatus.PENDING.value,
            input_data=input_data or {},
            start_time=datetime.now(),
            files=files,
        )
        db.add(execution)
        await db.commit()
        await db.refresh(execution)

        await self._create_node_executions(db, execution, expanded_flow)

        return execution

    async def get_execution(
        self, db: AsyncSession, execution_id: int
    ) -> Optional[FlowExecution]:
        """获取执行记录"""
        query = select(FlowExecution).where(FlowExecution.id == execution_id)
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_node_executions(
        self, db: AsyncSession, flow_execution_id: int
    ) -> List[NodeExecution]:
        """获取节点执行记录"""
        query = (
            select(NodeExecution)
            .where(NodeExecution.flow_execution_id == flow_execution_id)
            .order_by(NodeExecution.id)
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def cancel_execution(
        self, db: AsyncSession, execution_id: int
    ) -> Optional[FlowExecution]:
        """取消执行（支持 RUNNING 和 WAITING_HUMAN 状态）"""
        execution = await self.get_execution(db, execution_id)
        if not execution:
            return None

        cancellable = (
            ExecutionStatus.RUNNING.value,
            ExecutionStatus.WAITING_HUMAN.value,
        )
        if execution.status in cancellable:
            execution.status = ExecutionStatus.CANCELLED.value
            execution.end_time = datetime.now()
            execution.wait_data = None
            await db.commit()
            await self._cancel_running_nodes(execution_id)

        return execution

    @staticmethod
    def _map_input_to_schema(
        input_data: Optional[dict], input_schema: Optional[dict]
    ) -> dict:
        """
        根据 input_schema 映射输入数据，校验必填字段

        Args:
            input_data: 调用者传入的原始输入数据
            input_schema: 流程的输入参数定义（含 fields 列表）

        Returns:
            映射后的 input_data 字典

        Raises:
            ValueError: 必填字段缺失时抛出
        """
        raw = input_data or {}
        fields = (input_schema or {}).get("fields", [])

        if not fields:
            return dict(raw)

        mapped: dict = {}
        missing: list[str] = []

        for field in fields:
            name = field.get("name", "")
            required = field.get("required", False)
            if not name:
                continue
            if name in raw:
                mapped[name] = raw[name]
            elif required:
                missing.append(name)

        if missing:
            raise ValueError(f"缺少必填输入字段: {', '.join(missing)}")

        return mapped

    async def execute_stream(
        self,
        flow_id: int,
        input_data: Optional[dict] = None,
        files: Optional[list] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        流式执行流程，生成 SSE 事件流

        使用 LangGraph checkpointer 和 interrupt 机制实现多轮人工交互。

        Args:
            flow_id: 流程ID
            input_data: 输入数据
            files: 附件文件信息

        Yields:
            事件字典
        """
        async with AsyncSessionLocal() as db:
            flow = await self._get_flow_with_details(db, flow_id, FlowType.FLOW)
            if not flow:
                yield FlowEventFactory.error(f"流程不存在: {flow_id}")
                return

            try:
                self._validate_flow_structure(flow)
            except ValueError as e:
                yield FlowEventFactory.error(str(e))
                logger.exception(e)
                return

            try:
                input_data = self._map_input_to_schema(input_data, flow.input_schema)
            except ValueError as e:
                yield FlowEventFactory.error(str(e))
                return

            try:
                expanded_flow = await self._expand_card_nodes(db, flow)
            except Exception as e:
                yield FlowEventFactory.error(f"展开能力卡片失败: {str(e)}")
                logger.exception(e)
                return

            execution = FlowExecution(
                flow_id=flow_id,
                status=ExecutionStatus.RUNNING.value,
                input_data=input_data or {},
                start_time=datetime.now(),
                files=files,
            )
            db.add(execution)
            await db.commit()
            await db.refresh(execution)

            yield FlowEventFactory.flow_start(
                flow_id=flow_id, execution_id=execution.id
            )

            await self._create_node_executions(db, execution, expanded_flow)

            context = FlowContext(
                flow_id=flow_id, execution_id=execution.id, input_data=input_data
            )
            context.start()

            graph = self._build_graph(
                expanded_flow, execution.id, self._conversation_service
            )
            config = {
                "configurable": {
                    "thread_id": f"flow_{execution.id}",
                    "scope_type": "flow",
                }
            }

            async for event in self._execute_graph_stream(
                graph=graph,
                config=config,
                execution_id=execution.id,
                expanded_flow=expanded_flow,
                context=context,
                tool_node_keys=self._get_tool_node_keys(expanded_flow),
                resume_input=None,
                flow_name=flow.name or "",
            ):
                yield event
                if event.get("type") == "waiting_human":
                    return

    async def resume_execution(
        self, execution_id: int, human_input: str
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        恢复执行（多轮人工交互）

        使用 LangGraph Command(resume=...) 恢复执行，自动执行后续节点

        Args:
            execution_id: 执行记录ID
            human_input: 用户输入内容

        Yields:
            SSE事件
        """
        async with AsyncSessionLocal() as db:
            execution = await self.get_execution(db, execution_id)
            if not execution:
                yield FlowEventFactory.error(f"执行记录不存在: {execution_id}")
                return

            if execution.status != ExecutionStatus.WAITING_HUMAN.value:
                yield FlowEventFactory.error("当前执行不在等待人工输入状态")
                return

            # 乐观锁：CAS 更新，防止并发 resume
            stmt = (
                update(FlowExecution)
                .where(
                    FlowExecution.id == execution_id,
                    FlowExecution.status == ExecutionStatus.WAITING_HUMAN.value,
                )
                .values(
                    status=ExecutionStatus.RUNNING.value,
                    wait_for=None,
                    wait_data=None,
                )
            )
            result = await db.execute(stmt)
            if result.rowcount == 0:
                yield FlowEventFactory.error("执行已被其他请求抢占，请刷新后重试")
                return
            await db.refresh(execution)
            # 追加 human_input 到 human_inputs
            execution.human_inputs = execution.human_inputs or {}
            execution.human_inputs[f"human_input_{len(execution.human_inputs)}"] = (
                human_input
            )
            await db.commit()

            flow = await self._get_flow_with_details(
                db, execution.flow_id, FlowType.FLOW
            )
            if not flow:
                yield FlowEventFactory.error(f"流程不存在: {execution.flow_id}")
                return

            try:
                expanded_flow = await self._expand_card_nodes(db, flow)
            except Exception as e:
                yield FlowEventFactory.error(f"展开能力卡片失败: {str(e)}")
                return

            graph = self._build_graph(
                expanded_flow, execution.id, self._conversation_service
            )
            config = {
                "configurable": {
                    "thread_id": f"flow_{execution_id}",
                    "scope_type": "flow",
                }
            }

            context = await self._restore_context_from_checkpoint(
                execution, expanded_flow, config
            )

            async for event in self._execute_graph_stream(
                graph=graph,
                config=config,
                execution_id=execution.id,
                expanded_flow=expanded_flow,
                context=context,
                tool_node_keys=self._get_tool_node_keys(expanded_flow),
                resume_input=human_input,
                flow_name=flow.name or "",
            ):
                yield event

    # ---- 图执行核心 ----

    async def _execute_graph_stream(
        self,
        graph: CompiledStateGraph,
        config: dict,
        execution_id: int,
        expanded_flow: ExpandedFlow,
        context: FlowContext,
        tool_node_keys: set[str],
        resume_input: Optional[str] = None,
        flow_name: str = "",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        公共图执行方法

        统一处理首次执行和恢复执行的事件流
        所有数据库操作使用独立短连接，避免 CancelledError 时连接冲突

        Args:
            graph: 编译后的 StateGraph
            config: 执行配置（包含 thread_id）
            execution_id: 执行记录ID
            expanded_flow: 展开后的流程定义
            context: 执行上下文
            tool_node_keys: 工具节点 key 集合
            resume_input: 恢复执行时的用户输入（None 表示首次执行）

        Yields:
            SSE 事件字典
        """
        node_map = {n.node_key: n for n in expanded_flow.nodes}

        try:
            if resume_input is not None:
                config["configurable"]["_human_resume_input"] = resume_input
                stream_input = Command(resume=resume_input)
            else:
                stream_input = context.state.model_dump()

            async for event in graph.astream(
                input=stream_input, config=config, stream_mode=["updates", "custom"]
            ):
                if not isinstance(event, tuple) or len(event) != 2:
                    continue

                stream_mode_type, event_data = event

                # ---- custom 事件：实时 SSE 推送 ----
                if stream_mode_type == "custom":
                    custom_event = self._process_custom_event(event_data)
                    if custom_event:
                        yield custom_event
                    continue

                if stream_mode_type != "updates":
                    continue

                # ---- updates 事件：节点完成 / interrupt ----
                for node_key, result in event_data.items():
                    if node_key == "__interrupt__":
                        interrupt_data = result[0].value if result else {}
                        yield await self._handle_interrupt_and_save(
                            execution_id, interrupt_data, context
                        )
                        return

                    node = node_map.get(node_key)
                    if not node:
                        continue

                    if isinstance(result, dict):
                        if "variables" in result:
                            for k, v in result.get("variables", {}).items():
                                context.state.set_variable(k, v)
                        if "output_data" in result:
                            context.state.output_data.update(
                                result.get("output_data", {})
                            )
                        if "conversation_messages" in result:
                            context.state.conversation_messages.update(
                                result.get("conversation_messages", {})
                            )
                        if "errors" in result:
                            context.state.errors.extend(result.get("errors", []))

                    async for evt in self._process_node_update(
                        node, context, execution_id, tool_node_keys
                    ):
                        yield evt

            # ---- 流程完成 ----
            is_interrupted = self._check_interrupted(execution_id, context.state)
            async with AsyncSessionLocal() as db:
                execution = await self.get_execution(db, execution_id)
                if execution:
                    if (
                        not is_interrupted
                        and execution.status != ExecutionStatus.CANCELLED.value
                    ):
                        execution.status = ExecutionStatus.SUCCESS.value
                    if not execution.end_time:
                        execution.end_time = datetime.now()
                    execution.output_data = context.state.output_data
                    await db.commit()

                    # ---- WebSocket 广播执行完成通知 ----
                    duration_ms = None
                    if execution.start_time:
                        duration_ms = int(
                            (datetime.now() - execution.start_time).total_seconds()
                            * 1000
                        )

            interrupt_service.clear_flow_interrupted(execution_id)

            status = "cancelled" if is_interrupted else "success"
            try:
                from app.services.ws_manager import ws_manager

                await ws_manager.notify_execution_done(
                    execution_id=execution_id,
                    flow_id=expanded_flow.id,
                    flow_name=flow_name,
                    status=status,
                    source="flow",
                    duration_ms=duration_ms,
                )
            except Exception as ws_err:
                logger.warning(f"WebSocket 广播失败: {ws_err}")

            yield FlowEventFactory.flow_done(
                execution_id=execution_id,
                output_data=context.state.output_data,
                status=status,
            )

        except asyncio.CancelledError:
            logger.info(
                f"流程执行收到 CancelledError（SSE 断开 detach）: execution_id={execution_id}"
            )
            raise

        except Exception as e:
            error_msg = str(e)
            logger.exception(f"图执行失败: {e}")

            is_recoverable = self._is_recoverable_exception(e)

            try:
                async with AsyncSessionLocal() as db:
                    execution = await self.get_execution(db, execution_id)
                    if execution:
                        execution.status = ExecutionStatus.FAILED.value
                        execution.error_message = error_msg
                        execution.end_time = datetime.now()
                        await db.commit()
            except Exception as db_err:
                logger.warning(f"更新执行状态失败: {db_err}")

            if not is_recoverable:
                try:
                    thread_id = config.get("configurable", {}).get("thread_id")
                    if thread_id:
                        await self._checkpointer.adelete_thread(thread_id)
                        logger.info(
                            f"已清理不可恢复异常的 checkpoint: thread_id={thread_id}"
                        )
                except Exception as cleanup_error:
                    logger.warning(f"清理 checkpoint 失败: {cleanup_error}")

            # ---- WebSocket 广播执行失败通知 ----
            try:
                from app.services.ws_manager import ws_manager

                await ws_manager.notify_execution_done(
                    execution_id=execution_id,
                    flow_id=None,
                    flow_name=flow_name,
                    status="failed",
                    source="flow",
                    error_message=error_msg,
                )
            except Exception as ws_err:
                logger.warning(f"WebSocket 广播失败: {ws_err}")

            yield FlowEventFactory.error(error_msg, execution_id=execution_id)

    # ---- SSE 事件处理 ----

    @staticmethod
    def _process_custom_event(event_data: Any) -> Optional[Dict[str, Any]]:
        """
        处理 custom 事件，返回 SSE 事件字典

        仅负责实时推送给前端，不做内存状态跟踪
        """
        if hasattr(event_data, "to_dict"):
            return event_data.to_dict()
        if isinstance(event_data, dict):
            return event_data
        return None

    async def _process_node_update(
        self,
        node: FlowNode,
        context: FlowContext,
        execution_id: int,
        tool_node_keys: set[str],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        处理单个节点完成后的状态更新和事件生成

        包括：更新节点执行状态、生成 node_start / node_done 事件、
        处理中断标志、捕获节点级异常

        Args:
            node: 节点对象
            context: 执行上下文
            execution_id: 执行记录ID
            tool_node_keys: 工具节点 key 集合

        Yields:
            node_start / node_done 事件
        """
        if node.node_key in tool_node_keys:
            return

        if self._check_interrupted(execution_id, context.state):
            await self._update_node_execution_status(
                execution_id,
                node.node_key,
                NodeExecutionStatus.CANCELLED.value,
            )
            yield FlowEventFactory.node_done(
                node_key=node.node_key,
                node_type=node.node_type,
                output_data=None,
                error="执行被取消",
            )
            return

        try:
            input_content = self._get_node_input_content(node, context.state)
            await self._update_node_execution_status(
                execution_id,
                node.node_key,
                NodeExecutionStatus.RUNNING.value,
                input_content,
            )
            yield FlowEventFactory.node_start(
                node_key=node.node_key,
                node_type=node.node_type,
                node_name=node.node_name,
                input_data=input_content,
            )

            output_content = self._get_node_output_content(node, context.state)

            is_interrupted = self._check_interrupted(execution_id, context.state)
            node_status = (
                NodeExecutionStatus.CANCELLED.value
                if is_interrupted
                else NodeExecutionStatus.SUCCESS.value
            )
            await self._update_node_execution_status(
                execution_id,
                node.node_key,
                node_status,
                input_content,
                output_content,
                None if not is_interrupted else "执行被取消",
            )

            node_error = None
            for err in context.state.errors:
                if err.get("node_key") == node.node_key:
                    node_error = err.get("message")
                    break

            yield FlowEventFactory.node_done(
                node_key=node.node_key,
                node_type=node.node_type,
                output_data=output_content,
                error=node_error,
            )

        except Exception as e:
            error_msg = str(e)
            logger.exception(f"节点 {node.node_key} 执行失败: {e}")

            input_content = self._get_node_input_content(node, context.state)
            await self._update_node_execution_status(
                execution_id,
                node.node_key,
                NodeExecutionStatus.FAILED.value,
                input_content,
                None,
                error_msg,
            )
            context.state.errors.append(
                {"node_key": node.node_key, "message": error_msg}
            )

            yield FlowEventFactory.node_done(
                node_key=node.node_key,
                node_type=node.node_type,
                output_data=None,
                error=error_msg,
            )

    # ---- execution_steps 构建（数据源：conversation_message） ----

    @staticmethod
    def _convert_messages_to_steps(messages: list) -> Optional[list]:
        """
        将 ConversationMessage ORM 对象列表转换为 execution_steps 格式（1:1 映射）

        每条消息对应一个 step，与 conversation_message 记录条数一致。
        直接从 ORM 属性读取，无需经过 LangChain 消息转换。
        从 tool 消息的 status 字段回填 tool_calls 的 status。
        """
        steps: list[dict] = []

        for msg in messages:
            role = getattr(msg, "role", "")
            raw_content = getattr(msg, "content", "") or ""
            content = deserialize_content(raw_content)

            if role == "human":
                steps.append(
                    {
                        "step": len(steps) + 1,
                        "role": "human",
                        "content": content,
                    }
                )
            elif role == "ai":
                thinking = getattr(msg, "thinking", "") or ""
                tool_calls = getattr(msg, "tool_calls", None)

                steps.append(
                    {
                        "step": len(steps) + 1,
                        "role": "ai",
                        "thinking": thinking,
                        "content": content,
                        "tool_calls": tool_calls,
                    }
                )
            elif role == "tool":
                steps.append(
                    {
                        "step": len(steps) + 1,
                        "role": "tool",
                        "content": content,
                        "tool_call_id": getattr(msg, "tool_call_id", "") or "",
                        "tool_name": getattr(msg, "name", "") or "",
                        "status": getattr(msg, "status", "success") or "success",
                    }
                )

        # 回填 tool_calls 的 status：从匹配的 tool 消息 status 字段读取
        tool_call_status: dict[str, str] = {}
        for step in steps:
            if step.get("role") == "tool":
                tc_id = step.get("tool_call_id", "")
                if tc_id:
                    tool_call_status[tc_id] = step.get("status", "success")

        for step in steps:
            if step.get("role") == "ai" and step.get("tool_calls"):
                for tc in step["tool_calls"]:
                    if isinstance(tc, dict) and tc.get("id"):
                        tc["status"] = tool_call_status.get(tc["id"], "success")

        return steps if steps else None

    # ---- 辅助方法 ----

    @staticmethod
    def _get_tool_node_keys(flow: ExpandedFlow) -> set[str]:
        """获取通过 source_handle='tools' 边连接到 LLM 的工具节点 key 集合"""
        tool_keys: set[str] = set()
        for edge in flow.edges:
            if getattr(edge, "source_handle", None) == "tools":
                tool_keys.add(edge.source_node_key)
        return tool_keys

    @staticmethod
    def _check_interrupted(execution_id: int, state: FlowState) -> bool:
        """检查流程是否被中断（state 标志 + 全局中断服务）"""
        return state.is_interrupted or interrupt_service.is_flow_interrupted(
            execution_id
        )

    def _is_recoverable_exception(self, e: Exception) -> bool:
        """
        判断异常是否可恢复

        可恢复异常：用户可以修复后重试
        不可恢复异常：需要重新启动流程（FlowValidationError）

        Args:
            e: 异常对象

        Returns:
            bool: 是否可恢复
        """
        from app.agent_flow.exceptions import FlowValidationError

        return not isinstance(e, FlowValidationError)

    # ---- 状态恢复 ----

    async def _restore_context_from_checkpoint(
        self, execution: FlowExecution, expanded_flow: ExpandedFlow, config: dict
    ) -> FlowContext:
        """
        从 checkpoint 恢复执行上下文，失败时从执行记录恢复

        Args:
            execution: 执行记录
            expanded_flow: 展开后的流程
            config: checkpoint 配置

        Returns:
            FlowContext: 恢复后的执行上下文
        """
        context = FlowContext(
            flow_id=execution.flow_id,
            execution_id=execution.id,
            input_data=execution.input_data or {},
        )
        context.start()

        try:
            checkpoint_tuple = await self._checkpointer.aget_tuple(config)
            if checkpoint_tuple and checkpoint_tuple.checkpoint:
                channel_values = checkpoint_tuple.checkpoint.get("channel_values", {})
                for field in _STATE_FIELDS:
                    if field in channel_values:
                        setattr(context.state, field, channel_values[field])
                logger.info(f"从 checkpoint 恢复状态成功: execution_id={execution.id}")
                return context
        except Exception as e:
            logger.warning(f"从 checkpoint 恢复状态失败，使用执行记录: {e}")

        if execution.output_data:
            context.state.output_data = execution.output_data

        return context

    # ---- 取消处理 ----

    async def _cancel_running_nodes(
        self,
        execution_id: int,
    ) -> None:
        """取消所有处于运行中的节点"""
        try:
            async with AsyncSessionLocal() as db:
                query = select(NodeExecution).where(
                    NodeExecution.flow_execution_id == execution_id,
                    NodeExecution.status.in_(
                        [
                            NodeExecutionStatus.PENDING.value,
                            NodeExecutionStatus.RUNNING.value,
                        ]
                    ),
                )
                result = await db.execute(query)
                running_nodes = list(result.scalars().all())

                for node_exec in running_nodes:
                    node_exec.status = NodeExecutionStatus.CANCELLED.value
                    node_exec.error_message = "执行被取消"
                    node_exec.end_time = datetime.now()

                if running_nodes:
                    await db.commit()
                    logger.info(
                        f"已取消 {len(running_nodes)} 个运行中的节点: execution_id={execution_id}"
                    )
        except Exception as e:
            logger.warning(f"取消运行中节点失败: {e}")

    # ---- 数据库操作 ----

    async def _handle_interrupt_and_save(
        self,
        execution_id: int,
        interrupt_data: dict,
        context: FlowContext,
    ) -> dict:
        """
        处理 interrupt 事件并保存状态

        Args:
            execution_id: 执行记录ID
            interrupt_data: interrupt 数据
            context: 执行上下文

        Returns:
            waiting_human 事件
        """
        question = interrupt_data.get("question", "请提供输入")
        context_str = interrupt_data.get("context")
        interrupt_type = interrupt_data.get("type", "human_input_required")
        node_key = interrupt_data.get("node_key", "")

        wait_data = {
            "type": interrupt_type,
            "question": question,
            "context": context_str,
        }

        async with AsyncSessionLocal() as db:
            execution = await self.get_execution(db, execution_id)
            if execution:
                execution.status = ExecutionStatus.WAITING_HUMAN.value
                execution.wait_for = "human_input"
                execution.wait_data = wait_data
                execution.output_data = context.state.output_data
                await db.commit()

        return FlowEventFactory.waiting_human(
            execution_id=execution.id,
            node_key=node_key,
            question=question,
            context=context_str,
            wait_data=wait_data,
        )

    async def _create_node_executions(
        self, db: AsyncSession, execution: FlowExecution, flow: ExpandedFlow
    ) -> None:
        """创建节点执行记录，跳过工具节点和循环内部子节点"""
        tool_node_keys = self._get_tool_node_keys(flow)
        for node in flow.nodes:
            if node.node_key in tool_node_keys:
                continue
            if "__" in node.node_key:
                continue
            node_execution = NodeExecution(
                flow_execution_id=execution.id,
                node_key=node.node_key,
                node_type=node.node_type,
                node_name=node.node_name,
                status=NodeExecutionStatus.PENDING.value,
            )
            db.add(node_execution)
        await db.commit()

    async def _update_node_execution_status(
        self,
        flow_execution_id: int,
        node_key: str,
        status: int,
        input_data: Optional[dict] = None,
        output_data: Optional[dict] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """更新节点执行状态，使用独立短连接避免 CancelledError 时连接冲突"""
        try:
            async with AsyncSessionLocal() as db:
                query = select(NodeExecution).where(
                    NodeExecution.flow_execution_id == flow_execution_id,
                    NodeExecution.node_key == node_key,
                )
                result = await db.execute(query)
                node_execution = result.scalar_one_or_none()

                if node_execution:
                    node_execution.status = status
                    if input_data is not None:
                        node_execution.input_data = input_data
                    if output_data is not None:
                        node_execution.output_data = output_data
                    if error_message:
                        node_execution.error_message = error_message
                    if status in [
                        NodeExecutionStatus.SUCCESS.value,
                        NodeExecutionStatus.FAILED.value,
                        NodeExecutionStatus.CANCELLED.value,
                    ]:
                        node_execution.end_time = datetime.now()
                    elif status == NodeExecutionStatus.RUNNING.value:
                        node_execution.start_time = datetime.now()
                    await db.commit()
        except Exception as e:
            logger.warning(f"更新节点执行状态失败: {e}")

    # ---- 节点内容提取 ----

    def _get_node_input_content(
        self, node: FlowNode, state: FlowState
    ) -> Optional[dict]:
        """
        获取节点的输入内容（变量依赖）

        Args:
            node: 节点对象
            state: 流程状态

        Returns:
            输入内容字典，格式为 {变量名: 值}
        """
        return NodeHandlerRegistry.get_input_content(
            node.node_type, node, state, variable_resolver
        )

    def _get_node_output_content(
        self, node: FlowNode, state: FlowState
    ) -> Optional[dict]:
        """
        获取节点的输出内容（带变量名）

        Args:
            node: 节点对象
            state: 流程状态

        Returns:
            输出内容字典，格式为 {变量名: 值}，如果节点无需输出则返回 None
        """
        return NodeHandlerRegistry.get_output_content(
            node.node_type, node, state, variable_resolver
        )


flow_executor_service = FlowExecutorService()

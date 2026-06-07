"""
子图流式执行器

封装子图的 astream 调用、事件转发和 NodeExecution 记录管理，
供 CardNodeHandler 和 LoopNodeHandler 复用。
"""

import logging
from datetime import datetime
from typing import Callable, Optional

from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter
from sqlalchemy import select

from app.config.database import AsyncSessionLocal
from app.agent_flow.flow_context import FlowState
from app.agent_flow.flow_event import FlowEventFactory
from app.models.node_execution import NodeExecution, NodeExecutionStatus

logger = logging.getLogger(__name__)


class SubgraphRunner:
    """子图流式执行器。

    通过 node_name_formatter 回调适配不同父节点类型的节点名称格式。
    """

    @staticmethod
    async def stream(
        subgraph,
        state: FlowState,
        config: Optional[RunnableConfig],
        writer: Optional[StreamWriter],
        execution_id: int = 0,
        *,
        node_name_formatter: Callable[[dict], str],
        warn_no_values: bool = False,
    ) -> Optional[dict]:
        """流式调用子图，转发 custom 事件并管理 NodeExecution 记录。

        Args:
            subgraph: 已编译的 StateGraph 子图
            state: 当前流程状态
            config: LangGraph RunnableConfig
            writer: StreamWriter 用于发射事件
            execution_id: 流程执行 ID
            node_name_formatter: 节点名称格式化回调，接收 sub_node_start/done 事件 dict
            warn_no_values: 是否在无 values 事件时输出 warning

        Returns:
            子图最终状态 dict（来自最后一个 values 事件）
        """
        result = None
        event_count = 0

        async for event in subgraph.astream(
            input=state.model_dump(),
            config=config,
            stream_mode=["values", "custom"],
        ):
            if isinstance(event, tuple) and len(event) == 2:
                mode, data = event
                if mode == "custom" and isinstance(data, dict):
                    evt_type = data.get("type")
                    if evt_type == "sub_node_start":
                        node_name = node_name_formatter(data)
                        await SubgraphRunner.create_node_execution(
                            execution_id, data, node_name
                        )
                        if writer:
                            writer(
                                FlowEventFactory.node_start(
                                    node_key=data["node_key"],
                                    node_type=data["node_type"],
                                    node_name=node_name,
                                    input_data=data.get("input_data"),
                                )
                            )
                    elif evt_type == "sub_node_done":
                        await SubgraphRunner.update_node_execution(execution_id, data)
                        if writer:
                            writer(
                                FlowEventFactory.node_done(
                                    node_key=data["node_key"],
                                    node_type=data["node_type"],
                                    output_data=data.get("output_data"),
                                    error=data.get("error"),
                                )
                            )
                    elif writer:
                        writer(data)
                elif mode == "custom" and writer:
                    writer(data)
                elif mode == "values":
                    result = data
                    event_count += 1

        if warn_no_values and event_count == 0:
            logger.warning("子图 astream 未产生任何 values 事件")

        return result

    @staticmethod
    async def create_node_execution(
        execution_id: int, event: dict, node_name: str
    ) -> None:
        """为子图节点创建或复用 NodeExecution 记录（RUNNING 状态）。"""
        try:
            node_key = event["node_key"]
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
                if ne:
                    ne.status = NodeExecutionStatus.RUNNING.value
                    ne.start_time = datetime.now()
                    if event.get("input_data") is not None:
                        ne.input_data = event["input_data"]
                    await db.commit()
                    return

                ne = NodeExecution(
                    flow_execution_id=execution_id,
                    node_key=node_key,
                    node_type=event["node_type"],
                    node_name=node_name,
                    status=NodeExecutionStatus.RUNNING.value,
                    input_data=event.get("input_data"),
                    start_time=datetime.now(),
                )
                db.add(ne)
                await db.commit()
        except Exception as e:
            logger.warning(f"创建子图节点执行记录失败: {e}")

    @staticmethod
    async def update_node_execution(execution_id: int, event: dict) -> None:
        """更新子图节点的 NodeExecution 记录（SUCCESS 或 FAILED 状态）。"""
        try:
            node_key = event["node_key"]
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
                if ne:
                    error_msg = event.get("error")
                    if error_msg:
                        ne.status = NodeExecutionStatus.FAILED.value
                        ne.error_message = error_msg
                    else:
                        ne.status = NodeExecutionStatus.SUCCESS.value
                    ne.output_data = event.get("output_data")
                    ne.end_time = datetime.now()
                    await db.commit()
        except Exception as e:
            logger.warning(f"更新子图节点执行记录失败: {e}")

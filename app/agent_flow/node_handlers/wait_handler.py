"""
延时节点处理器

等待指定秒数后透传状态。典型用途：
- 宽限期等待（超时判定前的缓冲）
- 重试退避
- 批量操作的节奏控制（配合循环节点的迭代间隔）

等待基于 asyncio.sleep，流程被取消时 LangGraph 任务取消会立即中断等待。
"""

import asyncio
import logging
from typing import Optional

from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter
from pydantic import BaseModel, Field

from app.agent_flow.flow_context import FlowState
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.node_handlers.base_handler import BaseNodeHandler
from app.models.flow_node import FlowNode

logger = logging.getLogger(__name__)

# 单次最大等待秒数（防呆：避免误配置导致流程长时间挂起）
MAX_WAIT_SECONDS = 3600


class WaitNodeConfig(BaseModel):
    """延时节点配置"""

    model_config = {"extra": "ignore"}
    wait_seconds: int = Field(
        5,
        description="等待秒数",
        ge=0,
        le=MAX_WAIT_SECONDS,
    )


@NodeHandlerRegistry.register("wait")
class WaitNodeHandler(BaseNodeHandler):
    """延时节点：等待 N 秒后透传状态，无输入输出变量要求"""

    ConfigClass = WaitNodeConfig

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        node_key = node.node_key

        try:
            cfg = WaitNodeConfig.model_validate(node.base_config or {})
        except Exception as exc:
            state.add_error(node_key, f"延时节点配置无效: {exc}")
            return state

        seconds = min(max(cfg.wait_seconds, 0), MAX_WAIT_SECONDS)
        if seconds <= 0:
            return state

        logger.info("延时节点[%s]开始等待 %d 秒", node_key, seconds)
        await asyncio.sleep(seconds)
        logger.info("延时节点[%s]等待完成", node_key)
        return state

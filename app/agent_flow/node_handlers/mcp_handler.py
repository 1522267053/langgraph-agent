"""
MCP节点处理器

MCP节点不参与执行流程，仅作为工具提供者的标记
在LLM节点执行时，会查找连接的MCP节点并加载工具
"""

import logging
from typing import Optional, TYPE_CHECKING
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from langgraph.types import StreamWriter
from pydantic import Field

from app.config.database import AsyncSessionLocal
from app.models.flow_node import FlowNode
from app.agent_flow.flow_context import FlowState
from app.agent_flow.node_handlers.base_handler import BaseNodeHandler, BaseNodeConfig
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.mcp_manager import mcp_tool_manager

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.agent_flow.tool_resolver import LlmToolConfig


class McpNodeConfig(BaseNodeConfig):
    """MCP 节点配置"""

    mcp_server_ids: list[int] = Field(default=[], description="MCP 服务器 ID 列表")


@NodeHandlerRegistry.register("mcp")
class McpNodeHandler(BaseNodeHandler):
    """
    MCP节点处理器

    MCP节点不执行实际逻辑，仅作为工具提供者的标记
    工具加载在LLM节点中通过 tool_resolver 解析处理
    """

    ConfigClass = McpNodeConfig

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        """
        执行MCP节点（空操作）

        MCP节点不参与执行流程，直接返回状态
        工具由LLM节点在执行时通过解析连接关系自动加载
        """
        return state

    async def get_tool(self, node: FlowNode) -> list[BaseTool]:
        """
        返回MCP服务器提供的所有工具

        Args:
            node: 节点对象

        Returns:
            MCP工具列表
        """
        config = node.base_config or {}
        mcp_server_ids = config.get("mcp_server_ids", [])
        if not mcp_server_ids:
            return []

        try:
            async with AsyncSessionLocal() as db:
                return await mcp_tool_manager.get_tools(
                    db, [int(i) for i in mcp_server_ids]
                )
        except Exception as e:
            logger.exception(f"获取mcp工具失败:{str(e)}")
            return []

    @classmethod
    def get_tool_config(cls, node: FlowNode, config: "LlmToolConfig") -> bool:
        """将MCP服务器ID添加到工具配置"""
        node_config = node.base_config or {}
        mcp_ids = node_config.get("mcp_server_ids", [])
        if mcp_ids:
            config.mcp_server_ids.extend([int(i) for i in mcp_ids])
            return True
        return False

"""
MCP节点处理器

MCP节点不参与执行流程，仅作为工具提供者的标记
在LLM节点执行时，会查找连接的MCP节点并加载工具
"""

import logging
from typing import Optional, TYPE_CHECKING
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool

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
    # 注：approval_required_tools / approval_required_patterns 字段已在 BaseNodeConfig 提供


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
        返回MCP服务器提供的所有工具，每个工具额外 wrap 一层工具审批钩子

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
                base_tools = await mcp_tool_manager.get_tools(
                    db, [int(i) for i in mcp_server_ids]
                )
        except Exception as e:
            logger.exception(f"获取mcp工具失败:{str(e)}")
            return []

        # 工具审批：wrap 一层审批钩子（基类 _check_and_request_approval 统一处理白名单+正则）
        cfg = self._get_config(node)
        handler = self
        wrapped: list[BaseTool] = []
        for base_tool in base_tools:
            if not isinstance(base_tool, StructuredTool) or not base_tool.coroutine:
                wrapped.append(base_tool)
                continue
            tool_name = base_tool.name
            original_coro = base_tool.coroutine

            async def approval_wrapped_coro(*args, **kwargs):
                # 把 args/kwargs 平铺进 kwargs 后做正则匹配（args_schema 通常是 kwargs 形式）
                flat: dict = dict(kwargs)
                for i, v in enumerate(args):
                    flat.setdefault(f"_arg_{i}", v)
                content = f"{tool_name} " + " ".join(
                    str(v)[:200] for v in flat.values()
                )
                approval_result = await handler._check_and_request_approval(
                    tool_name=tool_name,
                    tool_args=flat,
                    cfg=cfg,
                    content_for_pattern=content,
                    node_key=node.node_key,
                )
                if approval_result not in (None, "approved"):
                    return {
                        "error": (
                            f"用户未批准执行MCP工具 {tool_name}（{approval_result}）。"
                            "如需继续，请征得用户同意后重新调用。"
                        ),
                        "success": False,
                        "error_type": "approval_rejected",
                    }
                return await original_coro(*args, **kwargs)

            wrapped.append(
                StructuredTool(
                    name=tool_name,
                    description=base_tool.description,
                    args_schema=base_tool.args_schema,
                    coroutine=approval_wrapped_coro,
                    response_format=base_tool.response_format,
                    metadata=base_tool.metadata,
                )
            )
        return wrapped

    @classmethod
    def get_tool_config(cls, node: FlowNode, config: "LlmToolConfig") -> bool:
        """将MCP服务器ID添加到工具配置"""
        node_config = node.base_config or {}
        mcp_ids = node_config.get("mcp_server_ids", [])
        if mcp_ids:
            config.mcp_server_ids.extend([int(i) for i in mcp_ids])
            return True
        return False

    @classmethod
    def get_tool_info(cls, node: FlowNode) -> list[dict]:
        """返回MCP服务器提供的工具名称（从缓存读取，不实际连接）"""
        cfg = node.base_config or {}
        mcp_server_ids = cfg.get("mcp_server_ids", [])
        if not mcp_server_ids:
            return []

        try:
            from app.agent_flow.mcp_manager import mcp_tool_manager

            manager = mcp_tool_manager
            tools = []
            for sid in mcp_server_ids:
                sid_int = int(sid)
                cached = manager._tools_cache.get(sid_int)
                if cached:
                    for t in cached:
                        tools.append(
                            {
                                "name": t.name,
                                "description": t.description or "",
                            }
                        )
            return tools
        except Exception:
            return []

"""
日程管理节点处理器

为 Agent 提供 LLM 工具，使 LLM 能创建、查询、更新和删除日程。
日程数据存储在 agenda 表中，通过 creator_name 区分用户。

提供的工具（实现见 tools/agenda_toolkit.py）：
1. agenda_create - 创建日程
2. agenda_list - 查询日程列表
3. agenda_update - 更新日程
4. agenda_delete - 删除日程

本 handler 只负责注册，工具实现与描述单源见 tools/agenda_toolkit.py
（闭包无 handler 状态依赖，构造零注入）。
"""

from typing import Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.types import StreamWriter

from app.models.flow_node import FlowNode
from app.agent_flow.flow_context import FlowState
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.node_handlers.base_handler import BaseNodeHandler
from app.agent_flow.tools.agenda_toolkit import AgendaToolKit


@NodeHandlerRegistry.register("agenda")
class AgendaNodeHandler(BaseNodeHandler):
    """
    日程管理节点处理器

    作为 LLM 工具提供者使用（通过 tools Handle 连接到 LLM 节点）。
    提供日程的创建、查询、更新和删除能力，帮助 LLM 管理用户的日程安排。
    """

    @classmethod
    def get_config_schema(cls) -> list[dict]:
        return []

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState | dict:
        return state

    @classmethod
    def allow_multiple_tool_connections(cls) -> bool:
        """日程管理工具只需一个，同一 LLM 不允许多实例"""
        return False

    async def get_system_prompt_hint(self, node: FlowNode) -> Optional[str]:
        """返回日程管理工具使用提示，追加到 LLM system_prompt"""
        return (
            "\n\n## 日程管理\n"
            "拥有日程管理能力（agenda_create/agenda_list/agenda_update/agenda_delete）。使用规则：\n"
            "- 用户提到日程、提醒、安排时间时，主动使用日程工具\n"
            "- 时间格式统一使用 YYYY-MM-DD HH:MM:SS\n"
            "- 修改日程流程：① agenda_list 查询找到目标日程（含 recurrence/color）→ ② 获取 id ③ agenda_update(id, ...) 更新\n"
            "- 删除日程流程：① agenda_list 查询找到目标日程 → ② 获取 id → ③ agenda_delete(id)\n"
            "- 日程时间已过时也可查询（用于回顾）\n"
            "- 更新字段语义：不传=保持原值；传 null 或空串=清空（title 不支持清空）；传值=设为该值"
        )

    async def get_tool(self, node: FlowNode) -> list[BaseTool]:
        """组装 AgendaToolKit（闭包无状态依赖，零注入）"""
        return list(await AgendaToolKit().build_tools())

    @classmethod
    def get_tool_info(cls, node: FlowNode) -> list[dict]:
        """委托 toolkit 单源派生（描述与 get_tool 产出逐字节一致）"""
        return AgendaToolKit.tool_info(node)

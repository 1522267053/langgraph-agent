"""
任务计划节点处理器

为 Agent/Flow 提供 LLM 工具，使 LLM 能自主创建和更新任务列表。
任务列表按会话（Agent）或执行（Flow）维度持久化存储到数据库。

提供的工具（实现见 tools/todo_toolkit.py）：
1. todowrite - 写入/更新任务计划列表（全量替换）
2. todoread - 读取当前任务计划列表

本 handler 只负责注册与依赖注入（resolve_context / writer 回调），
工具实现与描述单源见 tools/todo_toolkit.py。
"""

from typing import Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.types import StreamWriter

from app.models.flow_node import FlowNode
from app.agent_flow.flow_context import FlowState
from app.agent_flow.node_handlers.base_handler import BaseNodeHandler
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.tools.todo_toolkit import TodoToolKit


@NodeHandlerRegistry.register("todo")
class TodoNodeHandler(BaseNodeHandler):
    """
    任务计划节点处理器

    作为 LLM 工具提供者使用（通过 tools Handle 连接到 LLM 节点）。
    提供任务列表的创建、更新和读取能力，帮助 LLM 跟踪复杂任务的执行进度。
    """

    def __init__(self):
        super().__init__()
        self._writer: Optional[StreamWriter] = None
        self._ref_type: Optional[str] = None
        self._ref_id: Optional[int] = None

    def _resolve_context(
        self, config: Optional[RunnableConfig] = None
    ) -> tuple[Optional[str], Optional[int]]:
        """从 config 中解析 ref_type 和 ref_id"""
        if self._ref_type is not None:
            return self._ref_type, self._ref_id
        if config and "configurable" in config:
            configurable = config["configurable"]
            scope_type = configurable.get("scope_type", "agent")
            thread_id = configurable.get("thread_id")
            if thread_id:
                try:
                    self._ref_type = scope_type
                    _, id_str = thread_id.split("_", 1)
                    self._ref_id = int(id_str)
                    return self._ref_type, self._ref_id
                except (ValueError, TypeError):
                    pass
        return None, None

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
        """任务计划系统只需一个，同一 LLM 不允许多实例"""
        return False

    async def get_system_prompt_hint(self, node: FlowNode) -> Optional[str]:
        """返回任务计划使用提示，追加到 LLM system_prompt"""
        return (
            "\n\n## 任务计划系统(专给大模型使用，不能给用户使用，不需要记录用户的待办)\n"
            "拥有任务规划与进度跟踪能力。使用规则：\n"
            "- 复杂任务（3步以上）时，使用 todowrite 创建任务列表来组织和跟踪进度\n"
            "- 收到新指令后立即捕获，更新任务列表\n"
            "- 完成任务后立即标记为 completed（不要批量更新）\n"
            "- 开始新任务时标记为 in_progress（同一时间仅一个 in_progress）\n"
            "- 取消无关任务时标记为 cancelled\n"
            "- 需要修改单条任务的内容/状态/优先级时，用 todowrite 重写整个列表并同步更新对应条目\n"
            "- 将复杂任务拆分为小步骤\n"
            "- 需要查看当前任务列表时使用 todoread\n"
            "- 简单任务（1-2步）或纯对话不需要使用任务列表"
        )

    async def get_tool(self, node: FlowNode) -> list[BaseTool]:
        """组装 TodoToolKit（注入上下文解析与 writer 回调）"""

        async def _resolve_context() -> tuple[Optional[str], Optional[int]]:
            return self._resolve_context()

        def _get_writer():
            return self._writer

        kit = TodoToolKit(resolve_context=_resolve_context, get_writer=_get_writer)
        return list(await kit.build_tools())

    @classmethod
    def get_tool_info(cls, node: FlowNode) -> list[dict]:
        """委托 toolkit 单源派生（描述与 get_tool 产出逐字节一致）"""
        return TodoToolKit.tool_info(node)

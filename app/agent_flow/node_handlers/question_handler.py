"""
问题反问节点处理器

Question 节点作为工具节点连接到 LLM 节点，提供 ask_user_question 工具。
LLM 在需要澄清歧义时调用，向用户抛出结构化选项（1-4 个 + Other 自填）。

工具实现与描述单源见 tools/question_toolkit.py；本 handler 只负责
注册与依赖注入（session_id/writer 读取回调、node_key 路由）。
"""

import logging
from typing import Optional, TYPE_CHECKING

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.types import StreamWriter

from app.agent_flow.flow_context import FlowState
from app.agent_flow.flow_event import (
    NodeDoneEvent,
    NodeStartEvent,
)
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.node_handlers.base_handler import (
    BaseNodeConfig,
    BaseNodeHandler,
)
from app.agent_flow.tools.question_toolkit import QuestionToolKit
from app.models.flow_node import FlowNode
from pydantic import Field

if TYPE_CHECKING:
    from app.agent_flow.tool_resolver import LlmToolConfig

logger = logging.getLogger(__name__)


class QuestionNodeConfig(BaseNodeConfig):
    """Question 节点配置"""

    description: str = Field(
        default="", description="节点描述（注入到 LLM system_prompt 帮助理解何时调用）"
    )


@NodeHandlerRegistry.register("question")
class QuestionNodeHandler(BaseNodeHandler):
    """
    问题反问节点处理器

    与 mcp/skill/memory 等工具节点形态一致：
    - execute() 空操作（不参与执行图）
    - get_tool() 组装 QuestionToolKit
    - allow_multiple_tool_connections=False（同 skill，避免工具名冲突）
    """

    ConfigClass = QuestionNodeConfig

    # 注：_writer / _session_id / _resolve_context 已下沉到 BaseNodeHandler 基类，
    # 本类继承即可直接调用（sub_agent_handler 也是同一模式）。

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        """工具节点：空操作，工具由 LLM 节点在 tool_resolver 中加载"""
        if writer:
            self._writer = writer
        if writer:
            writer(
                NodeStartEvent(
                    node_key=node.node_key,
                    node_type=node.node_type,
                    node_name=node.node_name,
                    input_data={},
                )
            )
        if writer:
            writer(
                NodeDoneEvent(
                    node_key=node.node_key, node_type=node.node_type, output_data={}
                )
            )
        return state

    @classmethod
    def allow_multiple_tool_connections(cls) -> bool:
        """Question 节点工具名固定为 ask_user_question，多实例会导致工具名冲突"""
        return False

    async def get_tool(self, node: FlowNode) -> Optional[BaseTool]:
        """组装 QuestionToolKit（注入 session_id/writer 回调与 node_key）"""

        def _get_session_id() -> int:
            return self._session_id

        def _get_writer() -> StreamWriter:
            if self._writer is None:
                raise RuntimeError("_writer为空")
            return self._writer

        kit = QuestionToolKit(
            node_key=node.node_key,
            get_session_id=_get_session_id,
            get_writer=_get_writer,
        )
        tools = await kit.build_tools()
        return tools[0] if tools else None

    @classmethod
    def get_tool_config(cls, node: FlowNode, config: "LlmToolConfig") -> bool:
        """将 Question 节点 key 加入工具配置（LLM 节点收集时识别）"""
        node_config = node.base_config or {}
        description = node_config.get("description", "")
        config.question_node_keys.append(node.node_key)
        config.question_configs[node.node_key] = {
            "name": node.node_name or "问题反问",
            "description": description
            or "调用 ask_user_question 工具向用户抛出结构化选项",
        }
        return True

    @classmethod
    def get_tool_info(cls, node: FlowNode) -> list[dict]:
        """委托 toolkit 单源派生（描述与 get_tool 产出逐字节一致）"""
        return QuestionToolKit.tool_info(node)

    async def get_system_prompt_hint(self, node: FlowNode) -> Optional[str]:
        """注入到 LLM system_prompt：说明何时该用 ask_user_question"""
        config = self._get_config(node)
        description = getattr(config, "description", "") or ""
        if not description:
            return None
        return (
            f"\n# 问题反问工具（ask_user_question）\n\n"
            f"当遇到以下情况之一，可调用 ask_user_question 工具向用户抛出结构化选项：\n"
            f"- 需求存在歧义，需要用户在多个方案间做选择\n"
            f"- 涉及用户偏好 / 取舍（数据库选型、命名风格、UI 风格等）\n"
            f"- 需要确认是否执行某项有副作用的操作\n\n"
            f"节点描述：{description}\n"
            f"调用规范：提供 1-4 个选项（label 必填，description 可选），"
            f"选项不互斥、需要用户同时选多项时设置 multiple: true，"
            f"用户未响应约 5 分钟后超时"
        )

"""
问题反问节点处理器

Question 节点作为工具节点连接到 LLM 节点，提供 ask_user_question 工具。
LLM 在需要澄清歧义时调用，向用户抛出结构化选项（2-4 个 + Other 自填）。
"""

import asyncio
import json
import logging
import uuid
from typing import Optional, TYPE_CHECKING

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import StreamWriter
from pydantic import BaseModel, Field

from app.agent_flow.flow_context import FlowState
from app.agent_flow.flow_event import (
    NodeDoneEvent,
    NodeStartEvent,
    QuestionRequestEvent,
)
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.node_handlers.base_handler import (
    BaseNodeConfig,
    BaseNodeHandler,
)
from app.constants.timing import USER_RESPONSE_TIMEOUT_SECONDS
from app.models.flow_node import FlowNode
from app.services.question_service import question_service

if TYPE_CHECKING:
    from app.agent_flow.tool_resolver import LlmToolConfig

logger = logging.getLogger(__name__)


class AskUserQuestionInput(BaseModel):
    """ask_user_question 工具输入参数"""

    question: str = Field(..., description="向用户提出的问题正文，清晰描述需要澄清的点")
    options: list[dict] = Field(
        ...,
        description=(
            "选项列表（1-4 项）。每项为对象："
            "{label: 简短标签, description?: 详细说明, preview?: 长内容预览}"
        ),
    )
    multiple: bool = Field(
        default=False,
        description=(
            "true=多选：用户可勾选多项后点确定提交；false/省略=单选：点击选项即提交。"
            "选项不互斥且可能需要同时选多项时设为 true"
        ),
    )
    header: Optional[str] = Field(
        default=None, description="短标题（弹窗表头），2-12 字"
    )


class QuestionNodeConfig(BaseNodeConfig):
    """Question 节点配置"""

    description: str = Field(
        default="", description="节点描述（注入到 LLM system_prompt 帮助理解何时调用）"
    )


@NodeHandlerRegistry.register("question")
class QuestionNodeHandler(BaseNodeHandler):
    """
    问题反问节点处理器

    作为工具节点连接到 LLM 节点，提供 ask_user_question 工具：
    - LLM 调用工具 → emit QuestionRequestEvent（SSE）
    - 前端弹窗显示选项 → 用户点击 → POST /question/resolve
    - question_service.resolve 唤醒 Future → 工具返回 answers 给 LLM

    与 mcp/skill/memory 等工具节点形态一致：
    - execute() 空操作（不参与执行图）
    - get_tool() 返回 StructuredTool
    - allow_multiple_tool_connections=False（同 skill，避免工具名冲突）
    """

    ConfigClass = QuestionNodeConfig

    def __init__(self):
        super().__init__()
        self._writer: Optional[StreamWriter] = None
        self._session_id: int = 0

    def _resolve_context(self, config: Optional[RunnableConfig]) -> None:
        """记录当前 Agent 会话 ID（与 sub_agent_handler 同样的注入模式）

        不得触碰 _writer：setup_tool_handlers 在调用本方法前已注入 writer，
        此处重置会把它清成 None，导致工具守卫误判为非 Agent 模式。
        """
        self._session_id = 0
        configurable = (config or {}).get("configurable", {})
        session_id = configurable.get("session_id")
        if not session_id:
            thread_id = str(configurable.get("thread_id") or "")
            if thread_id.startswith("agent_"):
                session_id = thread_id.removeprefix("agent_")
        try:
            self._session_id = int(session_id or 0)
        except (TypeError, ValueError):
            self._session_id = 0

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
        """返回 ask_user_question StructuredTool（每次调用触发 question_service）"""
        # 闭包捕获当前 handler 引用与节点 key
        handler = self
        node_key = node.node_key

        async def ask_user_question(
            question: str,
            options: list[dict],
            multiple: bool = False,
            header: Optional[str] = None,
        ) -> str:
            """见 AskUserQuestionInput schema"""
            # 1. 校验选项数（1-4）
            if not options:
                return json.dumps(
                    {"error": "必须提供至少 1 个选项"}, ensure_ascii=False
                )
            if len(options) > 4:
                return json.dumps({"error": "选项数最多 4 个"}, ensure_ascii=False)
            for opt in options:
                if not isinstance(opt, dict) or not opt.get("label"):
                    return json.dumps(
                        {"error": "每个选项必须包含非空 label 字段"},
                        ensure_ascii=False,
                    )

            # 2. 从 handler 闭包读取 session_id + writer
            session_id = handler._session_id
            writer = handler._writer

            if session_id <= 0 or writer is None:
                return json.dumps(
                    {"error": "问题反问工具仅在 Agent 模式下可用"},
                    ensure_ascii=False,
                )

            question_id = uuid.uuid4().hex[:16]

            # 3. 注册 Future + emit SSE 事件
            future = question_service.register(session_id, question_id)
            writer(
                QuestionRequestEvent(
                    node_key=node_key,
                    question_id=question_id,
                    question=question,
                    header=header,
                    options=options,
                    multiple=multiple,
                    expires_in=USER_RESPONSE_TIMEOUT_SECONDS,
                )
            )

            # 4. 等待前端响应（USER_RESPONSE_TIMEOUT_SECONDS 超时，与 tool_approval 一致）
            try:
                await asyncio.wait_for(
                    future.event.wait(), timeout=USER_RESPONSE_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                question_service.remove(session_id, question_id)
                return json.dumps(
                    {
                        "error": (
                            f"用户未在 {USER_RESPONSE_TIMEOUT_SECONDS // 60} 分钟内响应，"
                            "问题已过期"
                        )
                    },
                    ensure_ascii=False,
                )

            question_service.remove(session_id, question_id)
            answers = future.answers
            if answers is None:
                return json.dumps({"error": "用户取消了问题"}, ensure_ascii=False)
            return json.dumps({"answers": answers}, ensure_ascii=False)

        return StructuredTool(
            name="ask_user_question",
            description=(
                "向用户提出结构化选项问题并等待选择。"
                "仅在确实需要用户澄清偏好 / 取舍时才调用，不要滥用。"
                "提供 1-4 个选项，最后一个 Other 选项由前端自动追加。"
                "选项互斥的方案取舍（如选数据库、选 UI 风格）使用默认单选；"
                "选项可并存、用户可能需要同时选多项（如勾选多个要处理的文件、"
                "多项改进措施）时设置 multiple: true。"
                "返回 JSON {answers: [label, ...]}，用户选择 Other 时 answers[0] 是用户输入的文本。"
            ),
            func=None,
            coroutine=ask_user_question,
            args_schema=AskUserQuestionInput,
        )

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
        """返回节点暴露的工具元数据（前端画布展示用）"""
        return [
            {
                "name": "ask_user_question",
                "description": ("向用户提出结构化选项问题并等待选择"),
            }
        ]

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
            f"前端会自动追加 Other 选项允许用户自由输入。\n"
            f"不要在可以自主判断的场景滥用此工具（避免打断用户）。\n"
        )

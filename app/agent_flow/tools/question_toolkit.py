"""
问题反问工具集（QuestionToolKit）

从 question_handler.get_tool 抽出的 1 个工具：ask_user_question。
LLM 在需要澄清歧义时调用，向用户抛出结构化选项（1-4 个 + Other 自填）：
- 工具调用 → emit QuestionRequestEvent（SSE）
- 前端弹窗显示选项 → 用户点击 → POST /question/resolve
- question_service.resolve 唤醒 Future → 工具返回 answers 给 LLM

依赖注入：get_session_id / get_writer / node_key 由 handler 注入
（Agent 模式上下文，非 Agent 模式下工具返回错误引导）。

依赖方向：tools/* → app/services/*、app/flow_event、app/constants/*；
本模块不 import node_handlers。
"""

import asyncio
import json
import uuid
from typing import TYPE_CHECKING, Callable, Optional, Sequence

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent_flow.flow_event import QuestionRequestEvent
from app.agent_flow.tools.base_abs_tool import BaseToolKit
from app.constants.timing import USER_RESPONSE_TIMEOUT_SECONDS
from app.services.question_service import question_service
from langgraph.types import StreamWriter

if TYPE_CHECKING:
    from app.models.flow_node import FlowNode

# ---- 描述单源（唯一维护点；tool_info 与 build_tools 共用）----
QUESTION_TOOL_DESCRIPTIONS: dict[str, str] = {
    "ask_user_question": (
        "向用户提出结构化选项问题并等待选择。"
        "仅在确实需要用户澄清偏好 / 取舍时才调用，不要滥用。"
        "提供 1-4 个选项，最后一个 Other 选项由前端自动追加。"
        "选项互斥的方案取舍（如选数据库、选 UI 风格）使用默认单选；"
        "选项可并存、用户可能需要同时选多项（如勾选多个要处理的文件、"
        "多项改进措施）时设置 multiple: true。"
        "返回 JSON {answers: [label, ...]}，用户选择 Other 时 answers[0] 是用户输入的文本。"
    ),
}


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


class QuestionToolKit(BaseToolKit):
    """问题反问工具集（1 个工具）

    由 question_handler.get_tool 构造，注入 session_id/writer 读取回调
    与 node_key（SSE 事件路由用）。
    """

    def __init__(
        self,
        node_key: str,
        get_session_id: Callable[[], int],
        get_writer: Callable[[], StreamWriter],
    ):
        self._node_key = node_key
        self._get_session_id = get_session_id
        self._get_writer = get_writer

    async def build_tools(self) -> Sequence[StructuredTool]:
        node_key = self._node_key
        get_session_id = self._get_session_id
        get_writer = self._get_writer

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

            # 2. 读取 session_id + writer（handler 注入回调）
            session_id = get_session_id()
            writer = get_writer()

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

        return [
            StructuredTool(
                name="ask_user_question",
                description=QUESTION_TOOL_DESCRIPTIONS["ask_user_question"],
                func=None,
                coroutine=ask_user_question,
                args_schema=AskUserQuestionInput,
            )
        ]

    @classmethod
    def tool_info(cls, node: "FlowNode") -> list[dict]:
        """从描述常量派生（单源：与 build_tools 产出逐字节一致）"""
        return [
            {"name": n, "description": d} for n, d in QUESTION_TOOL_DESCRIPTIONS.items()
        ]

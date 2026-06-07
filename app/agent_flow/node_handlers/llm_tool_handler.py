"""
增强版LLM节点处理器

支持：
- MCP工具调用（通过连接MCP节点）
- 人工协助工具（通过连接Human节点）
- API工具（通过连接API节点）
- 知识库工具（通过连接Knowledge节点）
- 多轮工具调用（ReAct循环）
- 多轮人工交互（使用 LangGraph interrupt 机制）
- 对话历史管理（通过 state.conversation_messages 自动恢复）
- 流式输出（通过 StreamWriter）
- 中断检测（通过 interrupt_service）

工具获取方式：
通过连接的工具节点（source_handle="tools"）获取，各节点处理器实现 get_tool() 方法

对话历史管理：
- 对话历史存储在 state.conversation_messages 中
- 通过 LangGraph checkpoint 自动恢复，无需从数据库加载
- 仅在首次执行时从数据库加载历史（作为初始化）
"""

import asyncio
import json
import logging
import openai
from typing import TYPE_CHECKING, Any, Optional, Union


from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.types import StreamWriter, interrupt
from pydantic import Field

from app.models.agent_message import AgentMessage
from app.models.flow_node import FlowNode
from app.services.agent_conversation_service import AgentConversationService
from app.services.conversation_service import ConversationService
from app.services.interrupt_service import interrupt_service
from app.agent_flow.exceptions import NodeExecutionError
from app.agent_flow.flow_context import FlowState
from app.agent_flow.flow_event import (
    ErrorEvent,
    LlmRetryEvent,
    NodeContentEvent,
    NodeStartEvent,
    NodeThinkingEvent,
    TokenUsageEvent,
    ToolCallEndEvent,
    ToolCallLimitEvent,
    ToolCallStartEvent,
    ToolApprovalEvent,
)
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.node_handlers.base_handler import (
    BaseNodeHandler,
    BaseNodeConfig,
    NodeVariable,
)
from app.agent_flow.tool_resolver import get_connected_tool_nodes
from app.agent_flow.ai_provider import create_provider
from app.agent_flow.message_buffer import MessageBuffer

from app.utils.media_resolver import build_multimodal_content, collect_media_blocks
from app.utils.message_utils import extract_token_usage

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)

COMPRESS_THRESHOLD_RATIO = 0.83

_RETRYABLE_ERRORS = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
)
_RETRY_DELAYS = [1, 2, 4]
# 人工协助工具名（LLM 通过此工具名触发 interrupt）
_REQUEST_HUMAN_HELP = "request_human_help"

# 需要用户确认才能执行的工具名（仅 Agent 模式生效）
_APPROVAL_REQUIRED_TOOLS: frozenset[str] = frozenset(
    {"shell_executor", "python_executor", "file_write", "text_editor"}
)

# system_prompt hint 优先级：值越小越靠前，静态内容放前面有利于 LLM 缓存命中
_HINT_PRIORITY: dict[str, int] = {
    "todo": 0,
    "human": 0,
    "knowledge": 1,
    "shell": 1,
    "memory": 2,
}


class LlmNodeConfig(BaseNodeConfig):
    input_variables: list[NodeVariable] = Field(
        default=[],
        description="输入变量映射列表",
    )
    output_variables: list[NodeVariable] = Field(
        default=[NodeVariable(name="result"), NodeVariable(name="thinking")],
        description="输出变量列表",
    )
    provider: str = Field(
        "",
        description="供应商标识（留空则使用系统全局默认）",
        json_schema_extra={
            "options": [
                "deepseek",
                "openai_compatible",
                "qwen",
                "zhipu",
                "minimax",
            ]
        },
    )
    model: str = Field("", description="模型名称（留空则使用系统全局默认）")
    api_key: Optional[str] = Field(None, description="API Key")
    base_url: str = Field("", description="API 地址（为空则使用供应商默认地址）")
    temperature: float = Field(0.7, description="温度参数（0-2），越高越随机")
    max_tokens: int = Field(8192, description="最大生成 token 数")
    max_tool_iterations: int = Field(20, description="最大工具调用轮次")
    history_mode: str = Field(
        "node",
        description="对话历史模式",
        json_schema_extra={"options": ["node", "flow", "none"]},
    )
    max_history_turns: int = Field(10, description="最大对话历史轮次")
    capabilities: dict = Field(
        default_factory=lambda: {
            "image": False,
            "video": False,
            "audio": False,
            "pdf": False,
            "xlsx": False,
        },
        description="模型能力开关",
    )
    system_prompt: Optional[str] = Field(
        None, description="系统提示词，定义 LLM 的角色和行为"
    )
    user_prompt: str = Field(
        ...,
        description="用户提示词模板（必填，否则llm收不到消息。支持变量插值，如:{{message}}）",
    )
    require_tool_approval: bool = Field(
        False, description="危险工具执行前需要用户确认（仅Agent模式生效）"
    )
    extra_body: dict = Field(
        {}, description="附加请求参数（JSON对象，会合并到请求体中）"
    )
    reasoning_effort: Optional[str] = Field(
        None,
        description="推理深度（low/medium/high），部分模型支持",
        json_schema_extra={"options": ["low", "medium", "high"]},
    )
    context_length: int = Field(
        0, description="模型上下文窗口大小（token数，0表示不限制）"
    )


class LlmToolNodeHandler(BaseNodeHandler):
    """
    增强版LLM节点处理器

    支持MCP工具调用和多轮人工协助
    工具通过连接到LLM节点的MCP和Human节点提供
    使用 LangGraph interrupt 机制实现人工交互
    """

    ConfigClass = LlmNodeConfig

    def __init__(
        self,
        flow=None,
        db_session_factory=None,
        execution_id: int = 0,
        conversation_service: Optional[
            Union[ConversationService, AgentConversationService]
        ] = None,
        handler_registry: Optional[dict] = None,
        session_id: int = 0,
    ):
        super().__init__()
        self.flow = flow
        self.db_session_factory = db_session_factory
        self.execution_id = execution_id
        self.conversation_service = conversation_service
        self.handler_registry = handler_registry or {}
        self.session_id = session_id

    @property
    def _id_param(self) -> int:
        """获取当前上下文的 ID 参数（Agent 模式用 session_id，Flow 模式用 execution_id）"""
        return self.session_id if self.session_id else self.execution_id

    def _check_interrupted(self, state: FlowState) -> bool:
        """
        检查是否被中断

        优先级：
        1. state.is_interrupted（内部状态）
        2. interrupt_service（外部中断信号）

        Returns:
            True 表示需要中断，False 表示继续执行
        """
        if state.is_interrupted:
            return True
        if self.session_id > 0:
            if interrupt_service.is_agent_interrupted(self.session_id):
                state.set_interrupted()
                return True
        elif self.execution_id > 0:
            if interrupt_service.is_flow_interrupted(self.execution_id):
                state.set_interrupted()
                return True
        return False

    # ---- 事件发送辅助方法 ----

    def check_config(
        self,
        config: dict,
        node_key: str,
        state: FlowState,
        writer: Optional[StreamWriter] = None,
    ) -> dict | None:
        """校验 LLM 必填配置"""
        model = self._require_config(config, "model", node_key, "模型", state, writer)
        if not model:
            return None
        api_key = self._require_config(
            config, "api_key", node_key, "API Key", state, writer
        )
        if not api_key:
            return None
        return {"model": model, "api_key": api_key}

    def _emit_tool_start(
        self,
        writer: Optional[StreamWriter],
        node_key: str,
        tool_name: str,
        tool_args: dict,
    ) -> None:
        """发送工具调用开始事件"""
        self._emit(
            writer,
            ToolCallStartEvent(
                node_key=node_key, tool_name=tool_name, tool_args=tool_args
            ),
        )

    def _emit_tool_end(
        self,
        writer: Optional[StreamWriter],
        node_key: str,
        tool_name: str,
        result: Any,
        status: str = "success",
    ) -> None:
        """发送工具调用结束事件"""
        self._emit(
            writer,
            ToolCallEndEvent(
                node_key=node_key, tool_name=tool_name, status=status, result=result
            ),
        )

    # ---- 主执行入口 ----

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState | dict:
        """
        执行增强版LLM节点，支持多轮人工交互和流式输出

        整体流程：
        1. 解析配置、初始化工具处理器、收集工具和 prompt 提示
        2. 准备 LLM 实例并绑定工具
        3. 构建初始消息列表（checkpoint 恢复 / DB 加载 / 首次构建）
        4. ReAct 循环：流式调用 LLM → 处理工具调用 → 继续调用
        5. 保存执行结果（输出变量 + 对话历史）
        """
        cfg = self._get_config(node)
        max_tool_iterations = cfg.max_tool_iterations

        # 单次遍历：收集工具 + 注入处理器依赖 + 收集 prompt 提示
        tools, prompt_hints = await self._setup_tool_handlers(
            node, state, writer, config, cfg
        )

        # 解析输入变量，提取 system_prompt 和 user_prompt
        input_data = self.__class__.get_input_content(
            node, state, self._resolver, node.base_config or {}
        )
        system_prompt = (
            (input_data.get("system_prompt") or "").strip() or None
            if input_data
            else None
        )
        user_prompt = (
            (input_data.get("user_prompt") or "").strip() or None
            if input_data
            else None
        )
        if not user_prompt:
            raise NodeExecutionError(node.node_key, "用户提示词（user_prompt）不能为空")

        # 追加工具节点的 system_prompt 提示
        for hint in prompt_hints:
            system_prompt = (system_prompt or "") + hint

        # 发送 node_start 事件（在流式输出之前）
        self._emit(
            writer,
            NodeStartEvent(
                node_key=node.node_key,
                node_type=node.node_type,
                node_name=node.node_name,
                input_data=input_data if input_data else None,
            ),
        )

        checked = self.check_config(
            node.base_config or {}, node.node_key, state, writer
        )
        if not checked:
            return state

        # 准备 LLM 实例和消息列表
        _, llm_with_tools, _ = self._prepare_llm(
            node.base_config or {}, tools, node.node_key, state
        )
        messages = await self._build_initial_messages(
            node, node.base_config or {}, user_prompt, state, config, writer
        )

        # 自动上下文压缩：超过 80% 时调用 LLM 压缩旧消息
        cfg_context_length = cfg.context_length or 0
        msg_buf = MessageBuffer(
            messages,
            session_id=self.session_id,
            execution_id=self.execution_id,
            db_session_factory=self.db_session_factory,
            conversation_service=self.conversation_service,
            node_key=node.node_key,
            emit_fn=self._emit,
        )
        prompt_tokens = await self._should_auto_compress(cfg_context_length)
        if cfg_context_length > 0 and prompt_tokens > int(
            cfg_context_length * COMPRESS_THRESHOLD_RATIO
        ):
            await msg_buf.maybe_compress(
                cfg_context_length, node.base_config or {}, writer
            )

        # ReAct 循环 + 结果保存
        last_content = ""
        thinking_content: list[str] = []
        output_names = self._get_output_var_names(node, ["result", "thinking"])
        result_name = output_names[0] if len(output_names) > 0 else "result"
        thinking_name = output_names[1] if len(output_names) > 1 else "thinking"
        try:
            last_content, thinking_content = await self._run_react_loop(
                llm_with_tools,
                system_prompt,
                msg_buf,
                tools,
                node,
                state,
                writer,
                max_tool_iterations,
                context_length=cfg_context_length,
            )
            if last_content:
                state.set_node_variable(node.node_key, result_name, last_content)
            if thinking_content:
                state.set_node_variable(
                    node.node_key, thinking_name, "".join(thinking_content)
                )
            state.set_conversation_messages(node.node_key, list(msg_buf.messages))
            await msg_buf.save_to_db()
        except asyncio.CancelledError:
            logger.info(
                f"LLM节点被取消, 跳过DB保存（保持checkpoint同步）, "
                f"node_key={node.node_key}"
            )
            state.set_interrupted()
            if last_content:
                state.set_node_variable(node.node_key, result_name, last_content)
            if thinking_content:
                state.set_node_variable(
                    node.node_key, thinking_name, "".join(thinking_content)
                )
            state.set_conversation_messages(node.node_key, list(msg_buf.messages))
            raise
        except Exception as e:
            logger.info(
                f"LLM节点异常, 跳过DB保存（保持checkpoint同步）, "
                f"node_key={node.node_key}, error={type(e).__name__}: {e}"
            )
            state.add_error(node.node_key, f"LLM调用失败: {str(e)}")
            state.set_conversation_messages(node.node_key, list(msg_buf.messages))
            raise

        return state

    # ---- 工具处理器初始化 ----

    async def _setup_tool_handlers(
        self,
        node: FlowNode,
        state: FlowState,
        writer: Optional[StreamWriter],
        config: Optional[RunnableConfig],
        cfg: LlmNodeConfig,
    ) -> tuple[list[BaseTool], list[str]]:
        """
        单次遍历工具节点，完成三件事：
        1. 注入处理器依赖（_agent_id, _writer, _resolve_context, _llm_config）
        2. 收集工具定义
        3. 收集 system_prompt 提示片段（按优先级排序，静态内容靠前以利于 LLM 缓存命中）

        Returns:
            (工具列表, prompt提示片段列表)
        """
        tools: list[BaseTool] = []
        prompt_hints: list[tuple[int, int, str]] = []

        if not self.flow or not self.db_session_factory:
            return tools, [h for _, _, h in prompt_hints]

        tool_nodes = get_connected_tool_nodes(self.flow, node.node_key)
        llm_config = {
            "model": cfg.model,
            "api_key": cfg.api_key,
            "base_url": cfg.base_url,
            "provider": cfg.provider,
            "context_length": cfg.context_length,
        }

        for idx, tool_node in enumerate(tool_nodes):
            handler = self.handler_registry.get(tool_node.node_type)
            if not handler:
                continue

            # 注入 _agent_id（记忆节点等需要知道当前 Agent 的 ID）
            if hasattr(handler, "_agent_id") and hasattr(self.flow, "id"):
                handler._agent_id = self.flow.id

            # 注入 writer、resolve_context、llm_config
            if hasattr(handler, "_writer"):
                handler._writer = writer
            if hasattr(handler, "_resolve_context"):
                handler._resolve_context(config)
            if hasattr(handler, "_llm_config"):
                handler._llm_config = llm_config

            # 收集工具定义（get_tool 返回 None 时仍需收集 prompt hints）
            try:
                result = handler.get_tool(tool_node)
                if result is not None:
                    if asyncio.iscoroutine(result):
                        result = await result
                    if isinstance(result, list):
                        tools.extend(result)
                    elif result:
                        tools.append(result)
            except Exception as e:
                state.add_error(
                    node.node_key,
                    f"获取工具失败 [{tool_node.node_name}]: {str(e)}",
                )

            # 收集 system_prompt 提示（所有 handler 均为 async）
            if hasattr(handler, "get_system_prompt_hint"):
                hint = await handler.get_system_prompt_hint(tool_node)
                if hint:
                    priority = _HINT_PRIORITY.get(tool_node.node_type, 1)
                    prompt_hints.append((priority, idx, hint))

        # 按优先级排序：静态内容靠前，动态内容（如记忆）靠后，利于 LLM 缓存命中
        prompt_hints.sort(key=lambda x: (x[0], x[1]))

        return tools, [h for _, _, h in prompt_hints]

    # ---- ReAct 循环 ----

    async def _run_react_loop(
        self,
        llm: BaseChatModel,
        system_prompt: Optional[str],
        msg_buf: MessageBuffer,
        tools: list[BaseTool],
        node: FlowNode,
        state: FlowState,
        writer: Optional[StreamWriter],
        max_tool_iterations: int,
        context_length: int = 0,
    ) -> tuple[str, list[str]]:
        """
        ReAct 循环：流式调用 LLM → 处理工具调用 → 继续调用

        Returns:
            (最后一条文本内容, 所有 thinking 片段)
        """
        thinking_content: list[str] = []
        last_content = ""
        tool_call_count = 0

        while True:
            messages = msg_buf.messages
            # system_prompt 不存入 messages/checkpoint，每次调用时临时拼接
            call_messages = (
                [SystemMessage(content=system_prompt), *messages]
                if system_prompt
                else messages
            )
            (
                response,
                current_thinking,
                current_content,
            ) = await self._stream_llm_response(
                llm, call_messages, node.node_key, state, writer
            )

            # 推送 token 用量事件
            if response:
                usage = extract_token_usage(response)
                if usage.get("total_tokens"):
                    self._emit(
                        writer,
                        TokenUsageEvent(
                            node_key=node.node_key,
                            prompt_tokens=usage["prompt_tokens"],
                            completion_tokens=usage["completion_tokens"],
                            total_tokens=usage["total_tokens"],
                        ),
                    )

                    # 循环中检查上下文是否需要压缩
                    if context_length > 0 and usage.get("prompt_tokens", 0) > int(
                        context_length * COMPRESS_THRESHOLD_RATIO
                    ):
                        # 先将当前response追加到msg_buf，
                        # 确保压缩时能看到LLM最新输出
                        if response:
                            msg_buf.append(response)
                            if current_content:
                                last_content = current_content
                            thinking_content.extend(current_thinking)
                        node_config = node.base_config or {}
                        await msg_buf.maybe_compress(
                            context_length, node_config, writer
                        )
                        # 压缩后直接结束循环，用户下次发消息时从压缩后的摘要继续
                        break

                # 检查 finish_reason，上下文溢出时记录错误并终止循环
                finish_reason = (
                    response.response_metadata.get("finish_reason", "")
                    if response.response_metadata
                    else ""
                )
                if finish_reason == "model_context_window_exceeded":
                    state.add_error(
                        node.node_key, "模型上下文窗口已超出，响应可能被截断"
                    )
                    self._emit(
                        writer,
                        ErrorEvent(
                            node_key=node.node_key,
                            message="模型上下文窗口已超出，响应被截断",
                        ),
                    )
                    # 仍需保存已有内容到历史
                    msg_buf.append(response)
                    if current_content:
                        last_content = current_content
                    thinking_content.extend(current_thinking)
                    break

            # 将完整 AI 消息追加到历史
            if response:
                msg_buf.append(response)
            if current_content:
                last_content = current_content
            thinking_content.extend(current_thinking)

            # 无工具调用则结束循环
            if not response or not response.tool_calls:
                break

            # 处理工具调用，返回 (是否继续循环, 工具调用总次数)
            should_continue, tool_call_count = await self._handle_tool_calls(
                response.tool_calls,
                tools,
                msg_buf,
                node,
                state,
                writer,
                tool_call_count,
                max_tool_iterations,
            )
            if not should_continue:
                break

        return last_content, thinking_content

    # ---- LLM 准备 ----

    def _prepare_llm(
        self, node_config: dict, tools: list[BaseTool], node_key: str, state: FlowState
    ) -> tuple[BaseChatModel, BaseChatModel, bool]:
        """
        创建 LLM 实例并绑定工具

        Returns:
            (llm, llm_with_tools, has_tools) — 原始实例、绑定工具后的实例、是否有工具可用
        """
        api_key = node_config.get("api_key")
        model = node_config.get("model", "")
        base_url = node_config.get("base_url")
        max_tokens = node_config.get("max_tokens", 8192)
        temperature = node_config.get("temperature", 0.7)
        provider_name = node_config.get("provider", "deepseek")
        extra_body = node_config.get("extra_body")
        reasoning_effort = node_config.get("reasoning_effort")
        llm = self._create_llm(
            api_key,
            model,
            base_url,
            max_tokens,
            provider_name,
            temperature,
            extra_body=extra_body,
            reasoning_effort=reasoning_effort,
        )

        has_tools = len(tools) > 0
        if has_tools:
            try:
                llm_with_tools = llm.bind_tools(tools)
            except Exception as bind_error:
                # 模型不支持 function calling 时降级为无工具模式
                state.add_error(
                    node_key,
                    f"模型不支持工具调用，请更换支持function calling的模型: {str(bind_error)}",
                )
                has_tools = False
                llm_with_tools = llm
        else:
            llm_with_tools = llm

        return llm, llm_with_tools, has_tools

    # ---- 消息构建 ----

    async def _build_initial_messages(
        self,
        node: FlowNode,
        node_config: dict,
        user_prompt: Optional[str],
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        writer: Optional[StreamWriter] = None,
    ) -> list[BaseMessage]:
        """构建初始消息列表（统一入口）"""
        messages = await self._load_base_messages(node, node_config, state)
        messages = self._validate_tool_pairs(messages)
        resume_injected = self._inject_resume_if_needed(
            messages, config, node.node_key, writer
        )
        self._append_user_message(
            messages, state, node_config, user_prompt, config, resume_injected
        )
        return messages

    async def _should_auto_compress(self, context_length: int) -> int:
        """查 DB 最后一条 AI 消息的 prompt_tokens，返回已用 token 数"""
        if not self.session_id or not self.db_session_factory or context_length <= 0:
            return 0
        try:
            async with self.db_session_factory() as db:
                from sqlalchemy import select

                query = (
                    select(AgentMessage.prompt_tokens)
                    .where(
                        AgentMessage.session_id == self.session_id,
                        AgentMessage.role == "assistant",
                        AgentMessage.is_delete == 0,
                    )
                    .order_by(AgentMessage.id.desc())
                    .limit(1)
                )
                result = await db.execute(query)
                return result.scalar() or 0
        except Exception:
            return 0

    @staticmethod
    def _validate_tool_pairs(messages: list[BaseMessage]) -> list[BaseMessage]:
        """
        校验并修复消息中 tool_call 与 tool result 的配对关系。

        确保发送给 LLM 的消息列表满足：
        1. ToolMessage 必须紧跟在对应的 AIMessage（含 tool_calls）之后
        2. 孤立的 ToolMessage（无前置 tool_call）会被移除
        3. 含 tool_calls 但缺少 ToolMessage 的 AIMessage，其未匹配的 tool_calls 会被清除
        """
        result: list[BaseMessage] = []
        pending_ai_index: int = -1
        pending_ids: set[str] = set()

        def flush_pending():
            nonlocal pending_ai_index, pending_ids
            if pending_ids and 0 <= pending_ai_index < len(result):
                ai = result[pending_ai_index]
                if ai.tool_calls:
                    ai.tool_calls = [
                        tc
                        for tc in ai.tool_calls
                        if (
                            tc.get("id", "")
                            if isinstance(tc, dict)
                            else getattr(tc, "id", "")
                        )
                        not in pending_ids
                    ]
                    if not ai.tool_calls:
                        ai.tool_calls = None
            pending_ai_index = -1
            pending_ids.clear()

        for msg in messages:
            if isinstance(msg, AIMessage) and msg.tool_calls:
                flush_pending()
                pending_ids = {
                    tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                    for tc in msg.tool_calls
                }
                result.append(msg)
                pending_ai_index = len(result) - 1
            elif isinstance(msg, AIMessage):
                flush_pending()
                result.append(msg)
            elif isinstance(msg, ToolMessage):
                if pending_ai_index >= 0 and msg.tool_call_id in pending_ids:
                    pending_ids.discard(msg.tool_call_id)
                    result.append(msg)
                    if not pending_ids:
                        pending_ai_index = -1
                else:
                    logger.debug(
                        f"移除孤立 ToolMessage: tool_call_id={msg.tool_call_id}"
                    )
            else:
                flush_pending()
                result.append(msg)

        flush_pending()
        return result

    async def _load_base_messages(
        self, node: FlowNode, node_config: dict, state: FlowState
    ) -> list[BaseMessage]:
        """加载历史消息：优先 checkpoint，其次数据库"""
        messages: list[BaseMessage] = [
            m
            for m in state.get_conversation_messages(node.node_key)
            if not isinstance(m, SystemMessage)
        ]
        if messages:
            return messages

        return [
            m
            for m in await self._load_history_from_db(node_config, node.node_key)
            if not isinstance(m, SystemMessage)
        ]

    def _inject_resume_if_needed(
        self,
        messages: list[BaseMessage],
        config: Optional[RunnableConfig],
        node_key: str = "",
        writer: Optional[StreamWriter] = None,
    ) -> bool:
        """interrupt 恢复场景：将人类回复注入为 ToolMessage

        仅当 messages 中存在未匹配的 tool_call 时才注入（说明 resume 来自
        当前 LLM 节点的 request_human_help 工具调用），否则返回 False，
        避免流程中 Human 节点 interrupt 恢复后误影响下游 LLM 节点。
        注入成功时同步发送 tool_call_end 事件，关闭前端 running 状态的工具调用。
        """
        if not messages or not config:
            return False

        resume_input = config.get("configurable", {}).get("_human_resume_input")
        if not resume_input:
            return False

        matched_ids = {
            m.tool_call_id
            for m in messages
            if isinstance(m, ToolMessage) and m.tool_call_id
        }
        for msg in messages:
            if not isinstance(msg, AIMessage) or not msg.tool_calls:
                continue
            for tc in msg.tool_calls:
                tc_id = (
                    tc.get("id", "")
                    if isinstance(tc, dict)
                    else getattr(tc, "id", "") or ""
                )
                tc_name = (
                    tc.get("name", "")
                    if isinstance(tc, dict)
                    else getattr(tc, "name", "") or ""
                )
                if tc_id and tc_id not in matched_ids:
                    messages.append(
                        ToolMessage(
                            content=str(resume_input),
                            tool_call_id=tc_id,
                            name=tc_name,
                        )
                    )
                    if node_key and writer:
                        self._emit_tool_end(
                            writer, node_key, tc_name, resume_input, status="success"
                        )
                    return True

        return False

    def _append_user_message(
        self,
        messages: list[BaseMessage],
        state: FlowState,
        node_config: dict,
        user_prompt: Optional[str],
        config: Optional[RunnableConfig] = None,
        resume_injected: bool = False,
    ) -> None:
        """构建 multimodal HumanMessage 并追加到消息列表"""
        if resume_injected:
            return

        actual_user_prompt = user_prompt or state.input_data.get("message", "")
        if not actual_user_prompt:
            return

        capabilities = node_config.get("capabilities", {})
        media_blocks, file_index = collect_media_blocks(state.input_data, capabilities)
        prompt_text = (
            f"{actual_user_prompt}\n\n{file_index}"
            if file_index
            else actual_user_prompt
        )
        if media_blocks:
            content = build_multimodal_content(prompt_text, media_blocks)
        else:
            content = prompt_text
        messages.append(HumanMessage(content=content))

    # ---- 流式 LLM 调用 ----

    async def _stream_llm_response(
        self,
        llm: BaseChatModel,
        messages: list[BaseMessage],
        node_key: str,
        state: FlowState,
        writer: Optional[StreamWriter],
    ) -> tuple[Optional[AIMessageChunk], list[str], str]:
        """
        流式调用 LLM 并收集响应

        对限流、网络错误、超时自动重试（间隔1s→2s→4s，最多3次）

        Returns:
            (完整响应, thinking片段列表, 累积文本内容)
        """
        response: Optional[AIMessageChunk] = None
        current_thinking = ""
        current_content = ""
        thinking_chunks: list[str] = []
        retry_count = 0

        while True:
            try:
                async for chunk in llm.astream(messages):
                    if self._check_interrupted(state):
                        break

                    if (
                        chunk.additional_kwargs
                        and "reasoning_content" in chunk.additional_kwargs
                    ):
                        thinking_chunk = chunk.additional_kwargs["reasoning_content"]
                        current_thinking += thinking_chunk
                        thinking_chunks.append(thinking_chunk)
                        self._emit(
                            writer,
                            NodeThinkingEvent(
                                node_key=node_key, content=thinking_chunk
                            ),
                        )

                    if chunk.content:
                        for text, is_thinking in self._parse_content_blocks(
                            chunk.content
                        ):
                            if is_thinking:
                                current_thinking += text
                                thinking_chunks.append(text)
                                self._emit(
                                    writer,
                                    NodeThinkingEvent(node_key=node_key, content=text),
                                )
                            else:
                                current_content += text
                                self._emit(
                                    writer,
                                    NodeContentEvent(node_key=node_key, content=text),
                                )

                    response = response + chunk if response else chunk

                break
            except _RETRYABLE_ERRORS as e:
                retry_count += 1
                if retry_count > len(_RETRY_DELAYS):
                    raise
                delay = _RETRY_DELAYS[retry_count - 1]
                self._emit(
                    writer,
                    LlmRetryEvent(
                        node_key=node_key,
                        message=f"LLM请求失败({e})，{delay}秒后重试({retry_count}/3)",
                        retry_count=retry_count,
                        max_retries=len(_RETRY_DELAYS),
                        wait_seconds=delay,
                    ),
                )
                await asyncio.sleep(delay)

        return response, thinking_chunks, current_content

    @staticmethod
    def _parse_content_blocks(content) -> list[tuple[str, bool]]:
        """
        解析 chunk.content，兼容 str 和 Anthropic content block 列表格式

        Anthropic streaming 返回的 content 可能是:
        - str: 普通文本
        - list[dict]: [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": "..."}, {"type": "signature", ...}]

        Returns:
            [(文本, 是否thinking), ...] 列表，忽略 signature 等无关块
        """
        if isinstance(content, str):
            if content:
                return [(content, False)]
            return []
        if isinstance(content, list):
            result: list[tuple[str, bool]] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")
                if block_type == "thinking":
                    text = block.get("thinking", "")
                    if text:
                        result.append((text, True))
                elif block_type == "text":
                    text = block.get("text", "")
                    if text:
                        result.append((text, False))
            return result
        return []

    # ---- 工具调用处理 ----

    async def _handle_tool_calls(
        self,
        tool_calls: list[dict],
        tools: list[BaseTool],
        msg_buf: MessageBuffer,
        node: FlowNode,
        state: FlowState,
        writer: Optional[StreamWriter],
        tool_call_count: int,
        max_tool_iterations: int,
    ) -> tuple[bool, int]:
        """
        统一处理所有工具调用（人工协助 + 普通工具）

        不同 MCP 服务器的工具调用并行执行（per-server 锁保证安全），
        非 MCP 工具也并行执行。人工介入工具单独处理。

        Returns:
            (是否应继续循环, 工具调用总次数)
        """
        # 扫描是否存在人工介入工具，存在则跳过所有其他工具（避免有副作用的工具先执行）
        human_help_idx = next(
            (
                i
                for i, tc in enumerate(tool_calls)
                if tc.get("name") == _REQUEST_HUMAN_HELP
            ),
            -1,
        )
        if human_help_idx >= 0:
            skip_msg = "人工介入，跳过其他工具调用"
            before = tool_calls[:human_help_idx]
            if before:
                self._reject_remaining_tools(
                    before, msg_buf, node.node_key, writer, skip_msg
                )
            after = tool_calls[human_help_idx + 1 :]
            if after:
                self._reject_remaining_tools(
                    after, msg_buf, node.node_key, writer, skip_msg
                )

            tool_call = tool_calls[human_help_idx]
            tool_name = tool_call.get("name", "")
            tool_args = tool_call.get("args", {})
            tool_id = tool_call.get("id", "")
            tool_call_count += 1
            self._emit_tool_start(writer, node.node_key, tool_name, tool_args)
            result = await self._handle_human_interaction(
                tool_args, tool_id, msg_buf.messages, node, state
            )
            msg_buf.append(
                ToolMessage(content=str(result), tool_call_id=tool_id, name=tool_name)
            )
            self._emit_tool_end(
                writer, node.node_key, tool_name, result, status="success"
            )
            return True, tool_call_count

        # ---- 工具确认（仅 Agent 模式） ----
        # session_id > 0 表示 Agent 模式（由 agent_executor_service 传入真实会话 ID），
        # Flow 模式 session_id 为 0，不触发确认逻辑
        if self.session_id > 0:
            config = node.base_config or {}
            if config.get("require_tool_approval"):
                approval_names = {
                    tc["name"] for tc in tool_calls
                } & _APPROVAL_REQUIRED_TOOLS
                if approval_names:
                    from app.services.tool_approval_service import (
                        tool_approval_service,
                    )

                    # 注册等待句柄并通过 SSE 通知前端
                    future = tool_approval_service.register(
                        self.session_id, tool_calls, list(approval_names)
                    )
                    self._emit(
                        writer,
                        ToolApprovalEvent(
                            node_key=node.node_key,
                            tool_calls=tool_calls,
                            approval_needed=list(approval_names),
                        ),
                    )
                    # 等待前端确认（5分钟超时，SSE 流保持连接）
                    try:
                        await asyncio.wait_for(future.event.wait(), timeout=300)
                    except asyncio.TimeoutError:
                        tool_approval_service.remove(self.session_id)
                        state.set_interrupted()
                        for tc in tool_calls:
                            tc_name = tc.get("name", "")
                            tc_id = tc.get("id", "")
                            msg = "工具确认超时（5分钟未响应），自动取消执行"
                            msg_buf.append(
                                ToolMessage(
                                    content=msg, tool_call_id=tc_id, name=tc_name
                                )
                            )
                            self._emit_tool_end(
                                writer, node.node_key, tc_name, msg, status="error"
                            )
                        return False, tool_call_count
                    tool_approval_service.remove(self.session_id)

                    if future.result == "rejected":
                        state.set_interrupted()
                        for tc in tool_calls:
                            tc_name = tc.get("name", "")
                            tc_id = tc.get("id", "")
                            msg = "用户拒绝执行"
                            msg_buf.append(
                                ToolMessage(
                                    content=msg, tool_call_id=tc_id, name=tc_name
                                )
                            )
                            self._emit_tool_end(
                                writer, node.node_key, tc_name, msg, status="error"
                            )
                        return False, tool_call_count

        # ---- 检查工具调用次数是否超限（整批检查） ----
        if tool_call_count + len(tool_calls) > max_tool_iterations:
            over_idx = max_tool_iterations - tool_call_count
            if over_idx < 0:
                over_idx = 0
            if over_idx < len(tool_calls):
                limit_msg = f"超过最大工具调用次数: {max_tool_iterations}"
                self._reject_remaining_tools(
                    tool_calls[over_idx:], msg_buf, node.node_key, writer, limit_msg
                )
                self._emit(
                    writer,
                    ToolCallLimitEvent(
                        node_key=node.node_key,
                        max_iterations=max_tool_iterations,
                    ),
                )
                state.add_error(
                    node.node_key, f"超过最大工具调用次数: {max_tool_iterations}"
                )
                tool_calls = tool_calls[:over_idx]
                if not tool_calls:
                    return False, tool_call_count

        # ---- 并行执行工具调用 ----
        tool_call_count += len(tool_calls)

        async def _run_single_tool(tool_call: dict) -> tuple[dict, Any]:
            """执行单个工具调用并返回 (tool_call, result)"""
            tool_name = tool_call.get("name", "")
            tool_args = tool_call.get("args", "")
            if self._check_interrupted(state):
                return tool_call, {"success": False, "error": "执行被中断"}
            self._emit_tool_start(writer, node.node_key, tool_name, tool_args)
            result = await self._execute_tool(tool_name, tool_args, tools, state)
            return tool_call, result

        results = await asyncio.gather(
            *[_run_single_tool(tc) for tc in tool_calls], return_exceptions=True
        )

        for tool_call, raw_result in results:
            if isinstance(raw_result, Exception):
                raw_result = {
                    "success": False,
                    "error": f"工具执行异常: {str(raw_result)}",
                }

            tool_name = tool_call.get("name", "")
            tool_id = tool_call.get("id", "")

            tool_status = "error"
            if not isinstance(raw_result, Exception):
                try:
                    parsed = (
                        json.loads(raw_result)
                        if isinstance(raw_result, str)
                        else raw_result
                    )
                    if not (
                        isinstance(parsed, dict) and parsed.get("success") is False
                    ):
                        tool_status = "success"
                except (json.JSONDecodeError, TypeError):
                    tool_status = "success"

            msg_buf.append(
                ToolMessage(
                    content=str(raw_result), tool_call_id=tool_id, name=tool_name
                )
            )
            self._emit_tool_end(
                writer, node.node_key, tool_name, raw_result, tool_status
            )

        return True, tool_call_count

    def _reject_remaining_tools(
        self,
        remaining_calls: list[dict],
        msg_buf: MessageBuffer,
        node_key: str,
        writer: Optional[StreamWriter],
        reason: str,
    ) -> None:
        """拒绝剩余的工具调用（发送失败事件 + ToolMessage）"""
        for call in remaining_calls:
            call_id = call.get("id", "")
            call_name = call.get("name", "")
            self._emit_tool_end(
                writer,
                node_key,
                call_name,
                {"success": False, "error": reason},
                status="error",
            )
            msg_buf.append(
                ToolMessage(content=reason, tool_call_id=call_id, name=call_name)
            )

    async def _handle_human_interaction(
        self,
        tool_args: dict,
        tool_id: str,
        messages: list[BaseMessage],
        node: FlowNode,
        state: FlowState,
    ) -> str:
        """
        处理人工协助工具调用

        流程：保存当前进度 → LangGraph interrupt → 等待用户输入 → 返回用户回复
        """
        question = tool_args.get("question", "需要您的帮助")
        context_str = tool_args.get("context")

        # 在 interrupt 前保存进度（state + DB），确保前端能展示当前对话
        state.set_conversation_messages(node.node_key, list(messages))
        await self._save_history_to_db(messages, node.node_key)

        # 触发 LangGraph interrupt，暂停执行等待用户输入
        human_input = interrupt(
            {
                "type": "human_input_required",
                "node_key": node.node_key,
                "question": question,
                "context": context_str,
                "tool_call_id": tool_id,
            }
        )

        return human_input

    # ---- LLM 创建 ----

    def _create_llm(
        self,
        api_key: str,
        model: str,
        base_url: str = "",
        max_tokens: int = 8192,
        provider_name: str = "deepseek",
        temperature: float = 0.7,
        extra_body: Optional[dict] = None,
        reasoning_effort: Optional[str] = None,
    ) -> BaseChatModel:
        """通过 AI 提供商创建 LLM 实例"""
        provider = create_provider(provider_name, api_key, base_url)
        kwargs: dict = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "streaming": True,
            "verbose": True,
        }
        if extra_body:
            kwargs["extra_body"] = extra_body
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        return provider.create_chat_model(**kwargs)

    # ---- 对话历史管理 ----

    async def _load_history_from_db(
        self, node_config: dict, node_key: str
    ) -> list[BaseMessage]:
        """
        从数据库加载对话历史（仅首次执行时调用）

        Agent 模式（session_id > 0）: 始终加载全部历史
        Flow 模式: 支持 history_mode:
        - "none": 不加载历史
        - "flow": 加载整个流程的历史
        - "node": 仅加载当前节点的历史（默认）
        """
        if not self.conversation_service or not self.db_session_factory:
            return []

        max_history_turns = node_config.get("max_history_turns", 10)

        # Agent 模式：始终加载全部对话历史
        if self.session_id:
            try:
                async with self.db_session_factory() as db:
                    messages = await self.conversation_service.get_full_history(
                        db, self._id_param
                    )
                    return list(messages)
            except Exception:
                return []

        # Flow 模式：按 history_mode 区分
        history_mode = node_config.get("history_mode", "node")
        if history_mode == "none":
            return []

        try:
            async with self.db_session_factory() as db:
                if history_mode == "flow":
                    messages = await self.conversation_service.get_full_history(
                        db, self._id_param, limit=max_history_turns * 4
                    )
                else:
                    messages = await self.conversation_service.get_history(
                        db, self._id_param, node_key, limit=max_history_turns * 4
                    )
                return list(messages)
        except Exception:
            return []

    # ---- 工具执行 ----

    async def _execute_tool(
        self, tool_name: str, tool_args: dict, tools: list[BaseTool], state: FlowState
    ) -> Any:
        """按名称查找并执行工具"""
        if self._check_interrupted(state):
            return {"success": False, "error": "执行被中断"}
        for tool in tools:
            if tool.name == tool_name:
                try:
                    return await tool.ainvoke(tool_args)
                except Exception as e:
                    return {"success": False, "error": f"工具执行错误: {str(e)}"}
        return {"success": False, "error": f"未找到工具: {tool_name}"}

    # ---- 输入/输出内容（用于执行结果显示） ----

    @classmethod
    def get_input_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        """获取LLM节点的输入内容"""
        if config is None:
            config = node.base_config or {}
        input_data = {}

        input_vars = config.get("input_variables", [])
        context = {}
        for var in input_vars:
            name = var.get("name", "")
            source = var.get("source", "")
            if name and source:
                value = resolver.resolve_safe(source, state)
                context[name] = value
                input_data[name] = value

        system_prompt = config.get("system_prompt")
        if system_prompt:
            rendered = resolver.render_template(system_prompt, state, context)
            input_data["system_prompt"] = rendered

        user_prompt = config.get("user_prompt")
        if user_prompt:
            rendered = resolver.render_template(user_prompt, state, context)
            input_data["user_prompt"] = rendered

        return input_data if input_data else None

    @classmethod
    def get_output_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        """获取LLM节点的输出内容"""
        if config is None:
            config = node.base_config or {}
        output = {}

        output_vars = config.get("output_variables", [])
        if not output_vars:
            output_vars = [{"name": "result"}, {"name": "thinking"}]

        for var in output_vars:
            name = (
                var.get("name", "")
                if isinstance(var, dict)
                else getattr(var, "name", "")
            )
            if name:
                value = state.get_node_variable(node.node_key, name)
                if value is not None:
                    output[name] = value

        return output if output else None


# LLM 节点处理器工厂函数
@NodeHandlerRegistry.register_factory("llm")
def create_llm_handler(
    flow,
    db_session_factory,
    execution_id: int,
    conversation_service,
    handler_registry: Optional[dict] = None,
    session_id: int = 0,
):
    """
    创建 LLM 节点处理器实例

    Args:
        flow: 流程对象
        db_session_factory: 数据库会话工厂
        execution_id: 执行记录ID（Flow 模式）
        conversation_service: 对话服务
        handler_registry: 工具处理器注册表
        session_id: 会话ID（Agent 模式）
    """
    return LlmToolNodeHandler(
        flow=flow,
        db_session_factory=db_session_factory,
        execution_id=execution_id,
        conversation_service=conversation_service,
        handler_registry=handler_registry,
        session_id=session_id,
    )

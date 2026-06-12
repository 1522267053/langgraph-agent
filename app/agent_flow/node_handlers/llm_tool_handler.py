"""
LLM 节点处理器主入口

支持：
- MCP 工具调用（通过连接 MCP 节点）
- 人工协助工具（通过连接 Human 节点）
- 多轮工具调用（ReAct 循环）
- 多轮人工交互（使用 LangGraph interrupt 机制）
- 对话历史管理（通过 state.conversation_messages 自动恢复）
- 流式输出（通过 StreamWriter）
- 中断检测（通过 interrupt_service）
- 工具输出统一截断（通过 tool_output_truncate 模块）

子模块职责：
- llm_factory: LLM 实例创建和工具绑定
- llm_message_builder: 消息构建（历史加载、恢复、multimodal）
- llm_stream: 流式 LLM 调用（重试、thinking 解析）
- llm_tool_executor: 工具调用处理（执行、人工交互、审批、截断）
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Optional, Union

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    SystemMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter

from pydantic import Field

from app.models.flow_node import FlowNode
from app.services.agent_conversation_service import AgentConversationService
from app.services.conversation_service import ConversationService
from app.services.interrupt_service import interrupt_service
from app.agent_flow.exceptions import NodeExecutionError
from app.agent_flow.flow_context import FlowState
from app.agent_flow.flow_event import (
    ErrorEvent,
    NodeStartEvent,
    TokenUsageEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.node_handlers.base_handler import (
    BaseNodeHandler,
    BaseNodeConfig,
    NodeVariable,
)
from app.agent_flow.message_buffer import MessageBuffer
from app.utils.message_utils import extract_token_usage

from app.agent_flow.node_handlers.llm_factory import prepare_llm
from app.agent_flow.node_handlers.llm_message_builder import (
    build_initial_messages,
    should_auto_compress,
)
from app.agent_flow.node_handlers.llm_stream import stream_llm_response
from app.agent_flow.node_handlers.llm_tool_executor import (
    handle_tool_calls,
    setup_tool_handlers,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# 自动压缩阈值比例：已用 token 超过 context_length 的此比例时触发压缩
COMPRESS_THRESHOLD_RATIO = 0.83


class LlmNodeConfig(BaseNodeConfig):
    """LLM 节点配置模型"""

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
        description="用户提示词模板（必填，否则 LLM 收不到消息。支持变量插值，如: {{message}}）",
    )
    require_tool_approval: bool = Field(
        False, description="危险工具执行前需要用户确认（仅 Agent 模式生效）"
    )
    extra_body: dict = Field(
        {}, description="附加请求参数（JSON 对象，会合并到请求体中）"
    )
    reasoning_effort: Optional[str] = Field(
        None,
        description="推理深度（low/medium/high），部分模型支持",
        json_schema_extra={"options": ["low", "medium", "high"]},
    )
    context_length: int = Field(
        0, description="模型上下文窗口大小（token 数，0 表示不限制）"
    )


class LlmToolNodeHandler(BaseNodeHandler):
    """
    增强版 LLM 节点处理器

    支持 MCP 工具调用和多轮人工协助。
    工具通过连接到 LLM 节点的 MCP 和 Human 节点提供。
    使用 LangGraph interrupt 机制实现人工交互。
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
        """检查是否被中断

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

    def check_config(
        self,
        config: dict,
        node_key: str,
        state: FlowState,
        writer: Optional[StreamWriter] = None,
    ) -> dict | None:
        """校验 LLM 必填配置（model、api_key）"""
        model = self._require_config(config, "model", node_key, "模型", state, writer)
        if not model:
            return None
        api_key = self._require_config(
            config, "api_key", node_key, "API Key", state, writer
        )
        if not api_key:
            return None
        return {"model": model, "api_key": api_key}

    def _emit_tool_start(self, writer, node_key, tool_name, tool_args):
        """发送工具调用开始事件"""
        self._emit(
            writer,
            ToolCallStartEvent(
                node_key=node_key, tool_name=tool_name, tool_args=tool_args
            ),
        )

    def _emit_tool_end(self, writer, node_key, tool_name, result, status="success"):
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
        执行增强版 LLM 节点，支持多轮人工交互和流式输出

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
        tools, prompt_hints = await setup_tool_handlers(
            node,
            state,
            writer,
            config,
            cfg,
            flow=self.flow,
            db_session_factory=self.db_session_factory,
            handler_registry=self.handler_registry,
            emit_fn=self._emit,
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

        # 发送 node_start 事件
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
        _, llm_with_tools, _ = prepare_llm(
            node.base_config or {}, tools, node.node_key, state
        )
        messages = await build_initial_messages(
            node,
            node.base_config or {},
            user_prompt,
            state,
            session_id=self.session_id,
            execution_id=self.execution_id,
            conversation_service=self.conversation_service,
            db_session_factory=self.db_session_factory,
            config=config,
            writer=writer,
            emit_fn=self._emit,
            emit_tool_end_fn=self._emit_tool_end,
        )

        # 自动上下文压缩：超过阈值时调用 LLM 压缩旧消息
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
        prompt_tokens = await should_auto_compress(
            self.session_id, self.db_session_factory, cfg_context_length
        )
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
            logger.info(f"LLM节点被取消, node_key={node.node_key}")
            try:
                await asyncio.shield(msg_buf.save_to_db())
            except Exception as e:
                logger.warning(f"取消时保存消息失败: {e}")
            if self.session_id:
                from app.services.agent_executor_service import (
                    agent_executor_service,
                )

                agent_executor_service._pending_save_sessions.discard(self.session_id)
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
                f"LLM节点异常, node_key={node.node_key}, error={type(e).__name__}: {e}"
            )
            try:
                await msg_buf.save_to_db()
            except Exception:
                pass
            if self.session_id:
                from app.services.agent_executor_service import (
                    agent_executor_service,
                )

                agent_executor_service._pending_save_sessions.discard(self.session_id)
            state.add_error(node.node_key, f"LLM调用失败: {str(e)}")
            state.set_conversation_messages(node.node_key, list(msg_buf.messages))
            raise

        return state

    # ---- ReAct 循环 ----

    async def _run_react_loop(
        self,
        llm: BaseChatModel,
        system_prompt: Optional[str],
        msg_buf: MessageBuffer,
        tools: list,
        node: FlowNode,
        state: FlowState,
        writer: Optional[StreamWriter],
        max_tool_iterations: int,
        *,
        context_length: int = 0,
    ) -> tuple[str, list[str]]:
        """ReAct 循环：流式调用 LLM → 处理工具调用 → 继续调用

        核心编排逻辑，调用 llm_stream 模块进行流式调用，
        调用 llm_tool_executor 模块处理工具调用。

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

            # 流式调用 LLM
            (
                response,
                current_thinking,
                current_content,
            ) = await stream_llm_response(
                llm,
                call_messages,
                node.node_key,
                state,
                writer,
                check_interrupted_fn=self._check_interrupted,
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
                        # 先将当前 response 追加到 msg_buf，确保压缩时能看到 LLM 最新输出
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

            # 处理工具调用
            should_continue, tool_call_count = await handle_tool_calls(
                response.tool_calls,
                tools,
                msg_buf,
                node,
                state,
                writer,
                tool_call_count,
                max_tool_iterations,
                session_id=self.session_id,
                check_interrupted_fn=self._check_interrupted,
                emit_fn=self._emit,
                emit_tool_start_fn=self._emit_tool_start,
                emit_tool_end_fn=self._emit_tool_end,
            )
            if not should_continue:
                break

        return last_content, thinking_content

    # ---- 输入/输出内容（用于执行结果显示） ----

    @classmethod
    def get_input_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        """获取 LLM 节点的输入内容"""
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
        """获取 LLM 节点的输出内容"""
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
    """创建 LLM 节点处理器实例

    Args:
        flow: 流程对象
        db_session_factory: 数据库会话工厂
        execution_id: 执行记录 ID（Flow 模式）
        conversation_service: 对话服务
        handler_registry: 工具处理器注册表
        session_id: 会话 ID（Agent 模式）
    """
    return LlmToolNodeHandler(
        flow=flow,
        db_session_factory=db_session_factory,
        execution_id=execution_id,
        conversation_service=conversation_service,
        handler_registry=handler_registry,
        session_id=session_id,
    )

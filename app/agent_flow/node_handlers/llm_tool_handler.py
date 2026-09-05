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
- tools/structured_output: JSON 结构化输出工具（普通工具执行链路 + Pydantic 参数校验）
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Optional, Union

from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import Field, field_validator

from app.models.flow_node import FlowNode
from app.services.agent_conversation_service import AgentConversationService
from app.services.conversation_service import ConversationService
from app.services.interrupt_service import interrupt_service
from app.agent_flow.exceptions import NodeExecutionError
from app.agent_flow.flow_context import FlowState
from app.agent_flow.flow_event import (
    ErrorEvent,
    FlowPreviewEvent,
    KnowledgeCitationsEvent,
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
from app.utils.knowledge_reference import (
    KNOWLEDGE_CITATIONS_KEY,
    collect_message_knowledge_references,
    filter_references_in_content,
    merge_knowledge_references,
    validate_knowledge_citations,
)

from app.agent_flow.node_handlers.llm_factory import prepare_llm
from app.agent_flow.node_handlers.llm_message_builder import (
    build_initial_messages,
    should_auto_compress,
)
from app.agent_flow.node_handlers.llm_stream import stream_llm_response
from app.agent_flow.node_handlers.llm_tool_executor import (
    _PLAN_DISABLED_TOOL_NAMES,
    _is_plan_disabled_tool,
    handle_tool_calls,
    setup_tool_handlers,
)
from app.agent_flow.node_handlers.llm_tool_executor import ReactLoopContext
from app.agent_flow.tools.structured_output import (
    OUTPUT_TOOL_NAME,
    StructuredOutputService,
)

if TYPE_CHECKING:
    from app.agent_flow.tool_resolver import FlowLike

logger = logging.getLogger(__name__)

# 自动压缩阈值比例：已用 token 超过 context_length 的此比例时触发压缩
COMPRESS_THRESHOLD_RATIO = 0.83
_MAX_REACT_COMPRESSIONS = 3

_KNOWLEDGE_CITATION_PROMPT = """

# 知识库引用规则

知识片段中的 `[段落ID:x]` 是可验证引用标记。回答使用某个片段的事实时，必须在对应句子末尾原样保留该标记。只能使用当前上下文或工具结果中实际出现的标记，不得自行编造；未使用知识库内容时不要添加引用。
"""


def _build_mode_prompt(is_plan_mode: bool) -> str:
    """构建模式提醒段（<system-reminder> 内的运行模式章节）"""
    disabled_tools = (
        "、".join(sorted(_PLAN_DISABLED_TOOL_NAMES))
        + "（按前缀匹配；call_sub_agent_* 为子Agent 委派工具）"
    )
    if is_plan_mode:
        return f"""
# 当前运行模式：计划模式（Plan Mode）

你现在处于「计划模式」，处于只读阶段。以下约束优先于一切其他指令，包括用户在对话中直接提出的修改类请求（此时应将其纳入计划，等模式切换后再执行），零例外：
1. **只读探索**：可以读取文件、搜索代码、检索知识库来理解问题，但不得执行写入、编辑、删除或其他修改性操作。
2. **禁用工具**：以下工具在当前模式不可用：{disabled_tools}。不要尝试调用这些工具，也不要尝试用其他工具（如 Shell 重定向）绕过。
3. **Shell 限制**：`shell_executor` 当前可用，但仅允许执行只读、安全的探索命令；禁止通过 Shell 修改、删除文件或执行破坏性命令。
4. **主动澄清**：如果需求有歧义、边界不清或存在多种方案，先向用户提问确认，不要对用户意图做大幅假设。
5. **产出计划**：分析完成后，必须产出清晰、可执行的实施计划。若可用 `todowrite` 工具，请用它拆解有序任务；否则用 Markdown 列表输出计划。计划应包含：要改动的文件/模块、具体动作、潜在风险与注意事项。
6. **回合终止约束**：每轮回复只能以两种方式结束——向用户提出澄清问题，或输出完整实施计划。不要在未产出计划且未提问的情况下草率结束。
"""

    return f"""
# 当前运行模式：普通执行模式（Normal Mode）

你现在处于「普通执行模式」，可以根据用户需求执行任务并使用当前已提供的工具。
- 如果此前处于计划模式，现已切换到普通执行模式：此前的只读限制不再适用，可以正常修改文件、执行命令。
- 以下工具在计划模式下会被禁用，但在当前普通执行模式下可用：{disabled_tools}。
请根据任务需要正常使用这些工具，并遵守各工具自身的安全限制。
"""


def _build_runtime_reminder(is_plan_mode: bool, fragments: list[str]) -> str:
    """构建消息层运行时提醒（<system-reminder> 包装），随每轮 LLM 调用临时注入。

    不进入 system_prompt/checkpoint/DB：消息层注入紧邻最新对话、注意力权重高，
    且动态内容（模式切换、时间、各 handler 运行时片段）不破坏 system_prompt
    的前缀缓存。时间固定在此拼装（LLM 调用级信息，不属于任何工具 handler）；
    fragments 来自各工具 handler 的 get_runtime_reminder（如工作目录）。
    """
    sections = [_build_mode_prompt(is_plan_mode)]
    env_lines = [f"- 当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    # 多行片段续行缩进对齐，保持 "- " 列表项渲染一致
    env_lines.extend(f"- {fragment}".replace("\n", "\n  ") for fragment in fragments)
    sections.append("# 运行环境\n" + "\n".join(env_lines))
    return "<system-reminder>" + "\n".join(sections) + "</system-reminder>"


def _tool_call_name(tool_call) -> str:
    """兼容 dict / ToolCall 两种形态取工具名"""
    if isinstance(tool_call, dict):
        return tool_call.get("name", "")
    return getattr(tool_call, "name", "")


class LlmNodeConfig(BaseNodeConfig):
    """LLM 节点配置模型"""

    input_variables: list[NodeVariable] = Field(
        default=[],
        description="输入变量映射列表",
    )
    file_inputs: list[str] = Field(
        default_factory=list,
        description="文件输入变量路径列表（如 nodes.python_1.result），"
        "解析后的文件作为多模态附件发送给模型",
    )
    output_variables: list[NodeVariable] = Field(
        default=[NodeVariable(name="result"), NodeVariable(name="thinking")],
        description="输出变量列表",
    )
    provider: str = Field(
        "",
        description="供应商标识（留空则使用系统全局默认）",
        json_schema_extra={"options": []},
    )
    model: str = Field("", description="模型名称（留空则使用系统全局默认）")
    api_key: Optional[str] = Field(default=None, description="API Key")
    base_url: str = Field(
        default="", description="API 地址（为空则使用供应商默认地址）"
    )
    temperature: float = Field(default=0.7, description="温度参数（0-2），越高越随机")
    max_tokens: int = Field(default=8192, description="最大生成 token 数")
    max_tool_iterations: int = Field(default=200, description="最大工具调用轮次")
    history_mode: str = Field(
        default="node",
        description="对话历史模式",
        json_schema_extra={"options": ["node", "flow", "none"]},
    )
    max_history_turns: int = Field(default=10, description="最大对话历史轮次")
    capabilities: dict = Field(
        default_factory=lambda: {
            "image": False,
            "video": False,
            "audio": False,
            "pdf": False,
        },
        description="模型能力开关",
    )
    system_prompt: Optional[str] = Field(
        default=None, description="系统提示词，定义 LLM 的角色和行为"
    )
    user_prompt: str = Field(
        ...,
        description="用户提示词模板（必填，否则 LLM 收不到消息。支持变量插值，如: {{message}}）",
    )
    approval_required_tools: list[str] = Field(
        default_factory=list,
        description="执行前需要用户确认的完整工具名列表（仅 Agent 模式生效）",
    )
    extra_body: dict = Field(
        default_factory=dict, description="附加请求参数（JSON 对象，会合并到请求体中）"
    )
    reasoning_effort: Optional[str] = Field(
        default=None,
        description="推理深度（low/medium/high），部分模型支持",
        json_schema_extra={"options": ["low", "medium", "high"]},
    )
    stream_usage: Optional[bool] = Field(
        default=True,
        description="流式请求是否发送 stream_options.include_usage"
        "（None 继承系统全局配置，部分 OpenAI 兼容 API 不支持需关闭）",
    )
    context_length: int = Field(
        default=0, description="模型上下文窗口大小（token 数，0 表示不限制）"
    )
    required_tools: list[str] = Field(
        default_factory=list,
        description="必需调用的工具名列表。LLM 本轮未调用时自动提醒重试（简单模式）",
    )
    tool_check_script: str = Field(
        default="",
        description="自定义检查脚本（高级模式，留空走简单模式）。"
        "签名: def main(called_tools, last_result): "
        "return {'need_retry': bool, 'hint': str}",
    )
    required_tools_max_retries: int = Field(
        default=2, description="必需工具未调用时的最大提醒重试次数"
    )
    required_tools_hint: str = Field(
        default="",
        description="提醒消息模板，{{tools}} 占位符替换为缺失工具名（留空使用默认模板）",
    )
    json_output_enabled: bool = Field(
        default=False,
        description="启用 JSON 结构化输出：绑定 structured_output 虚拟工具，"
        "模型以其调用参数给出 JSON 结果（自动并入必需工具检查清单）",
    )
    json_fields: list[dict] = Field(
        default_factory=list,
        description="结构化输出字段树 [{name, type, description, required, "
        "item_type(仅array), children(object子字段/数组元素字段)}]",
    )
    compress_extra_prompt: Optional[str] = Field(
        default="",
        description="上下文压缩时使用的额外提示词，可用于生成指定格式的上下文压缩格式",
    )

    @field_validator("approval_required_tools")
    @classmethod
    def normalize_approval_required_tools(cls, value: list[str]) -> list[str]:
        """清理手动输入的工具名并保持原顺序去重。"""
        return list(dict.fromkeys(name.strip() for name in value if name.strip()))


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
        flow: Optional["FlowLike"] = None,
        db_session_factory: Optional[Callable[[], AsyncSession]] = None,
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

    def _emit_tool_start(
        self,
        writer,
        node_key,
        tool_name,
        tool_args,
        tool_call_id: Optional[str] = None,
    ):
        """发送工具调用开始事件"""
        self._emit(
            writer,
            ToolCallStartEvent(
                node_key=node_key,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_call_id=tool_call_id,
            ),
        )

    def _emit_tool_end(
        self,
        writer,
        node_key,
        tool_name,
        result,
        status="success",
        tool_call_id: Optional[str] = None,
    ):
        """发送工具调用结束事件"""
        self._emit(
            writer,
            ToolCallEndEvent(
                node_key=node_key,
                tool_name=tool_name,
                status=status,
                result=result,
                tool_call_id=tool_call_id,
            ),
        )

    def _emit_knowledge_citations(
        self, writer, node_key: str, citations: list[dict]
    ) -> None:
        """发送最终回答中经过校验的知识引用。"""
        if not citations:
            return
        self._emit(
            writer,
            KnowledgeCitationsEvent(node_key=node_key, citations=citations),
        )

    async def _emit_flow_preview(self, writer, flow_id: int, action: str):
        """查询 flow 详情并发送流程预览事件

        在工具执行批次完成后，检测到流程变更时调用。
        使用独立 DB 会话查询最新流程结构（节点+边）。
        """
        if not self.db_session_factory:
            return
        try:
            from app.config.database import AsyncSessionLocal
            from app.services.flow_service import flow_service
            from app.schemas.flow_node_schema import FlowNodeBase
            from app.schemas.flow_edge_schema import FlowEdgeBase

            async with AsyncSessionLocal() as db:
                flow = await flow_service.get_with_nodes_and_edges(db, flow_id)
                if not flow:
                    # 流程已被删除，发送精简事件通知前端
                    if action == "delete":
                        self._emit(
                            writer,
                            FlowPreviewEvent(
                                flow_id=flow_id, action="delete", flow_name=""
                            ),
                        )
                    return
                nodes_views = (
                    FlowNodeBase.model_to_view_batch(flow.nodes) if flow.nodes else []
                )
                edges_views = (
                    FlowEdgeBase.model_to_view_batch(flow.edges) if flow.edges else []
                )
                # Schema 实例转为 dict（mode="json" 确保 datetime 等类型转为可序列化字符串）
                nodes_dicts = [n.model_dump(mode="json") for n in nodes_views]
                edges_dicts = [e.model_dump(mode="json") for e in edges_views]

            self._emit(
                writer,
                FlowPreviewEvent(
                    flow_id=flow_id,
                    flow_name=flow.name,
                    flow_type=getattr(flow, "flow_type", None),
                    action=action,
                    nodes=nodes_dicts,
                    edges=edges_dicts,
                ),
            )
        except Exception as e:
            logger.warning(f"发送流程预览事件失败 flow_id={flow_id}: {e}")

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
        # runtime_reminders: 各工具 handler 的动态提醒片段（如工作目录），
        # 供消息层 <system-reminder> 拼装
        tools, prompt_hints, runtime_reminders = await setup_tool_handlers(
            node,
            state,
            writer,
            config,
            cfg,
            flow=self.flow,
            db_session_factory=self.db_session_factory,
            handler_registry=self.handler_registry,
            emit_fn=self._emit,
            session_id=self.session_id,
        )

        # 计划模式：禁用写操作工具（含子Agent 委派），并同步剔除 required_tools 中的被禁工具
        is_plan_mode = bool(state.get_variable("plan_mode"))
        plan_required_tools = cfg.required_tools
        if is_plan_mode:
            tools = [t for t in tools if not _is_plan_disabled_tool(t.name)]
            plan_required_tools = [
                r for r in cfg.required_tools if not _is_plan_disabled_tool(r)
            ]

        # 必需工具可用性过滤：未连接的工具不参与检查，避免模型永远无法调用而空转重试
        available_tool_names = {t.name for t in tools}
        plan_required_tools = [
            r for r in plan_required_tools if r in available_tool_names
        ]

        # 共享已调用工具集合：_run_react_loop 变更，结构化输出工具清单门控读取
        called_tools: set[str] = set()

        # JSON 结构化输出：构建 structured_output 普通工具并入工具列表（计划模式
        # 不启用），执行/展示复用 handle_tool_calls 统一链路；自动加入必需工具清单
        structured_service = StructuredOutputService(cfg.json_fields)
        json_output_tool = None
        if cfg.json_output_enabled and not is_plan_mode:
            if structured_service.enabled:
                json_output_tool = structured_service.build_tool(
                    get_called_tools=lambda: called_tools,
                    required_tools=plan_required_tools,
                )
                tools.append(json_output_tool)
            else:
                logger.warning(
                    "节点[%s] 已启用 JSON 结构化输出但未配置有效字段，已忽略",
                    node.node_key,
                )
        checklist = list(plan_required_tools)
        if json_output_tool is not None:
            checklist.append(OUTPUT_TOOL_NAME)

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

        knowledge_reference_sources: dict[str, int] = {}
        if self.flow:
            for flow_node in self.flow.nodes:
                if flow_node.node_type != "knowledge":
                    continue
                knowledge_base_id = (flow_node.base_config or {}).get(
                    "knowledge_base_id"
                )
                if isinstance(knowledge_base_id, int):
                    knowledge_reference_sources[
                        f"nodes.{flow_node.node_key}.knowledge_references"
                    ] = knowledge_base_id

        preloaded_references: list[dict] = []
        citation_context = f"{system_prompt or ''}\n{user_prompt}"
        for key, knowledge_base_id in knowledge_reference_sources.items():
            value = state.variables.get(key)
            visible = [
                reference
                for reference in filter_references_in_content(citation_context, value)
                if reference.get("knowledge_base_id") == knowledge_base_id
            ]
            preloaded_references = merge_knowledge_references(
                preloaded_references, visible
            )

        allowed_knowledge_base_ids: set[int] = set()
        for tool in tools:
            metadata = getattr(tool, "metadata", None) or {}
            if not metadata.get("knowledge_tool"):
                continue
            knowledge_base_id = metadata.get("knowledge_base_id")
            if isinstance(knowledge_base_id, int):
                allowed_knowledge_base_ids.add(knowledge_base_id)
        for reference in preloaded_references:
            knowledge_base_id = reference.get("knowledge_base_id")
            if isinstance(knowledge_base_id, int):
                allowed_knowledge_base_ids.add(knowledge_base_id)

        # 追加工具节点的 system_prompt 提示
        for hint in prompt_hints:
            system_prompt = (system_prompt or "") + hint
        if preloaded_references:
            system_prompt = (system_prompt or "") + _KNOWLEDGE_CITATION_PROMPT

        # JSON 结构化输出引导：字段定义 + 必须调用 structured_output 的约束
        if json_output_tool is not None:
            system_prompt = (system_prompt or "") + structured_service.build_prompt()

        # 始终说明当前模式/时间/运行环境，避免模型仅根据工具列表推断权限；
        # 以消息层 <system-reminder> 注入（见 _run_react_loop），不占用 system_prompt
        mode_reminder = _build_runtime_reminder(is_plan_mode, runtime_reminders)

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
            history_mode=cfg.history_mode,
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
        called_tools: set[str] = set()
        output_names = self._get_output_var_names(node, ["result", "thinking"])
        result_name = output_names[0] if len(output_names) > 0 else "result"
        thinking_name = output_names[1] if len(output_names) > 1 else "thinking"
        try:
            ctx = ReactLoopContext(
                llm=llm_with_tools,
                system_prompt=system_prompt,
                mode_reminder=mode_reminder,
                msg_buf=msg_buf,
                tools=tools,
                node=node,
                state=state,
                writer=writer,
                session_id=self.session_id,
                check_interrupted_fn=self._check_interrupted,
                emit_fn=self._emit,
                emit_tool_start_fn=self._emit_tool_start,
                emit_tool_end_fn=self._emit_tool_end,
                emit_flow_preview_fn=self._emit_flow_preview,
                max_tool_iterations=max_tool_iterations,
                context_length=cfg_context_length,
                approval_required_tools=cfg.approval_required_tools,
                required_tools=plan_required_tools,
                tool_check_script=cfg.tool_check_script,
                required_tools_max_retries=cfg.required_tools_max_retries,
                required_tools_hint=cfg.required_tools_hint,
                structured_service=(
                    structured_service if json_output_tool is not None else None
                ),
                called_tools=called_tools,
                initial_knowledge_references=preloaded_references,
                allowed_knowledge_base_ids=allowed_knowledge_base_ids,
            )
            last_content, thinking_content, called_tools = await self._run_react_loop(
                ctx
            )
            if last_content:
                state.set_node_variable(node.node_key, result_name, last_content)
            if json_output_tool is not None:
                if not structured_service.accepted:
                    raise NodeExecutionError(
                        node.node_key,
                        "未收到有效的 JSON 结构化输出：structured_output 工具未被成功调用",
                    )
                structured_result = structured_service.accepted_result
                state.set_node_variable(
                    node.node_key, "structured_output", structured_result
                )
                if not last_content:
                    # 模型仅通过工具给出结果时，回填 JSON 文本保证下游 result 引用可用
                    state.set_node_variable(
                        node.node_key,
                        result_name,
                        json.dumps(structured_result, ensure_ascii=False),
                    )
            if thinking_content:
                state.set_node_variable(
                    node.node_key, thinking_name, "".join(thinking_content)
                )
            state.set_node_variable(node.node_key, "called_tools", list(called_tools))
            state.set_conversation_messages(node.node_key, list(msg_buf.messages))
            await msg_buf.save_to_db()
        except asyncio.CancelledError:
            logger.info(f"LLM节点被取消, node_key={node.node_key}")
            save_error: BaseException | None = None
            if self.session_id:
                from app.services.agent_executor_service import (
                    agent_executor_service,
                )

                save_error = await agent_executor_service._await_cancellation_safe(
                    msg_buf.save_to_db()
                )
            else:
                try:
                    await asyncio.shield(msg_buf.save_to_db())
                except (Exception, asyncio.CancelledError) as exc:
                    save_error = exc
            if save_error:
                logger.warning(f"取消时保存消息失败: {save_error}")
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

    async def _revalidate_knowledge_references(
        self,
        references: list[dict],
        allowed_knowledge_base_ids: set[int],
    ) -> list[dict]:
        """按当前连接的知识库重新校验历史候选引用。"""
        candidates = merge_knowledge_references(references)
        if not candidates or not allowed_knowledge_base_ids:
            return []

        grouped_segment_ids: dict[int, list[int]] = {}
        candidate_by_id: dict[str, dict] = {}
        for reference in candidates:
            knowledge_base_id = reference.get("knowledge_base_id")
            segment_id = reference.get("segment_id")
            if (
                not isinstance(knowledge_base_id, int)
                or knowledge_base_id not in allowed_knowledge_base_ids
                or not isinstance(segment_id, int)
            ):
                continue
            grouped_segment_ids.setdefault(knowledge_base_id, []).append(segment_id)
            candidate_by_id[reference["reference_id"]] = reference

        if not grouped_segment_ids:
            return []

        try:
            from app.config.database import AsyncSessionLocal
            from app.services.knowledge_title_service import knowledge_title_service

            session_factory = self.db_session_factory or AsyncSessionLocal
            refreshed: list[dict] = []
            async with session_factory() as db:
                for knowledge_base_id, segment_ids in grouped_segment_ids.items():
                    score_map = {}
                    method_map = {}
                    for segment_id in segment_ids:
                        candidate = candidate_by_id.get(f"segment:{segment_id}", {})
                        score_map[segment_id] = candidate.get("score")
                        method_map[segment_id] = candidate.get("retrieval_method")
                    refreshed.extend(
                        await knowledge_title_service.resolve_segment_references(
                            db,
                            knowledge_base_id,
                            segment_ids,
                            score_by_segment_id=score_map,
                            retrieval_method_by_segment_id=method_map,
                        )
                    )
        except Exception as exc:
            logger.warning(f"重新校验知识引用失败: {exc}")
            return []

        refreshed_by_id = {
            reference["reference_id"]: reference for reference in refreshed
        }
        return [
            refreshed_by_id[reference["reference_id"]]
            for reference in candidates
            if reference["reference_id"] in refreshed_by_id
        ]

    async def _run_react_loop(
        self, ctx: ReactLoopContext
    ) -> tuple[str, list[str], set[str]]:
        """ReAct 循环：流式调用 LLM → 处理工具调用 → 继续调用

        核心编排逻辑，调用 llm_stream 模块进行流式调用，
        调用 llm_tool_executor 模块处理工具调用。

        Args:
            ctx: 循环上下文（LLM 实例、提示词、消息缓冲、工具、执行环境、配置）

        Returns:
            (最后一条文本内容, 所有 thinking 片段, 本轮调用的工具名集合)
        """
        llm = ctx.llm
        system_prompt = ctx.system_prompt
        mode_reminder = ctx.mode_reminder
        msg_buf = ctx.msg_buf
        node = ctx.node
        state = ctx.state
        writer = ctx.writer
        context_length = ctx.context_length
        required_tools = ctx.required_tools
        tool_check_script = ctx.tool_check_script
        required_tools_max_retries = ctx.required_tools_max_retries
        required_tools_hint = ctx.required_tools_hint
        initial_knowledge_references = ctx.initial_knowledge_references
        allowed_knowledge_base_ids = ctx.allowed_knowledge_base_ids

        # JSON 结构化输出：共享服务实例（execute() 构建）+ 检查清单（自动追加）
        structured_service = ctx.structured_service
        json_output_enabled = structured_service is not None
        checklist = list(required_tools or [])
        if json_output_enabled:
            checklist.append(OUTPUT_TOOL_NAME)

        thinking_content: list[str] = []
        last_content = ""
        tool_call_count = 0
        called_tools = ctx.called_tools if ctx.called_tools is not None else set()
        retry_count = 0
        react_compress_attempts = 0
        tool_fp_count: dict[str, int] = {}
        knowledge_references = await self._revalidate_knowledge_references(
            merge_knowledge_references(
                initial_knowledge_references or [],
                collect_message_knowledge_references(msg_buf.messages),
            ),
            allowed_knowledge_base_ids or set(),
        )

        async def compress_react_tail(active_tail_start: int) -> bool:
            nonlocal react_compress_attempts
            if react_compress_attempts >= _MAX_REACT_COMPRESSIONS:
                error = "本次执行上下文压缩次数已达上限，已停止继续调用工具"
                state.add_error(node.node_key, error)
                self._emit(writer, ErrorEvent(node_key=node.node_key, message=error))
                return False

            react_compress_attempts += 1
            preserved_count = len(msg_buf.messages) - active_tail_start
            compressed = await msg_buf.maybe_compress(
                context_length,
                node.base_config or {},
                writer,
                preserve_tail_count=preserved_count,
            )
            if not compressed:
                error = "上下文压缩失败，已停止 ReAct 以避免超出模型上下文窗口"
                state.add_error(node.node_key, error)
                self._emit(writer, ErrorEvent(node_key=node.node_key, message=error))
                return False

            state.set_conversation_messages(node.node_key, list(msg_buf.messages))
            return True

        while True:
            active_tail_start = len(msg_buf.messages)
            needs_context_compression = False
            messages = msg_buf.messages
            # system_prompt 不存入 messages/checkpoint，每次调用时临时拼接；
            # 无 system_prompt 时也必须复制列表，避免下方 reminder 注入污染 msg_buf
            call_messages = (
                [SystemMessage(content=system_prompt), *messages]
                if system_prompt
                else list(messages)
            )
            # 模式提醒注入消息层（不入 checkpoint/DB，save_to_db 只落 msg_buf.messages）：
            # 插到最后一条 HumanMessage 之前紧邻当前用户输入，注意力权重最高；
            # 多轮工具迭代中插入位置稳定，保持 LLM 前缀缓存命中
            if mode_reminder:
                insert_idx = len(call_messages)
                for i in range(len(call_messages) - 1, -1, -1):
                    if isinstance(call_messages[i], HumanMessage):
                        insert_idx = i
                        break
                call_messages.insert(insert_idx, HumanMessage(content=mode_reminder))

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

            if response and not response.tool_calls:
                citations = validate_knowledge_citations(
                    current_content, knowledge_references
                )
                response.additional_kwargs[KNOWLEDGE_CITATIONS_KEY] = citations
                state.set_node_variable(node.node_key, "knowledge_citations", citations)
                self._emit_knowledge_citations(writer, node.node_key, citations)

            # 推送 token 用量事件 + 持久化到 token_usage 表
            if response:
                usage = extract_token_usage(response)
                if usage.get("total_tokens"):
                    node_config = node.base_config or {}
                    model_name = node_config.get("model", "")
                    provider_name = node_config.get("provider", "")
                    self._emit(
                        writer,
                        TokenUsageEvent(
                            node_key=node.node_key,
                            prompt_tokens=usage.get("prompt_tokens", 0),
                            completion_tokens=usage.get("completion_tokens", 0),
                            total_tokens=usage.get("total_tokens", 0),
                            model=model_name,
                            provider=provider_name,
                            cache_read_tokens=usage.get("cache_read_tokens", 0),
                            cache_write_tokens=usage.get("cache_write_tokens", 0),
                            reasoning_tokens=usage.get("reasoning_tokens", 0),
                        ),
                    )
                    # 异步写入 token_usage 表（不阻塞主流程）
                    if self.db_session_factory:
                        try:
                            from app.services.token_usage_service import (
                                token_usage_service,
                            )

                            async with self.db_session_factory() as tdb:
                                await token_usage_service.record_usage(
                                    tdb,
                                    source_type="agent" if self.session_id else "flow",
                                    source_id=self.session_id or self.execution_id,
                                    node_key=node.node_key,
                                    model=model_name,
                                    provider=provider_name,
                                    prompt_tokens=usage.get("prompt_tokens", 0),
                                    completion_tokens=usage.get("completion_tokens", 0),
                                    total_tokens=usage.get("total_tokens", 0),
                                    cache_read_tokens=usage.get("cache_read_tokens", 0),
                                    cache_write_tokens=usage.get(
                                        "cache_write_tokens", 0
                                    ),
                                    reasoning_tokens=usage.get("reasoning_tokens", 0),
                                    usage_metadata=usage.get("usage_metadata"),
                                )
                        except Exception as e:
                            logger.warning(f"记录 token_usage 失败: {e}")

                    needs_context_compression = context_length > 0 and usage.get(
                        "prompt_tokens", 0
                    ) > int(context_length * COMPRESS_THRESHOLD_RATIO)

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
                    if needs_context_compression:
                        await msg_buf.maybe_compress(
                            context_length, node.base_config or {}, writer
                        )
                    break

            # 将完整 AI 消息追加到历史
            if response:
                msg_buf.append(response)
            if current_content:
                last_content = current_content
            thinking_content.extend(current_thinking)

            # 收集本轮调用的工具名（仅当前 ReAct 循环内新增调用，不查历史/DB）
            tool_calls = response.tool_calls if response else []
            for tc in tool_calls:
                name = _tool_call_name(tc)
                if name:
                    called_tools.add(name)

            # 无工具调用时检查必需工具清单：未调用则注入提醒消息重试
            if not response or not response.tool_calls:
                if (
                    (tool_check_script or checklist)
                    and not self._check_interrupted(state)
                    and retry_count < required_tools_max_retries
                ):
                    need_retry, hint = await self._evaluate_required_tools(
                        checklist,
                        tool_check_script,
                        called_tools,
                        last_content,
                        required_tools_hint,
                    )
                    if (
                        not need_retry
                        and json_output_enabled
                        and not structured_service.accepted
                    ):
                        # 脚本模式通过但结构化输出未交付（脚本看不到 structured_output）
                        need_retry = True
                        hint = (
                            f"请调用 {OUTPUT_TOOL_NAME} 工具，"
                            "以符合字段定义的 JSON 参数输出最终结果，不要用文字回复。"
                        )
                    if need_retry and hint:
                        retry_count += 1
                        if needs_context_compression and not await compress_react_tail(
                            active_tail_start
                        ):
                            break
                        msg_buf.append(HumanMessage(content=hint))
                        continue
                if needs_context_compression:
                    await msg_buf.maybe_compress(
                        context_length, node.base_config or {}, writer
                    )
                break

            # 处理工具调用（structured_output 与普通工具同链路：事件展示/截断/重试）
            message_count_before_tools = len(msg_buf.messages)
            should_continue, tool_call_count = await handle_tool_calls(
                ctx,
                tool_calls,
                tool_call_count,
                tool_fp_count=tool_fp_count,
            )
            knowledge_references = merge_knowledge_references(
                knowledge_references,
                collect_message_knowledge_references(
                    msg_buf.messages[message_count_before_tools:]
                ),
            )
            if not should_continue:
                break

            # 结构化输出已被接受：任务完成，终止循环
            if json_output_enabled and structured_service.accepted:
                break

            if needs_context_compression and not await compress_react_tail(
                active_tail_start
            ):
                break

        return last_content, thinking_content, called_tools

    async def _evaluate_required_tools(
        self,
        required_tools: list[str],
        tool_check_script: str,
        called_tools: set[str],
        last_result: str,
        hint_template: str,
    ) -> tuple[bool, str]:
        """评估必需工具是否已调用，返回 (是否需要重试, 提醒消息)

        高级模式（tool_check_script 非空）：在 RestrictedPython 沙箱中执行
        自定义脚本，签名 def main(called_tools, last_result): return
        {"need_retry": bool, "hint": str}。
        简单模式：检查 called_tools 是否包含所有 required_tools，缺失则用
        hint_template（{{tools}} 占位）生成提醒。

        Args:
            required_tools: 必需工具名列表（简单模式）
            tool_check_script: 自定义检查脚本（高级模式，留空走简单模式）
            called_tools: 本轮已调用的工具名集合
            last_result: LLM 最后输出的文本内容
            hint_template: 提醒消息模板（简单模式用，{{tools}} 占位）
        """
        # 高级模式：执行自定义检查脚本（复用 Python 节点的沙箱）
        if tool_check_script:
            try:
                from app.agent_flow.node_handlers.python_handler import (
                    PythonNodeHandler,
                )

                handler: PythonNodeHandler = PythonNodeHandler()
                result = await handler._execute_python(  # type: ignore
                    tool_check_script,
                    {
                        "called_tools": list(called_tools),
                        "last_result": last_result,
                    },
                    timeout=30,
                )
                if result.get("success"):
                    ret = result.get("result")
                    if isinstance(ret, dict):
                        need_retry = bool(ret.get("need_retry", False))
                        hint = str(ret.get("hint", "") or "")
                        return need_retry, hint
            except Exception as e:
                logger.warning(f"必需工具检查脚本执行失败: {e}")
            return False, ""

        # 简单模式：工具名精确匹配
        missing = [t for t in required_tools if t not in called_tools]
        if not missing:
            return False, ""

        if hint_template:
            hint = hint_template.replace("{{tools}}", "、".join(missing))
        else:
            hint = (
                f"你尚未调用必需的工具：{'、'.join(missing)}。"
                "请根据任务需要调用上述工具完成操作，不要直接给出最终回复。"
            )
        return True, hint

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
            name = var.get("name", "") if isinstance(var, dict) else var.name
            if name:
                value = state.get_node_variable(node.node_key, name)
                if value is not None:
                    output[name] = value

        return output if output else None


# LLM 节点处理器工厂函数
@NodeHandlerRegistry.register_factory("llm")
def create_llm_handler(
    flow: Optional["FlowLike"],
    db_session_factory: Optional[Callable[[], AsyncSession]],
    execution_id: int,
    conversation_service: Optional[
        Union[ConversationService, AgentConversationService]
    ],
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

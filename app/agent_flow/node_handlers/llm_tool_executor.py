"""
LLM 工具执行模块

从 llm_tool_handler.py 抽离的工具执行职责，负责：
- 工具处理器初始化（收集工具定义 + system_prompt 提示）
- 工具调用处理（并行执行、人工交互、审批确认、次数限制）
- 统一截断（通过 tool_output_truncate 模块）
"""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import ToolCall, ToolMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.types import StreamWriter, interrupt

from app.agent_flow.flow_change_tracker import consume_changes_since
from app.agent_flow.flow_context import FlowState
from app.agent_flow.flow_event import (
    ToolApprovalEvent,
    ToolCallLimitEvent,
)
from app.agent_flow.message_buffer import MessageBuffer
from app.agent_flow.tool_output_truncate import smart_truncate_output
from app.agent_flow.tool_resolver import (
    FlowLike,
    filter_tools_by_intent,
    get_connected_tool_edges,
)
from app.agent_flow.tools.structured_output import StructuredOutputService
from app.config.build_utils import get_agent_work_dir
from app.models.flow import FlowType
from app.models.flow_node import FlowNode
from app.utils.knowledge_reference import (
    KNOWLEDGE_REFERENCES_KEY,
    filter_references_in_content,
    unpack_knowledge_result,
)

if TYPE_CHECKING:
    from app.agent_flow.node_handlers.llm_tool_handler import LlmNodeConfig

logger = logging.getLogger(__name__)

# 人工协助工具名（LLM 通过此工具名触发 interrupt）
_REQUEST_HUMAN_HELP = "request_human_help"

# 计划模式下禁用的工具名前缀（写操作 / 有副作用；python_executor 与 call_sub_agent_
# 的实际注册名带动态后缀 _{node_key}，统一按前缀匹配）
_PLAN_DISABLED_TOOL_NAMES = (
    "python_executor",
    "file_write",
    "text_editor",
    "call_sub_agent_",
)


def _is_plan_disabled_tool(tool_name: str) -> bool:
    """判断工具是否为计划模式禁用工具（前缀匹配，覆盖动态名后缀）"""
    return any(tool_name.startswith(prefix) for prefix in _PLAN_DISABLED_TOOL_NAMES)


# system_prompt hint 优先级：值越小越靠前，静态内容放前面有利于 LLM 缓存命中
_HINT_PRIORITY: dict[str, int] = {
    "todo": 0,
    "human": 0,
    "knowledge": 1,
    "shell": 1,
    "agenda": 1,
    "ssh": 1,
    "memory": 2,
}

# doom loop 检测：相同工具+相同参数重复调用次数上限，超过则跳过执行
_DOOM_LOOP_THRESHOLD = 3


@dataclass
class ReactLoopContext:
    """ReAct 循环与工具执行共享的上下文参数

    由 LlmToolNodeHandler 在一次节点执行内构建后传入 _run_react_loop 与
    handle_tool_calls，替代原先十余个散落参数；对象在单次节点执行内不变，
    tool_fp_count 等跨轮可变态仍由调用方持有。
    """

    # LLM 调用
    llm: BaseChatModel | Runnable
    system_prompt: Optional[str]
    mode_reminder: Optional[str]
    # 消息与工具
    msg_buf: MessageBuffer
    tools: list[BaseTool]
    # 执行环境
    node: FlowNode
    state: FlowState
    writer: Optional[StreamWriter]
    session_id: int
    check_interrupted_fn: Callable[[FlowState], bool]
    # 事件回调
    emit_fn: Optional[Callable] = None
    emit_tool_start_fn: Optional[Callable] = None
    emit_tool_end_fn: Optional[Callable] = None
    emit_flow_preview_fn: Optional[Callable] = None
    # 行为配置
    max_tool_iterations: int = 10
    context_length: int = 0
    approval_required_tools: Optional[list[str]] = None
    doom_loop_threshold: int = _DOOM_LOOP_THRESHOLD
    # 必需工具校验
    required_tools: Optional[list[str]] = None
    tool_check_script: str = ""
    required_tools_max_retries: int = 2
    required_tools_hint: str = ""
    # JSON 结构化输出（structured_output 普通工具，由 execute() 构建并注入 tools）
    structured_service: Optional["StructuredOutputService"] = None
    # 共享已调用工具集合（execute() 创建，结构化工具清单门控读取）
    called_tools: Optional[set] = None
    # 知识引用
    initial_knowledge_references: Optional[list[dict]] = None
    allowed_knowledge_base_ids: Optional[set[int]] = None


def _tool_call_fingerprint(tool_call: ToolCall) -> str:
    """生成工具调用指纹（工具名 + 参数哈希），用于 doom loop 重复检测"""
    name = tool_call.get("name", "")
    args = tool_call.get("args", "")
    if isinstance(args, dict):
        args_key = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    else:
        args_key = str(args)
    return f"{name}:{hashlib.md5(args_key.encode('utf-8')).hexdigest()}"


async def _resolve_session_work_dir(
    session_id: int,
    db_session_factory: Optional[Callable[[], AsyncSession]],
) -> Optional[Path]:
    """读取会话级项目工作路径（AgentSession.work_dir，用户在聊天页选择）

    目录已被删除/不可访问时回退 None（由调用方使用 Agent 默认工作目录）并告警。
    """
    if session_id <= 0 or not db_session_factory:
        return None
    from app.models.agent_session import AgentSession

    try:
        async with db_session_factory() as db:
            session = await db.get(AgentSession, session_id)
    except Exception as e:
        logger.warning("读取会话工作路径失败 session_id=%s: %s", session_id, e)
        return None
    if not session or not session.work_dir:
        return None
    work_dir = Path(session.work_dir)
    if not work_dir.is_dir():
        logger.warning(
            "会话工作路径不存在，回退 Agent 默认工作目录: %s (session_id=%s)",
            session.work_dir,
            session_id,
        )
        return None
    return work_dir


async def setup_tool_handlers(
    node: FlowNode,
    state: FlowState,
    writer: Optional[StreamWriter],
    config: Optional[RunnableConfig],
    cfg: "LlmNodeConfig",
    *,
    flow: Optional[FlowLike],
    db_session_factory: Optional[Callable[[], AsyncSession]],
    handler_registry: dict,
    emit_fn: Optional[Callable] = None,
    session_id: int = 0,
) -> tuple[list[BaseTool], list[str], list[str]]:
    """单次遍历工具节点，完成三件事：

    1. 注入处理器依赖（_agent_id, _writer, _resolve_context, _llm_config）
    2. 收集工具定义
    3. 收集 system_prompt 提示片段（按优先级排序，静态内容靠前以利于 LLM 缓存命中）

    Args:
        node: LLM 节点
        state: 流程状态
        writer: SSE 流式写入器
        config: RunnableConfig
        cfg: LLM 节点配置
        flow: 流程对象
        db_session_factory: 数据库会话工厂
        handler_registry: 工具处理器注册表
        emit_fn: 事件发送回调
        session_id: 会话 ID（Agent 模式，用于读取会话级项目工作路径）

    Returns:
        (工具列表, prompt 提示片段列表, 运行时提醒片段列表)
        第三项来自各工具 handler 的 get_runtime_reminder（动态内容），
        供消息层 <system-reminder> 拼装，按 handler 实例去重
    """
    tools: list[BaseTool] = []
    prompt_hints: list[tuple[int, int, str]] = []
    runtime_reminders: list[str] = []

    if not flow or not db_session_factory:
        return tools, [h for _, _, h in prompt_hints], runtime_reminders

    # 获取工具节点 + 边对，按意图条件过滤
    tool_edge_pairs = get_connected_tool_edges(flow, node.node_key)
    tool_edge_pairs = filter_tools_by_intent(tool_edge_pairs, state)

    # 会话级项目工作路径（仅 Agent 模式，用户在聊天页选择；空则用 Agent 默认目录）
    session_work_dir = await _resolve_session_work_dir(session_id, db_session_factory)

    # LLM 配置注入到工具处理器
    llm_config = {
        "model": cfg.model,
        "api_key": cfg.api_key,
        "base_url": cfg.base_url,
        "provider": cfg.provider,
        "context_length": cfg.context_length,
    }
    flow_id = getattr(flow, "id", None)
    flow_type = getattr(flow, "flow_type", "")
    # 已收集运行时提醒的 handler 实例（registry 为单例，同一 handler 服务多个
    # 工具节点时只收集一次）
    reminder_collected_handlers: set[int] = set()
    for idx, (tool_node, _edge) in enumerate(tool_edge_pairs):
        handler = handler_registry.get(tool_node.node_type)
        if not handler:
            continue

        # 注入 _agent_id（记忆节点等需要知道当前 Agent 的 ID）
        if hasattr(handler, "_agent_id") and flow_id is not None:
            handler._agent_id = flow_id

        # 注入 _working_dir（仅 Agent 类型，Shell 节点用作 cwd）
        # 优先级：会话级项目工作路径 > Agent 默认工作目录
        if (
            hasattr(handler, "_working_dir")
            and flow_type == "agent"
            and flow_id is not None
        ):
            handler._working_dir = session_work_dir or get_agent_work_dir(flow_id)

        # 注入 _media_caps（file_read 可自动注入的媒体类型，模型能力×适配器交集）
        if hasattr(handler, "_media_caps"):
            from app.services.ai_provider_service import get_adapter_type
            from app.utils.media_resolver import get_injectable_media_types

            adapter = (
                get_adapter_type(cfg.provider) if cfg.provider else "openai_compatible"
            )
            capabilities = (node.base_config or {}).get("capabilities") or {}
            handler._media_caps = get_injectable_media_types(capabilities, adapter)

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

        # 收集运行时提醒（动态内容，按 handler 实例去重，注入前依赖已在循环内完成）
        if id(handler) not in reminder_collected_handlers:
            reminder_collected_handlers.add(id(handler))
            reminder = await handler.get_runtime_reminder(tool_node)
            if reminder:
                runtime_reminders.append(reminder)

    # 按优先级排序：静态内容靠前，动态内容（如记忆）靠后，利于 LLM 缓存命中
    prompt_hints.sort(key=lambda x: (x[0], x[1]))

    # 注入 WS 远程工具（仅智能体类型，远程工具仅 Agent 支持）
    # contextvar（WS execute 路径）优先；前端 SSE 路径 fallback 全局注册表
    if flow_type == FlowType.AGENT.value and flow_id is not None:
        from app.agent_flow.ws_tool_context import (
            _current_ws_conn,
            get_active_ws_conn,
        )

        ws_conn = _current_ws_conn.get() or get_active_ws_conn(flow_id)
        if ws_conn and ws_conn.registered_tools:
            from app.agent_flow.remote_tool_builder import create_remote_tool

            for tool_def in ws_conn.registered_tools:
                if isinstance(tool_def, dict) and tool_def.get("name"):
                    tools.append(create_remote_tool(tool_def, ws_conn))

    # 去重：同名工具只保留第一个（子 Agent 等节点可能产生重复的通用工具）
    seen_names = set()
    unique_tools = []
    for tool in tools:
        if tool.name not in seen_names:
            seen_names.add(tool.name)
            unique_tools.append(tool)

    return unique_tools, [h for _, _, h in prompt_hints], runtime_reminders


async def handle_tool_calls(
    ctx: ReactLoopContext,
    tool_calls: list[ToolCall],
    tool_call_count: int,
    *,
    tool_fp_count: Optional[dict[str, int]] = None,
) -> tuple[bool, int]:
    """统一处理所有工具调用（计划模式拦截 + 人工协助 + 审批确认 + 并行执行 + 截断）

    不同 MCP 服务器的工具调用并行执行（per-server 锁保证安全），
    非 MCP 工具也并行执行。人工介入工具单独处理。
    计划模式下对禁用工具（含 call_sub_agent_* 子Agent 委派）硬拦截并反馈引导。

    Args:
        ctx: ReAct 循环上下文（消息缓冲、工具列表、执行环境、事件回调、行为配置）
        tool_calls: LLM 返回的工具调用列表
        tool_call_count: 当前已执行的工具调用次数
        tool_fp_count: doom loop 检测的指纹计数字典（由调用方持有，跨轮累积）

    Returns:
        (是否应继续循环, 工具调用总次数)
    """
    tools = ctx.tools
    msg_buf = ctx.msg_buf
    node = ctx.node
    state = ctx.state
    writer = ctx.writer
    session_id = ctx.session_id
    check_interrupted_fn = ctx.check_interrupted_fn
    emit_fn = ctx.emit_fn
    emit_tool_start_fn = ctx.emit_tool_start_fn
    emit_tool_end_fn = ctx.emit_tool_end_fn
    emit_flow_preview_fn = ctx.emit_flow_preview_fn
    approval_required_tools = ctx.approval_required_tools
    max_tool_iterations = ctx.max_tool_iterations
    doom_loop_threshold = ctx.doom_loop_threshold

    # 个别 OpenAI 兼容端点不返回 tool_call id，统一生成兜底 id
    # 写回原 dict（与 AIMessage.tool_calls 中条目同引用），保证 ToolMessage 可配对
    for tc in tool_calls:
        if not tc.get("id"):
            tc["id"] = f"call_{uuid.uuid4().hex[:24]}"

    # ---- 计划模式硬拦截：禁用工具虽不注入工具列表，但模型仍可能受历史消息误导而调用
    # （如上一轮普通模式的工具调用记录），此处拒绝执行并反馈，引导切换普通模式 ----
    if state.get_variable("plan_mode"):
        disabled_calls = [
            tc for tc in tool_calls if _is_plan_disabled_tool(tc.get("name", ""))
        ]
        if disabled_calls:
            reject_remaining_tools(
                disabled_calls,
                msg_buf,
                node.node_key,
                writer,
                "该工具在计划模式下被禁用（写操作/有副作用/子Agent 委派），"
                "请切换到普通执行模式后再调用，或改用只读方式完成当前步骤",
                emit_fn=emit_fn,
                emit_tool_end_fn=emit_tool_end_fn,
            )
            tool_calls = [
                tc
                for tc in tool_calls
                if not _is_plan_disabled_tool(tc.get("name", ""))
            ]
            if not tool_calls:
                return True, tool_call_count

    # ---- 人工协助工具：跳过所有其他工具（避免有副作用的工具先执行） ----
    human_help_idx = next(
        (i for i, tc in enumerate(tool_calls) if tc.get("name") == _REQUEST_HUMAN_HELP),
        -1,
    )
    if human_help_idx >= 0:
        skip_msg = "人工介入，跳过其他工具调用"
        before = tool_calls[:human_help_idx]
        if before:
            reject_remaining_tools(
                before,
                msg_buf,
                node.node_key,
                writer,
                skip_msg,
                emit_fn=emit_fn,
                emit_tool_end_fn=emit_tool_end_fn,
            )
        after = tool_calls[human_help_idx + 1 :]
        if after:
            reject_remaining_tools(
                after,
                msg_buf,
                node.node_key,
                writer,
                skip_msg,
                emit_fn=emit_fn,
                emit_tool_end_fn=emit_tool_end_fn,
            )

        tool_call = tool_calls[human_help_idx]
        tool_name = tool_call.get("name", "")
        tool_args = tool_call.get("args", {})
        tool_id = tool_call.get("id", "")
        if not tool_id:
            raise Exception("人工协助工具调用缺少 id")
        tool_call_count += 1

        if emit_tool_start_fn:
            emit_tool_start_fn(
                writer, node.node_key, tool_name, tool_args, tool_call_id=tool_id
            )
        result = await handle_human_interaction(
            tool_args, tool_id, msg_buf.messages, node, state
        )

        # 截断人工交互结果
        content = smart_truncate_output(result, prefix="human_output")
        msg_buf.append(
            ToolMessage(content=content, tool_call_id=tool_id, name=tool_name)
        )

        if emit_tool_end_fn:
            emit_tool_end_fn(
                writer,
                node.node_key,
                tool_name,
                result,
                status="success",
                tool_call_id=tool_id,
            )
        return True, tool_call_count

    # ---- 工具确认（仅 Agent 模式） ----
    if session_id > 0:
        if approval_required_tools:
            configured_approval_tools = set(approval_required_tools)
            approval_names = list(
                dict.fromkeys(
                    tc.get("name", "")
                    for tc in tool_calls
                    if tc.get("name", "") in configured_approval_tools
                )
            )
            if approval_names:
                from app.services.tool_approval_service import (
                    tool_approval_service,
                )

                # 注册等待句柄并通过 SSE 通知前端
                future = tool_approval_service.register(
                    session_id, tool_calls, approval_names
                )
                if emit_fn:
                    emit_fn(
                        writer,
                        ToolApprovalEvent(
                            node_key=node.node_key,
                            tool_calls=tool_calls,
                            approval_needed=approval_names,
                        ),
                    )

                # 等待前端确认（5分钟超时，SSE 流保持连接）
                try:
                    await asyncio.wait_for(future.event.wait(), timeout=300)
                except asyncio.TimeoutError:
                    tool_approval_service.remove(session_id)
                    state.set_interrupted()
                    for tc in tool_calls:
                        tc_name = tc.get("name", "")
                        tc_id = tc.get("id", "")
                        msg = "工具确认超时（5分钟未响应），自动取消执行"
                        msg_buf.append(
                            ToolMessage(content=msg, tool_call_id=tc_id, name=tc_name)
                        )
                        if emit_tool_end_fn:
                            emit_tool_end_fn(
                                writer,
                                node.node_key,
                                tc_name,
                                msg,
                                status="error",
                                tool_call_id=tc_id,
                            )
                    return False, tool_call_count

                tool_approval_service.remove(session_id)
                if future.result == "rejected":
                    state.set_interrupted()
                    for tc in tool_calls:
                        tc_name = tc.get("name", "")
                        tc_id = tc.get("id", "")
                        msg = "用户拒绝执行"
                        msg_buf.append(
                            ToolMessage(content=msg, tool_call_id=tc_id, name=tc_name)
                        )
                        if emit_tool_end_fn:
                            emit_tool_end_fn(
                                writer,
                                node.node_key,
                                tc_name,
                                msg,
                                status="error",
                                tool_call_id=tc_id,
                            )
                    return False, tool_call_count

    # ---- 检查工具调用次数是否超限（整批检查） ----
    if tool_call_count + len(tool_calls) > max_tool_iterations:
        over_idx = max_tool_iterations - tool_call_count
        if over_idx < 0:
            over_idx = 0
        if over_idx < len(tool_calls):
            limit_msg = f"超过最大工具调用次数: {max_tool_iterations}"
            reject_remaining_tools(
                tool_calls[over_idx:],
                msg_buf,
                node.node_key,
                writer,
                limit_msg,
                emit_fn=emit_fn,
                emit_tool_end_fn=emit_tool_end_fn,
            )
            if emit_fn:
                emit_fn(
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

    # ---- doom loop 检测：相同工具+相同参数重复调用超过阈值则跳过 ----
    if tool_fp_count is not None and doom_loop_threshold > 0:
        safe_calls: list[ToolCall] = []
        for tc in tool_calls:
            fp = _tool_call_fingerprint(tc)
            tool_fp_count[fp] = tool_fp_count.get(fp, 0) + 1
            if tool_fp_count[fp] > doom_loop_threshold:
                tc_name = tc.get("name", "")
                tc_id = tc.get("id", "")
                skip_msg = (
                    f"检测到重复执行（{tc_name} 相同参数已第 {tool_fp_count[fp]} 次调用），"
                    "已自动跳过。请回顾上一次执行结果，避免重复操作，更换思路或确认任务是否已完成。"
                )
                msg_buf.append(
                    ToolMessage(content=skip_msg, tool_call_id=tc_id, name=tc_name)
                )
                if emit_tool_end_fn:
                    emit_tool_end_fn(
                        writer,
                        node.node_key,
                        tc_name,
                        skip_msg,
                        status="error",
                        tool_call_id=tc_id,
                    )
            else:
                safe_calls.append(tc)
        tool_calls = safe_calls
        if not tool_calls:
            return True, tool_call_count

    # ---- 并行执行工具调用 ----
    tool_call_count += len(tool_calls)
    batch_start_time = time.time()

    async def _run_single_tool(
        tool_call: ToolCall,
    ) -> tuple[ToolCall, Any]:
        """执行单个工具调用并返回 (tool_call, result)"""
        tool_name = tool_call.get("name", "")
        tool_args = tool_call.get("args", "")
        tool_id = tool_call.get("id", "")
        if check_interrupted_fn(state):
            return tool_call, {"success": False, "error": "执行被中断"}
        if emit_tool_start_fn:
            emit_tool_start_fn(
                writer, node.node_key, tool_name, tool_args, tool_call_id=tool_id
            )
        result = await execute_tool(
            tool_name,
            tool_args,
            tools,
            state,
            check_interrupted_fn=check_interrupted_fn,
        )
        return tool_call, result

    # Sub-Agent resume 模式复用同一 session，不能同时执行多个调用；
    # new 模式及不同工具仍保持并行。
    tool_map = {tool.name: tool for tool in tools}
    serial_groups: dict[str, list[int]] = {}
    parallel_indices: list[int] = []
    for index, tool_call in enumerate(tool_calls):
        tool = tool_map.get(tool_call.get("name", ""))
        metadata = getattr(tool, "metadata", None) or {}
        tool_args = tool_call.get("args", {})
        session_mode = (
            tool_args.get("session_mode", "resume")
            if isinstance(tool_args, dict)
            else "resume"
        )
        if metadata.get("sub_agent") and session_mode == "resume":
            serial_groups.setdefault(tool_call.get("name", ""), []).append(index)
        else:
            parallel_indices.append(index)

    results: list[Any] = [None] * len(tool_calls)

    async def _store_result(index: int) -> None:
        try:
            results[index] = await _run_single_tool(tool_calls[index])
        except BaseException as exc:
            results[index] = exc

    async def _run_parallel() -> None:
        await asyncio.gather(*(_store_result(index) for index in parallel_indices))

    async def _run_serial(indices: list[int]) -> None:
        for index in indices:
            await _store_result(index)

    await asyncio.gather(
        _run_parallel(),
        *(_run_serial(indices) for indices in serial_groups.values()),
    )
    # file_read 媒体注入成功项收集：本轮所有 ToolMessage 落地后统一注入多模态 HumanMessage
    # （HumanMessage 必须在全部 tool_result 之后，避免破坏 tool_call 配对约束）
    pending_media_sources: list[str] = []

    for item in results:
        if isinstance(item, BaseException):
            if isinstance(item, asyncio.CancelledError):
                raise item
            tool_call = {}
            raw_result = {
                "success": False,
                "error": f"工具执行异常: {str(item)}",
            }
        else:
            tool_call, raw_result = item

        tool_name = tool_call.get("name", "")
        tool_id = tool_call.get("id", "")

        # 判断工具执行状态
        tool_status = "error"
        if not isinstance(raw_result, Exception):
            try:
                parsed = (
                    json.loads(raw_result)
                    if isinstance(raw_result, str)
                    else raw_result
                )
                if not (isinstance(parsed, dict) and parsed.get("success") is False):
                    tool_status = "success"
            except (json.JSONDecodeError, TypeError):
                tool_status = "success"

        # file_read 媒体注入成功 → 记录待注入的媒体路径（解析后的绝对路径）
        if (
            tool_name == "file_read"
            and tool_status == "success"
            and isinstance(raw_result, dict)
            and raw_result.get("media_type")
            and raw_result.get("file_path")
        ):
            pending_media_sources.append(str(raw_result["file_path"]))

        # 知识正文参与截断，引用元数据独立保存在 artifact 中。
        tool = tool_map.get(tool_name)
        tool_metadata = getattr(tool, "metadata", None) or {}
        knowledge_result = (
            unpack_knowledge_result(raw_result)
            if tool_metadata.get("knowledge_tool")
            else None
        )
        is_exempt = tool_name == "load_skill"
        artifact = None
        if knowledge_result is not None:
            knowledge_content, knowledge_references = knowledge_result
            content = smart_truncate_output(knowledge_content, prefix=tool_name)
            visible_references = filter_references_in_content(
                content, knowledge_references
            )
            artifact = {KNOWLEDGE_REFERENCES_KEY: visible_references}
        elif is_exempt:
            content = (
                raw_result
                if isinstance(raw_result, str)
                else json.dumps(raw_result, ensure_ascii=False, default=str)
            )
        else:
            content = smart_truncate_output(raw_result, prefix=tool_name)
        msg_buf.append(
            ToolMessage(
                content=content,
                tool_call_id=tool_id,
                name=tool_name,
                artifact=artifact,
            )
        )

        if emit_tool_end_fn:
            sse_result = (
                raw_result if is_exempt and knowledge_result is None else content
            )
            emit_tool_end_fn(
                writer,
                node.node_key,
                tool_name,
                sse_result,
                tool_status,
                tool_call_id=tool_id,
            )

    # ---- file_read 媒体注入：构建多模态 HumanMessage（在全部 ToolMessage 之后） ----
    if pending_media_sources:
        from app.utils.media_resolver import build_media_human_message

        try:
            msg_buf.append(await build_media_human_message(pending_media_sources))
        except Exception as e:
            logger.warning(f"file_read 媒体注入失败: {e}")

    # ---- 检测流程变更并发送预览事件 ----
    if emit_flow_preview_fn and writer:
        changes = consume_changes_since(batch_start_time)
        if changes:
            # 按 flow_id 去重，保留最新 action
            seen: dict[int, str] = {}
            for c in changes:
                seen[c.flow_id] = c.action
            for fid, action in seen.items():
                try:
                    await emit_flow_preview_fn(writer, fid, action)
                except Exception as e:
                    logger.warning(f"发送流程预览事件失败 flow_id={fid}: {e}")

    return True, tool_call_count


def reject_remaining_tools(
    remaining_calls: list[ToolCall],
    msg_buf: MessageBuffer,
    node_key: str,
    writer: Optional[StreamWriter],
    reason: str,
    *,
    emit_fn: Optional[Callable] = None,
    emit_tool_end_fn: Optional[Callable] = None,
) -> None:
    """拒绝剩余的工具调用（发送失败事件 + ToolMessage）

    用于工具调用超限、人工介入跳过等场景。

    Args:
        remaining_calls: 被拒绝的工具调用列表
        msg_buf: 消息缓冲区
        node_key: 节点 key
        writer: SSE 流式写入器
        reason: 拒绝原因
        emit_fn: 事件发送回调
        emit_tool_end_fn: 工具结束事件发送回调
    """
    for call in remaining_calls:
        call_id = call.get("id", "")
        call_name = call.get("name", "")
        if emit_tool_end_fn:
            emit_tool_end_fn(
                writer,
                node_key,
                call_name,
                {"success": False, "error": reason},
                status="error",
                tool_call_id=call_id,
            )
        msg_buf.append(
            ToolMessage(content=reason, tool_call_id=call_id, name=call_name)
        )


async def execute_tool(
    tool_name: str,
    tool_args: dict,
    tools: list[BaseTool],
    state: FlowState,
    *,
    check_interrupted_fn: Callable[[FlowState], bool],
) -> Any:
    """按名称查找并执行工具

    Args:
        tool_name: 工具名称
        tool_args: 工具参数
        tools: 可用工具列表
        state: 流程状态
        check_interrupted_fn: 中断检查回调

    Returns:
        工具执行结果
    """
    if check_interrupted_fn(state):
        return {"success": False, "error": "执行被中断"}
    for tool in tools:
        if tool.name == tool_name:
            try:
                return await tool.ainvoke(tool_args)
            except Exception as e:
                return {"success": False, "error": f"工具执行错误: {str(e)}"}
    return {"success": False, "error": f"未找到工具: {tool_name}"}


async def handle_human_interaction(
    tool_args: dict,
    tool_id: str,
    messages: list,
    node: FlowNode,
    state: FlowState,
) -> str:
    """处理人工协助工具调用

    流程：触发 LangGraph interrupt → 暂停执行 → 等待用户输入 → 返回用户回复

    Args:
        tool_args: 工具参数（包含 question、context）
        tool_id: 工具调用 ID
        messages: 当前消息列表
        node: 当前节点
        state: 流程状态

    Returns:
        用户输入的回复内容
    """
    question = tool_args.get("question", "需要您的帮助")
    context_str = tool_args.get("context")

    # 在 interrupt 前保存进度（state），确保前端能展示当前对话
    state.set_conversation_messages(node.node_key, list(messages))

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

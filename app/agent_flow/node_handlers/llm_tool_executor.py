"""
LLM 工具执行模块

从 llm_tool_handler.py 抽离的工具执行职责，负责：
- 工具处理器初始化（收集工具定义 + system_prompt 提示）
- 工具调用处理（并行执行、人工交互、审批确认、次数限制）
- 统一截断（通过 tool_output_truncate 模块）
"""

import asyncio
import json
import logging
from typing import Any, Callable, Optional

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.types import StreamWriter, interrupt

from app.agent_flow.flow_context import FlowState
from app.agent_flow.flow_event import (
    ToolApprovalEvent,
    ToolCallLimitEvent,
)
from app.agent_flow.message_buffer import MessageBuffer
from app.agent_flow.node_handlers.base_handler import BaseNodeConfig
from app.agent_flow.tool_output_truncate import smart_truncate_output
from app.agent_flow.tool_resolver import (
    filter_tools_by_intent,
    get_connected_tool_edges,
)
from app.models.flow_node import FlowNode

logger = logging.getLogger(__name__)

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


async def setup_tool_handlers(
    node: FlowNode,
    state: FlowState,
    writer: Optional[StreamWriter],
    config: Optional[RunnableConfig],
    cfg: BaseNodeConfig,
    *,
    flow: Optional[object],
    db_session_factory: Optional[object],
    handler_registry: dict,
    emit_fn: Optional[Callable] = None,
) -> tuple[list[BaseTool], list[str]]:
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

    Returns:
        (工具列表, prompt 提示片段列表)
    """
    tools: list[BaseTool] = []
    prompt_hints: list[tuple[int, int, str]] = []

    if not flow or not db_session_factory:
        return tools, [h for _, _, h in prompt_hints]

    # 获取工具节点 + 边对，按意图条件过滤
    tool_edge_pairs = get_connected_tool_edges(flow, node.node_key)
    tool_edge_pairs = filter_tools_by_intent(tool_edge_pairs, state)

    # LLM 配置注入到工具处理器
    llm_config = {
        "model": cfg.model,
        "api_key": cfg.api_key,
        "base_url": cfg.base_url,
        "provider": cfg.provider,
        "context_length": cfg.context_length,
    }

    for idx, (tool_node, _edge) in enumerate(tool_edge_pairs):
        handler = handler_registry.get(tool_node.node_type)
        if not handler:
            continue

        # 注入 _agent_id（记忆节点等需要知道当前 Agent 的 ID）
        if hasattr(handler, "_agent_id") and hasattr(flow, "id"):
            handler._agent_id = flow.id

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

    # 去重：同名工具只保留第一个（子 Agent 等节点可能产生重复的通用工具）
    seen_names = set()
    unique_tools = []
    for tool in tools:
        if tool.name not in seen_names:
            seen_names.add(tool.name)
            unique_tools.append(tool)

    return unique_tools, [h for _, _, h in prompt_hints]


async def handle_tool_calls(
    tool_calls: list[dict],
    tools: list[BaseTool],
    msg_buf: MessageBuffer,
    node: FlowNode,
    state: FlowState,
    writer: Optional[StreamWriter],
    tool_call_count: int,
    max_tool_iterations: int,
    *,
    session_id: int,
    check_interrupted_fn: Callable[[FlowState], bool],
    emit_fn: Optional[Callable] = None,
    emit_tool_start_fn: Optional[Callable] = None,
    emit_tool_end_fn: Optional[Callable] = None,
) -> tuple[bool, int]:
    """统一处理所有工具调用（人工协助 + 审批确认 + 并行执行 + 截断）

    不同 MCP 服务器的工具调用并行执行（per-server 锁保证安全），
    非 MCP 工具也并行执行。人工介入工具单独处理。

    Args:
        tool_calls: LLM 返回的工具调用列表
        tools: 可用工具列表
        msg_buf: 消息缓冲区
        node: 当前节点
        state: 流程状态
        writer: SSE 流式写入器
        tool_call_count: 当前已执行的工具调用次数
        max_tool_iterations: 最大工具调用轮次
        session_id: 会话 ID（Agent 模式 > 0）
        check_interrupted_fn: 中断检查回调
        emit_fn: 事件发送回调
        emit_tool_start_fn: 工具开始事件发送回调
        emit_tool_end_fn: 工具结束事件发送回调

    Returns:
        (是否应继续循环, 工具调用总次数)
    """
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
        tool_call_count += 1

        if emit_tool_start_fn:
            emit_tool_start_fn(writer, node.node_key, tool_name, tool_args)

        result = await handle_human_interaction(
            tool_args, tool_id, msg_buf.messages, node, state
        )

        # 截断人工交互结果
        content = smart_truncate_output(result, prefix="human_output")
        msg_buf.append(
            ToolMessage(content=content, tool_call_id=tool_id, name=tool_name)
        )

        if emit_tool_end_fn:
            emit_tool_end_fn(writer, node.node_key, tool_name, result, status="success")
        return True, tool_call_count

    # ---- 工具确认（仅 Agent 模式） ----
    if session_id > 0:
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
                    session_id, tool_calls, list(approval_names)
                )
                if emit_fn:
                    emit_fn(
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
                                writer, node.node_key, tc_name, msg, status="error"
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

    # ---- 并行执行工具调用 ----
    tool_call_count += len(tool_calls)

    async def _run_single_tool(
        tool_call: dict,
    ) -> tuple[dict, Any]:
        """执行单个工具调用并返回 (tool_call, result)"""
        tool_name = tool_call.get("name", "")
        tool_args = tool_call.get("args", "")
        if check_interrupted_fn(state):
            return tool_call, {"success": False, "error": "执行被中断"}
        if emit_tool_start_fn:
            emit_tool_start_fn(writer, node.node_key, tool_name, tool_args)
        result = await execute_tool(
            tool_name,
            tool_args,
            tools,
            state,
            check_interrupted_fn=check_interrupted_fn,
        )
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

        # 统一截断工具执行结果（load_skill 豁免，LLM 需要完整指令内容）
        is_exempt = tool_name == "load_skill"
        if is_exempt:
            content = (
                raw_result
                if isinstance(raw_result, str)
                else json.dumps(raw_result, ensure_ascii=False, default=str)
            )
        else:
            content = smart_truncate_output(raw_result, prefix=tool_name)
        msg_buf.append(
            ToolMessage(content=content, tool_call_id=tool_id, name=tool_name)
        )

        if emit_tool_end_fn:
            sse_result = raw_result if is_exempt else content
            emit_tool_end_fn(writer, node.node_key, tool_name, sse_result, tool_status)

    return True, tool_call_count


def reject_remaining_tools(
    remaining_calls: list[dict],
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

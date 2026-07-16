"""
LLM 消息构建模块

从 llm_tool_handler.py 抽离的消息构建职责，负责：
- 加载对话历史（checkpoint → DB 兜底）
- 校验并修复 tool_call 与 ToolMessage 的配对关系
- interrupt 恢复场景注入人类回复
- 构建 multimodal HumanMessage
- 自动压缩检查（查 DB 最后一条 AI 消息的 prompt_tokens）
"""

import logging
from typing import Callable, Optional, Union

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter

from app.agent_flow.flow_context import FlowState
from app.models.flow_node import FlowNode
from app.services.agent_conversation_service import AgentConversationService
from app.services.conversation_service import ConversationService
from app.utils.media_resolver import build_multimodal_content, collect_media_blocks

logger = logging.getLogger(__name__)


async def build_initial_messages(
    node: FlowNode,
    node_config: dict,
    user_prompt: Optional[str],
    state: FlowState,
    *,
    session_id: int,
    execution_id: int,
    conversation_service: Optional[
        Union[ConversationService, AgentConversationService]
    ],
    db_session_factory: Optional[object],
    config: Optional[RunnableConfig] = None,
    writer: Optional[StreamWriter] = None,
    emit_fn: Optional[Callable] = None,
    emit_tool_end_fn: Optional[Callable] = None,
) -> list[BaseMessage]:
    """构建初始消息列表（统一入口）

    流程：加载历史 → 校验配对 → 注入恢复 → 追加用户消息

    Args:
        node: 流程节点
        node_config: 节点配置
        user_prompt: 用户提示词
        state: 流程状态
        session_id: 会话 ID（Agent 模式）
        execution_id: 执行记录 ID（Flow 模式）
        conversation_service: 对话服务
        db_session_factory: 数据库会话工厂
        config: RunnableConfig（用于 interrupt 恢复）
        writer: SSE 流式写入器
        emit_fn: 事件发送回调
        emit_tool_end_fn: 工具结束事件发送回调

    Returns:
        构建完成的消息列表
    """
    messages = await load_base_messages(
        node,
        node_config,
        state,
        session_id=session_id,
        execution_id=execution_id,
        conversation_service=conversation_service,
        db_session_factory=db_session_factory,
    )
    messages = validate_tool_pairs(messages)
    resume_injected = inject_resume_if_needed(
        messages,
        config,
        node.node_key,
        writer,
        emit_fn=emit_fn,
        emit_tool_end_fn=emit_tool_end_fn,
    )
    append_user_message(
        messages, state, node_config, user_prompt, resume_injected=resume_injected
    )
    return messages


async def load_base_messages(
    node: FlowNode,
    node_config: dict,
    state: FlowState,
    *,
    session_id: int,
    execution_id: int,
    conversation_service: Optional[
        Union[ConversationService, AgentConversationService]
    ],
    db_session_factory: Optional[object],
) -> list[BaseMessage]:
    """加载历史消息：优先 checkpoint（state），其次数据库

    Args:
        node: 流程节点
        node_config: 节点配置
        state: 流程状态（从 checkpoint 恢复时包含历史消息）
        session_id: 会话 ID
        execution_id: 执行记录 ID
        conversation_service: 对话服务
        db_session_factory: 数据库会话工厂

    Returns:
        历史消息列表（不含 SystemMessage）
    """
    # 优先使用 checkpoint 中的消息
    messages: list[BaseMessage] = [
        m
        for m in state.get_conversation_messages(node.node_key)
        if not isinstance(m, SystemMessage)
    ]
    if messages:
        return messages

    # checkpoint 为空则从数据库加载
    return [
        m
        for m in await load_history_from_db(
            node_config,
            node.node_key,
            session_id=session_id,
            execution_id=execution_id,
            conversation_service=conversation_service,
            db_session_factory=db_session_factory,
        )
        if not isinstance(m, SystemMessage)
    ]


def validate_tool_pairs(messages: list[BaseMessage]) -> list[BaseMessage]:
    """校验并修复消息中 tool_call 与 tool result 的配对关系

    确保发送给 LLM 的消息列表满足：
    1. ToolMessage 必须紧跟在对应的 AIMessage（含 tool_calls）之后
    2. 孤立的 ToolMessage（无前置 tool_call）会被移除
    3. 含 tool_calls 但缺少 ToolMessage 的 AIMessage，其未匹配的 tool_calls 会被清除

    Args:
        messages: 原始消息列表

    Returns:
        修复后的消息列表
    """
    result: list[BaseMessage] = []
    # pending_* 跟踪最后一个含 tool_calls 的 AIMessage 中尚未匹配的 call id
    pending_ai_index: int = -1
    pending_ids: set[str] = set()

    def flush_pending():
        """清除未匹配的 tool_calls（AIMessage 中缺少对应 ToolMessage 的调用）"""
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
                logger.debug(f"移除孤立 ToolMessage: tool_call_id={msg.tool_call_id}")
        else:
            flush_pending()
            result.append(msg)

    flush_pending()
    return result


def inject_resume_if_needed(
    messages: list[BaseMessage],
    config: Optional[RunnableConfig],
    node_key: str = "",
    writer: Optional[StreamWriter] = None,
    *,
    emit_fn: Optional[Callable] = None,
    emit_tool_end_fn: Optional[Callable] = None,
) -> bool:
    """interrupt 恢复场景：将人类回复注入为 ToolMessage

    仅当 messages 中存在未匹配的 tool_call 时才注入（说明 resume 来自
    当前 LLM 节点的 request_human_help 工具调用），否则返回 False，
    避免流程中 Human 节点 interrupt 恢复后误影响下游 LLM 节点。
    注入成功时同步发送 tool_call_end 事件，关闭前端 running 状态的工具调用。

    Args:
        messages: 消息列表（就地修改）
        config: RunnableConfig（包含 _human_resume_input）
        node_key: 节点 key
        writer: SSE 流式写入器
        emit_fn: 事件发送回调（用于内部通知，此场景下暂未使用）
        emit_tool_end_fn: 工具结束事件发送回调

    Returns:
        是否成功注入了恢复消息
    """
    if not messages or not config:
        return False

    resume_input = config.get("configurable", {}).get("_human_resume_input")
    if not resume_input:
        return False

    # 收集已有 ToolMessage 的 tool_call_id
    matched_ids = {
        m.tool_call_id
        for m in messages
        if isinstance(m, ToolMessage) and m.tool_call_id
    }

    # 查找未匹配的 tool_call 并注入回复
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
                if node_key and writer and emit_tool_end_fn:
                    emit_tool_end_fn(
                        writer, node_key, tc_name, resume_input, status="success"
                    )
                return True

    return False


def append_user_message(
    messages: list[BaseMessage],
    state: FlowState,
    node_config: dict,
    user_prompt: Optional[str],
    *,
    resume_injected: bool = False,
) -> None:
    """构建 multimodal HumanMessage 并追加到消息列表

    如果已通过 inject_resume_if_needed 注入了恢复消息，则跳过（避免重复追加）。
    支持 capabilities 配置中的图片/文件等多模态内容。

    Args:
        messages: 消息列表（就地追加）
        state: 流程状态（包含 input_data）
        node_config: 节点配置（包含 capabilities）
        user_prompt: 用户提示词
        resume_injected: 是否已注入恢复消息
    """
    if resume_injected:
        return

    actual_user_prompt = user_prompt or state.input_data.get("message", "")
    if not actual_user_prompt:
        return

    # 收集多模态内容（图片/文件等）
    capabilities = node_config.get("capabilities", {})
    media_blocks, file_index = collect_media_blocks(state.input_data, capabilities)
    prompt_text = (
        f"{actual_user_prompt}\n\n{file_index}" if file_index else actual_user_prompt
    )

    if media_blocks:
        content = build_multimodal_content(prompt_text, media_blocks)
    else:
        content = prompt_text

    messages.append(
        HumanMessage(
            content=content,
            additional_kwargs={
                "_raw_user_content": state.input_data.get("message", "")
            },
        )
    )


async def load_history_from_db(
    node_config: dict,
    node_key: str,
    *,
    session_id: int,
    execution_id: int,
    conversation_service: Optional[
        Union[ConversationService, AgentConversationService]
    ],
    db_session_factory: Optional[object],
) -> list[BaseMessage]:
    """从数据库加载对话历史（仅首次执行时调用）

    Agent 模式（session_id > 0）: 始终加载全部历史
    Flow 模式: 支持 history_mode:
    - "none": 不加载历史
    - "flow": 加载整个流程的历史
    - "node": 仅加载当前节点的历史（默认）

    Args:
        node_config: 节点配置
        node_key: 节点 key
        session_id: 会话 ID（Agent 模式）
        execution_id: 执行记录 ID（Flow 模式）
        conversation_service: 对话服务
        db_session_factory: 数据库会话工厂

    Returns:
        历史消息列表
    """
    if not conversation_service or not db_session_factory:
        return []

    max_history_turns = node_config.get("max_history_turns", 10)
    capabilities = node_config.get("capabilities", {})
    id_param = session_id if session_id else execution_id

    # Agent 模式：始终加载全部对话历史
    if session_id:
        try:
            async with db_session_factory() as db:
                messages = await conversation_service.get_full_history(
                    db, id_param, capabilities=capabilities
                )
                return list(messages)
        except Exception:
            return []

    # Flow 模式：按 history_mode 区分
    history_mode = node_config.get("history_mode", "node")
    if history_mode == "none":
        return []

    try:
        async with db_session_factory() as db:
            if history_mode == "flow":
                messages = await conversation_service.get_full_history(
                    db, id_param, limit=max_history_turns * 4, capabilities=capabilities
                )
            else:
                messages = await conversation_service.get_history(
                    db,
                    id_param,
                    node_key,
                    limit=max_history_turns * 4,
                    capabilities=capabilities,
                )
            return list(messages)
    except Exception:
        return []


async def should_auto_compress(
    session_id: int,
    db_session_factory: Optional[object],
    context_length: int,
) -> int:
    """查 DB 最后一条 AI 消息的 prompt_tokens，返回已用 token 数

    用于判断是否需要在 ReAct 循环前自动压缩上下文。

    Args:
        session_id: 会话 ID（仅 Agent 模式有效）
        db_session_factory: 数据库会话工厂
        context_length: 模型上下文窗口大小（0 表示不限制）

    Returns:
        已使用的 prompt_tokens 数量，查询失败返回 0
    """
    if not session_id or not db_session_factory or context_length <= 0:
        return 0
    try:
        async with db_session_factory() as db:
            from sqlalchemy import select

            from app.models.agent_message import AgentMessage

            query = (
                select(AgentMessage.prompt_tokens)
                .where(
                    AgentMessage.session_id == session_id,
                    AgentMessage.role == "ai",
                    AgentMessage.is_delete == 0,
                )
                .order_by(AgentMessage.id.desc())
                .limit(1)
            )
            result = await db.execute(query)
            return result.scalar() or 0
    except Exception:
        return 0

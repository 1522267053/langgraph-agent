"""
LLM 求助（request_human_help）interrupt 恢复回归测试

根因背景（2026-10-18 实锤，exec 53）：
- interrupt 抛出后节点不会 return，conversation_messages 不进 checkpoint；
- 待恢复的 AIMessage（含 request_human_help tool_call）若未落库，恢复时
  inject_resume_if_needed 找不到匹配目标 → NodeExecutionError
  「未找到待恢复的人工协助工具调用」。

覆盖：
1. handle_human_interaction 在 interrupt 前将消息落库（含 AIMessage tool_calls）
2. 恢复场景 load_base_messages 强制从 DB 加载（checkpoint 为空也能找回）
3. history_mode="none" 的节点恢复时降级为 node 加载（force_load）
4. inject_resume_if_needed 用落库消息成功注入 ToolMessage（端到端匹配）
5. 未落库（修复前行为）恢复注入失败——根因对照，防止回归反转
"""

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent_flow.flow_context import FlowState
from app.agent_flow.message_buffer import MessageBuffer
from app.agent_flow.node_handlers.llm_message_builder import (
    inject_resume_if_needed,
    load_base_messages,
)
from app.agent_flow.node_handlers.llm_tool_executor import handle_human_interaction
from app.config.database import AsyncSessionLocal
from app.models.conversation_message import ConversationMessage
from app.models.flow_node import FlowNode
from app.services.conversation_service import ConversationService

EXEC_ID = 990001
NODE_KEY = "llm_test_resume"
TOOL_CALL_ID = "call_test_human_help_001"


def _make_node(history_mode: str = "node") -> FlowNode:
    return FlowNode(
        flow_id=1,
        node_type="llm",
        node_key=NODE_KEY,
        base_config={"history_mode": history_mode},
    )


def _make_resume_config() -> dict:
    return {
        "configurable": {
            "_human_resume_input": "用户回复内容",
            "_human_resume_type": "human_input_required",
            "_human_resume_node_key": NODE_KEY,
            "_human_resume_tool_call_id": TOOL_CALL_ID,
        }
    }


def _make_msg_buf() -> MessageBuffer:
    return MessageBuffer(
        [],
        execution_id=EXEC_ID,
        db_session_factory=AsyncSessionLocal,
        conversation_service=ConversationService(),
        node_key=NODE_KEY,
        history_mode="node",
    )


def _make_ai_message() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": TOOL_CALL_ID,
                "name": "request_human_help",
                "args": {"question": "需要什么帮助？"},
                "type": "tool_call",
            }
        ],
    )


async def _db_messages() -> list[ConversationMessage]:
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ConversationMessage)
            .where(
                ConversationMessage.execution_id == EXEC_ID,
                ConversationMessage.is_delete == 0,
            )
            .order_by(ConversationMessage.sequence)
        )
        return list(result.scalars().all())


@pytest.fixture(autouse=True)
async def _clean_conversation():
    """清理本用例执行 ID 的会话消息，保证互不污染"""
    from sqlalchemy import delete

    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(ConversationMessage).where(
                ConversationMessage.execution_id == EXEC_ID
            )
        )
        await db.commit()
    yield
    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(ConversationMessage).where(
                ConversationMessage.execution_id == EXEC_ID
            )
        )
        await db.commit()


async def test_human_interaction_persists_messages_before_interrupt():
    """interrupt 前落库：AIMessage（含 tool_calls）必须写进 DB"""
    node = _make_node()
    state = FlowState()
    msg_buf = _make_msg_buf()
    msg_buf.append(HumanMessage(content="测试一下request_human_help 工具(并发2次)"))
    msg_buf.append(_make_ai_message())

    # interrupt() 依赖 LangGraph 运行时 scratchpad，单测中直接模拟其抛出
    from langgraph.errors import GraphInterrupt

    with patch(
        "app.agent_flow.node_handlers.llm_tool_executor.interrupt",
        side_effect=GraphInterrupt(()),
    ):
        with pytest.raises(GraphInterrupt):
            await handle_human_interaction(
                {"question": "需要什么帮助？"},
                TOOL_CALL_ID,
                msg_buf.messages,
                node,
                state,
                msg_buf=msg_buf,
            )

    rows = await _db_messages()
    roles = [r.role for r in rows]
    assert "ai" in roles, f"AIMessage 未落库: roles={roles}"
    ai_row = next(r for r in rows if r.role == "ai")
    assert ai_row.tool_calls
    assert ai_row.tool_calls[0]["id"] == TOOL_CALL_ID
    assert ai_row.tool_calls[0]["name"] == "request_human_help"


async def test_resume_loads_from_db_and_injects_reply():
    """恢复场景：DB 中有落库消息时，load_base_messages 找回 tool_call 并注入回复"""
    node = _make_node()
    config = _make_resume_config()

    # 模拟 interrupt 前落库的内容（human + AIMessage）
    msg_buf = _make_msg_buf()
    msg_buf.append(HumanMessage(content="测试一下request_human_help 工具(并发2次)"))
    msg_buf.append(_make_ai_message())
    assert await msg_buf.save_to_db()

    state = FlowState()  # checkpoint 为空（interrupt 场景实况）

    messages = await load_base_messages(
        node,
        node.base_config or {},
        state,
        session_id=0,
        execution_id=EXEC_ID,
        conversation_service=ConversationService(),
        db_session_factory=AsyncSessionLocal,
        config=config,
    )

    # 恢复注入：应找到 DB 中的 tool_call 并追加 ToolMessage
    injected = inject_resume_if_needed(messages, config, NODE_KEY)
    assert injected, "恢复注入失败：未找到待恢复的人工协助工具调用"
    tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
    assert any(m.tool_call_id == TOOL_CALL_ID for m in tool_msgs)
    assert tool_msgs[-1].content == "用户回复内容"


async def test_resume_with_history_mode_none_still_loads():
    """history_mode=none 的节点求助恢复：force_load 降级为 node 加载"""
    node = _make_node(history_mode="none")
    config = _make_resume_config()

    msg_buf = _make_msg_buf()
    msg_buf.append(HumanMessage(content="提问"))
    msg_buf.append(_make_ai_message())
    assert await msg_buf.save_to_db()

    messages = await load_base_messages(
        node,
        node.base_config or {},
        FlowState(),
        session_id=0,
        execution_id=EXEC_ID,
        conversation_service=ConversationService(),
        db_session_factory=AsyncSessionLocal,
        config=config,
    )
    assert any(
        isinstance(m, AIMessage) and m.tool_calls for m in messages
    ), "history_mode=none 恢复时未从 DB 找回 AIMessage"
    assert inject_resume_if_needed(messages, config, NODE_KEY)


async def test_resume_fails_without_persisted_messages():
    """未落库（修复前行为）：恢复注入失败——留作根因对照，防止回归反转"""
    node = _make_node()
    config = _make_resume_config()

    messages = await load_base_messages(
        node,
        node.base_config or {},
        FlowState(),
        session_id=0,
        execution_id=EXEC_ID,
        conversation_service=ConversationService(),
        db_session_factory=AsyncSessionLocal,
        config=config,
    )
    assert not inject_resume_if_needed(messages, config, NODE_KEY)

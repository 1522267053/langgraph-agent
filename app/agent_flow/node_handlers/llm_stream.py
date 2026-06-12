"""
流式 LLM 调用模块

从 llm_tool_handler.py 抽离的流式调用职责，负责：
- 流式调用 LLM 并收集完整响应
- 自动重试限流/网络/超时错误（间隔 1s→2s→4s，最多 3 次）
- 解析 thinking 内容（兼容 Anthropic content block 格式）
"""

import asyncio
import logging
from typing import Callable, Optional

import openai
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, BaseMessage

from app.agent_flow.flow_context import FlowState
from app.agent_flow.flow_event import (
    LlmRetryEvent,
    NodeContentEvent,
    NodeThinkingEvent,
)
from langgraph.types import StreamWriter

logger = logging.getLogger(__name__)

# 可重试的错误类型（限流、网络、超时）
_RETRYABLE_ERRORS = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
)

# 重试间隔（秒），最多重试 len(_RETRY_DELAYS) 次
_RETRY_DELAYS = [1, 2, 4]


async def stream_llm_response(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    node_key: str,
    state: FlowState,
    writer: Optional[StreamWriter],
    *,
    check_interrupted_fn: Callable[[FlowState], bool],
) -> tuple[Optional[AIMessageChunk], list[str], str]:
    """流式调用 LLM 并收集响应

    对限流、网络错误、超时自动重试（间隔 1s→2s→4s，最多 3 次）

    Args:
        llm: LLM 实例
        messages: 消息列表
        node_key: 节点 key
        state: 流程状态
        writer: SSE 流式写入器
        check_interrupted_fn: 中断检查回调

    Returns:
        (完整响应, thinking 片段列表, 累积文本内容)
    """
    response: Optional[AIMessageChunk] = None
    current_content = ""
    thinking_chunks: list[str] = []
    retry_count = 0

    while True:
        try:
            async for chunk in llm.astream(messages):
                if check_interrupted_fn(state):
                    break

                # 处理 reasoning_content（DeepSeek 等模型的思考过程）
                if (
                    chunk.additional_kwargs
                    and "reasoning_content" in chunk.additional_kwargs
                ):
                    thinking_chunk = chunk.additional_kwargs["reasoning_content"]
                    thinking_chunks.append(thinking_chunk)
                    if writer:
                        writer(
                            NodeThinkingEvent(node_key=node_key, content=thinking_chunk)
                        )

                # 处理 chunk.content（兼容 str 和 Anthropic content block 列表）
                if chunk.content:
                    for text, is_thinking in parse_content_blocks(chunk.content):
                        if is_thinking:
                            thinking_chunks.append(text)
                            if writer:
                                writer(
                                    NodeThinkingEvent(node_key=node_key, content=text)
                                )
                        else:
                            current_content += text
                            if writer:
                                writer(
                                    NodeContentEvent(node_key=node_key, content=text)
                                )

                response = response + chunk if response else chunk

            break
        except _RETRYABLE_ERRORS as e:
            retry_count += 1
            if retry_count > len(_RETRY_DELAYS):
                raise
            delay = _RETRY_DELAYS[retry_count - 1]
            if writer:
                writer(
                    LlmRetryEvent(
                        node_key=node_key,
                        message=f"LLM请求失败({e})，{delay}秒后重试({retry_count}/3)",
                        retry_count=retry_count,
                        max_retries=len(_RETRY_DELAYS),
                        wait_seconds=delay,
                    )
                )
            await asyncio.sleep(delay)

    return response, thinking_chunks, current_content


def parse_content_blocks(content) -> list[tuple[str, bool]]:
    """解析 chunk.content，兼容 str 和 Anthropic content block 列表格式

    Anthropic streaming 返回的 content 可能是:
    - str: 普通文本
    - list[dict]: [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": "..."}, ...]

    Returns:
        [(文本, 是否 thinking), ...] 列表，忽略 signature 等无关块
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

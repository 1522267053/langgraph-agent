# -*- coding: utf-8 -*-
"""回归测试：LLM 流提前终止（无 finish_reason）必须显性化

背景（2026-10-23 线上现象）：
- 模型网关提前关闭流（未发 finish_reason 与 [DONE]），openai SDK 静默结束迭代；
- 半截正文被上层当完整回答落库（status=success），用户看到话说一半；
- 日志零报错，无法定位。修复：stream_llm_response 对该场景追加可见提示 + warning 日志。

用例：
1. 流正常结束（finish_reason=stop）→ 不追加任何提示（防误伤）
2. 流被截断（finish_reason=length）→ 追加 max_tokens 提示（原有行为不回退）
3. 流提前终止（无 finish_reason）→ 追加「服务端提前中断」提示 + warning 日志
"""

import logging
from typing import Any

import pytest

from app.agent_flow.flow_context import FlowState
from app.agent_flow.node_handlers.llm_stream import stream_llm_response


class _FakeLLM:
    """最小 LLM 替身：按脚本逐 chunk 吐流"""

    model_name = "fake-model"

    def __init__(self, chunks: list[Any]):
        self._chunks = chunks

    async def astream(self, messages):
        for c in self._chunks:
            yield c


class _CaptureWriter:
    def __init__(self):
        self.events: list[Any] = []

    def __call__(self, event):
        self.events.append(event)


def _chunk(content: str = "", finish_reason: str | None = None):
    metadata = {"finish_reason": finish_reason} if finish_reason else {}
    return AIMessageChunk(content=content, response_metadata=metadata)


from langchain_core.messages import AIMessageChunk  # noqa: E402


def _run_stream(chunks: list[Any]):
    """构造 stream_llm_response 协程与 writer（调用方 await 协程取结果）"""
    state = FlowState()
    writer = _CaptureWriter()
    llm = _FakeLLM(chunks)
    coro = stream_llm_response(
        llm,
        [],
        "llm_dev",
        state,
        writer,
        check_interrupted_fn=lambda s: False,
    )
    return coro, writer


@pytest.mark.asyncio
async def test_stream_normal_stop_no_hint():
    """正常 stop 结束：不追加提示"""
    coro, writer = _run_stream([_chunk("你好", "stop")])
    response, thinking, content = await coro
    assert content == "你好"
    assert "⚠️" not in content
    assert not writer.events or "⚠️" not in str(writer.events)


@pytest.mark.asyncio
async def test_stream_length_truncation_hint():
    """length 截断：追加 max_tokens 提示（原有行为）"""
    coro, writer = _run_stream([_chunk("长回答...", "length")])
    response, thinking, content = await coro
    assert "max_tokens" in content
    assert "⚠️" in content


@pytest.mark.asyncio
async def test_stream_early_termination_visible(caplog):
    """提前断流（无 finish_reason）：追加提示 + warning 留痕（本次修复）"""
    with caplog.at_level(
        logging.WARNING, logger="app.agent_flow.node_handlers.llm_stream"
    ):
        coro, writer = _run_stream(
            [
                _chunk("唯一待你拍板的是角标口径——编码后难改："),
                # 网关掐断：最后一个 chunk 无 finish_reason，无更多 chunk
            ]
        )
        response, thinking, content = await coro
    assert "服务端提前中断" in content
    assert "⚠️" in content
    assert any("LLM 流提前终止" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_stream_empty_response_no_hint():
    """空响应（response=None）：不误报（无内容可截断）"""
    llm = _FakeLLM([])
    state = FlowState()
    writer = _CaptureWriter()
    response, thinking, content = await stream_llm_response(
        llm,
        [],
        "llm_dev",
        state,
        writer,
        check_interrupted_fn=lambda s: False,
    )
    assert content == ""
    assert response is None

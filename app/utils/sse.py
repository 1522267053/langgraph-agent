"""
SSE (Server-Sent Events) 工具模块

提供统一的 SSE 响应生成功能
"""

import asyncio
import json
import logging
from typing import AsyncGenerator
from sse_starlette.sse import EventSourceResponse

logger = logging.getLogger(__name__)

DEFAULT_END_EVENT_TYPES = ("error", "flow_done", "waiting_human")

_DISCONNECT_ERRORS = (
    ConnectionError,
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
    OSError,
)


async def create_sse_response(
    stream_generator: AsyncGenerator[dict, None],
    end_event_types: tuple = DEFAULT_END_EVENT_TYPES,
) -> EventSourceResponse:
    """
    创建 SSE 响应

    Args:
        stream_generator: 异步事件生成器，yield 格式为 {"type": "xxx", "data": {...}}
        end_event_types: 结束事件类型列表，遇到这些事件后停止

    Returns:
        EventSourceResponse: SSE 响应对象

    Example:
        ```python
        @router.post("/chat")
        async def chat(request: ChatRequest, db: AsyncSession = Depends(get_db)):
            async def stream():
                async for event in service.chat_stream(db, request.content):
                    yield event
            return await create_sse_response(stream())
        ```
    """

    async def event_generator():
        try:
            async for event in stream_generator:
                event_type = event.get("type", "unknown")
                event_data = event.get("data", {})

                yield {
                    "event": event_type,
                    "data": json.dumps(event_data, ensure_ascii=False),
                }

                if event_type in end_event_types:
                    break

        except asyncio.CancelledError:
            await stream_generator.aclose()
        except _DISCONNECT_ERRORS:
            await stream_generator.aclose()
            logger.debug("SSE 客户端已断开连接")
        except Exception as e:
            from app.agent_flow.flow_event import FlowEventFactory

            error_event = FlowEventFactory.error(str(e))
            yield {
                "event": error_event["type"],
                "data": json.dumps(error_event["data"], ensure_ascii=False),
            }

    return EventSourceResponse(
        event_generator(), ping=30, ping_message_factory=lambda: {"comment": "ping"}
    )


def create_sse_event(event_type: str, data: dict) -> dict:
    """
    创建 SSE 事件对象

    Args:
        event_type: 事件类型
        data: 事件数据

    Returns:
        dict: 标准事件格式 {"type": "xxx", "data": {...}}
    """
    return {"type": event_type, "data": data}

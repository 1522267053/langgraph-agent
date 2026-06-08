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


async def _pump_to_queue(
    gen: AsyncGenerator,
    queue: asyncio.Queue,
    end_event_types: tuple,
) -> None:
    """将 generator 事件泵入 queue，用于 detach 模式下后台继续执行"""
    try:
        async for event in gen:
            event_type = event.get("type", "unknown")
            await queue.put(event)
            if event_type in end_event_types:
                return
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.warning("后台 generator 执行异常", exc_info=True)
    finally:
        await queue.put(None)


async def create_sse_response(
    stream_generator: AsyncGenerator[dict, None],
    end_event_types: tuple = DEFAULT_END_EVENT_TYPES,
    detach_on_disconnect: bool = False,
) -> EventSourceResponse:
    """
    创建 SSE 响应

    Args:
        stream_generator: 异步事件生成器，yield 格式为 {"type": "xxx", "data": {...}}
        end_event_types: 结束事件类型列表，遇到这些事件后停止
        detach_on_disconnect: SSE 断开时不中断 generator，让其在后台继续执行

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
        if detach_on_disconnect:
            queue: asyncio.Queue = asyncio.Queue()
            asyncio.create_task(
                _pump_to_queue(stream_generator, queue, end_event_types)
            )
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    event_type = event.get("type", "unknown")
                    event_data = event.get("data", {})
                    yield {
                        "event": event_type,
                        "data": json.dumps(event_data, ensure_ascii=False),
                    }
            except asyncio.CancelledError:
                pass

        else:
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
                try:
                    await stream_generator.aclose()
                except Exception:
                    pass
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

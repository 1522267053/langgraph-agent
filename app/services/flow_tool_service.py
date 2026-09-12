"""
Flow 作为工具调用的服务层

将 Flow 暴露为 Agent 可调用的工具，保留其中断/审批能力。
与 flow_tool_handler 的关系：
- handler：负责工具注册（StructuredTool + Pydantic schema）
- service：负责执行 + structured 返回（含嵌套自调防护）

核心契约（与 handler / 文档一致）：
- execute 模式（execution_id=None）：
    flow_executor_service.execute_stream(flow_id, input_data)
- resume 模式（execution_id!=None）：
    flow_executor_service.resume_execution(execution_id, human_input)
- 返回值结构：
    成功：{"success": True, "status": "completed", "output_data": {...}}
    中断：{"success": True, "status": "interrupted",
           "execution_id": int, "prompt": str, "context": Optional[str]}
    失败：{"success": False, "status": "error", "error": str}
"""

import logging
from contextvars import ContextVar
from typing import AsyncGenerator, Awaitable, Callable, Optional

from app.services.flow_executor_service import flow_executor_service

logger = logging.getLogger(__name__)


# 调用栈 contextvar：记录当前执行链路上"作为工具被调用"的 flow_id 集合
# 同 flow_id 二次出现即视为嵌套自调（与 dify 防自调策略一致）
_FLOW_TOOL_STACK: ContextVar[frozenset[int]] = ContextVar(
    "flow_tool_stack", default=frozenset()
)


class FlowToolService:
    """Flow 作为工具调用的服务层

    单入口双模式：根据 execution_id 是否为 None 路由到 execute 或 resume。
    """

    def __init__(self):
        super().__init__()

    async def invoke(
        self,
        flow_id: int,
        input_data: Optional[dict] = None,
        execution_id: Optional[int] = None,
        human_input: Optional[str] = None,
        progress_callback: Optional[Callable[[dict], Awaitable[None] | None]] = None,
    ) -> dict:
        """单入口双模式：根据 execution_id 是否为 None 路由

        Args:
            flow_id: 被调用的 Flow ID（flow_type 必须为 flow，agent 不可用）
            input_data: execute 模式的输入数据（resume 模式忽略）
            execution_id: 为 None 时走 execute 模式；提供时走 resume 模式
            human_input: resume 模式必填
            progress_callback: 透传 Flow 中间事件的回调（node_start /
                node_content / waiting_human / flow_done / error 等），
                父 Agent handler 传 writer 包成的回调实现实时进度转发。

        Returns:
            structured dict，详见模块顶部契约。
        """
        # ---- 防嵌套自调 ----
        stack = _FLOW_TOOL_STACK.get()
        if flow_id in stack:
            return {
                "success": False,
                "status": "error",
                "error": (
                    f"Flow {flow_id} 已在工具调用栈中（防嵌套自调）；"
                    f"当前栈：{sorted(stack)}"
                ),
            }

        # ---- 参数校验 ----
        if execution_id is not None and not human_input:
            return {
                "success": False,
                "status": "error",
                "error": "resume 模式必须提供 human_input",
            }

        # ---- execute 模式：默认注入 message="" 占位 ----
        # 背景：前端 Flow 编辑器默认在 input_schema 中注入 `message` 必填字段
        # （参考 nodeRegistry.ts start.initConfig L167）。
        # Flow-as-Tool 场景下 LLM 不传 message 也能跑（Flow 内部业务自行处理），
        # 但 _map_input_to_schema 会因 message 必填而拒绝。
        # 解法：execute 模式下默认填 message=""，LLM 显式传 message 则覆盖。
        # resume 模式 input_data 无意义（参数已固化到 checkpoint），跳过此步。
        if execution_id is None:
            payload = dict(input_data or {})
            payload.setdefault("message", "")
        else:
            payload = {}

        # ---- 进入调用栈 ----
        token = _FLOW_TOOL_STACK.set(stack | {flow_id})
        try:
            if execution_id is None:
                stream = flow_executor_service.execute_stream(flow_id, payload)
            else:
                stream = flow_executor_service.resume_execution(
                    execution_id, human_input or ""
                )
            return await self._consume(stream, progress_callback)
        except Exception as e:
            logger.exception("Flow 工具执行异常: flow_id=%s", flow_id)
            return {
                "success": False,
                "status": "error",
                "error": f"Flow 工具执行异常: {type(e).__name__}: {e}",
            }
        finally:
            _FLOW_TOOL_STACK.reset(token)

    async def _consume(
        self,
        stream: AsyncGenerator[dict, None],
        callback: Optional[Callable[[dict], Awaitable[None] | None]],
    ) -> dict:
        """消费 async generator 直到终止事件

        终结事件：
        - flow_done → status="completed"
        - waiting_human → status="interrupted"（含 execution_id / prompt）
        - error → status="error"
        - 流未正常结束 → 兜底 error

        中间事件透传给 progress_callback（如果提供），
        父 Agent handler 用此机制把 Flow 节点的 node_content /
        node_start 等实时转发到前端。
        """
        try:
            async for event in stream:
                etype = event.get("type")
                edata = event.get("data") or {}

                # 终结事件优先处理（不再透传 callback）
                if etype == "flow_done":
                    return {
                        "success": True,
                        "status": "completed",
                        "output_data": edata.get("output_data") or {},
                    }
                if etype == "waiting_human":
                    return {
                        "success": True,
                        "status": "interrupted",
                        "execution_id": edata.get("execution_id"),
                        "prompt": edata.get("question") or "需要人工输入",
                        "context": edata.get("context"),
                    }
                if etype == "error":
                    return {
                        "success": False,
                        "status": "error",
                        "error": edata.get("message") or "Flow 执行出错",
                    }

                # 中间事件透传给 callback
                if callback is not None:
                    try:
                        result = callback(event)
                        if result is not None and hasattr(result, "__await__"):
                            await result
                    except Exception:
                        logger.warning(
                            "progress_callback 转发失败（忽略不影响主流程）",
                            exc_info=True,
                        )

            # 流未正常结束（execute_stream 正常路径下不应发生）
            return {
                "success": False,
                "status": "error",
                "error": "Flow 事件流未正常结束",
            }
        except Exception as e:
            logger.exception("Flow 事件流消费异常")
            return {
                "success": False,
                "status": "error",
                "error": f"Flow 事件流消费异常: {type(e).__name__}: {e}",
            }


flow_tool_service = FlowToolService()

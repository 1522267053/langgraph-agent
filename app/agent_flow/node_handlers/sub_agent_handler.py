"""
子Agent节点处理器

将Agent作为工具提供给父Agent调用。
阻塞模式执行，执行期间通过 writer 发送心跳事件保持 SSE 连接。
"""

import asyncio
import json
import logging
from typing import Optional, TYPE_CHECKING

from langchain_core.tools import StructuredTool
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter
from pydantic import Field, create_model

from app.config.database import AsyncSessionLocal
from app.models.flow_node import FlowNode
from app.agent_flow.flow_context import FlowState
from app.agent_flow.node_handlers.base_handler import BaseNodeHandler, BaseNodeConfig
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.flow_event import NodeStartEvent, NodeDoneEvent
from app.services.flow_service import flow_service

if TYPE_CHECKING:
    from app.agent_flow.tool_resolver import LlmToolConfig

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 20

_TYPE_MAP = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
}


class SubAgentNodeConfig(BaseNodeConfig):
    """子Agent节点配置"""

    agent_id: int = Field(..., description="引用的Agent ID")


def _build_ask_tool_schema(agent_name: str, input_schema: dict | None, node_key: str):
    """根据子Agent的input_schema动态构建ask工具的参数模型

    Returns:
        (Pydantic模型类, file_list字段名集合)
    """
    model_name = f"Ask{node_key}Input"

    fields_def: dict[str, tuple] = {
        "task": (str, Field(..., description="要委派给子Agent执行的任务描述")),
    }

    file_list_fields: set[str] = set()
    schema_fields: list[dict] = []

    if input_schema:
        schema_fields = input_schema.get("fields") or []
        for sf in schema_fields:
            name = sf.get("name")
            field_type = sf.get("type", "string")
            description = sf.get("description", "")
            required = sf.get("required", False)

            if not name or name == "message":
                continue

            if field_type == "file_list":
                file_list_fields.add(name)
                desc = f"{description}（文件ID列表）" if description else "文件ID列表"
                py_type = list[int]
            else:
                py_type = _TYPE_MAP.get(field_type, str)
                desc = description

            if required:
                fields_def[name] = (py_type, Field(..., description=desc))
            else:
                fields_def[name] = (
                    Optional[py_type],
                    Field(default=None, description=desc),
                )

    model = create_model(model_name, **fields_def)
    return model, file_list_fields


@NodeHandlerRegistry.register("sub_agent")
class SubAgentNodeHandler(BaseNodeHandler):
    """子Agent节点处理器

    将Agent作为工具提供给父Agent调用。
    阻塞模式执行子Agent，期间通过 writer 心跳保持 SSE 活跃。
    取消由父Agent的中断机制传播（CancelledError）。
    """

    ConfigClass = SubAgentNodeConfig

    def __init__(self):
        super().__init__()
        self._writer: Optional[StreamWriter] = None

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        if writer:
            writer(
                NodeStartEvent(
                    node_key=node.node_key,
                    node_type=node.node_type,
                    node_name=node.node_name,
                    input_data={},
                )
            )
        if writer:
            writer(NodeDoneEvent(node_key=node.node_key, output={}))
        return state

    @classmethod
    def allow_multiple_tool_connections(cls) -> bool:
        return True

    async def get_tool(self, node: FlowNode) -> list[StructuredTool]:
        """返回子Agent相关的工具列表"""
        node_config = node.base_config or {}
        agent_id = node_config.get("agent_id")
        if not agent_id:
            return []

        async with AsyncSessionLocal() as db:
            agent = await flow_service.get_by_id(db, agent_id, raise_not_found=False)

        if not agent:
            return []

        agent_name = agent.name or f"agent_{agent_id}"
        tool_prefix = node.node_key
        description = agent.description or ""

        input_schema = None
        if hasattr(agent, "input_schema") and agent.input_schema:
            input_schema = agent.input_schema
            if isinstance(input_schema, str):
                try:
                    input_schema = json.loads(input_schema)
                except (json.JSONDecodeError, TypeError):
                    input_schema = None

        ask_schema, file_list_fields = _build_ask_tool_schema(
            agent_name, input_schema, node.node_key
        )

        tools: list[StructuredTool] = []

        # ---- ask 工具（阻塞 + 心跳） ----
        ask_desc = (
            f"将任务委派给子Agent「{agent_name}」执行。\n\n"
            f"{description}\n\n"
            f"调用后阻塞等待子Agent完成并返回结果。"
        )

        handler_ref = self
        _agent_id = agent_id
        _agent_name = agent_name
        _file_list_fields = file_list_fields

        async def ask_agent(**kwargs) -> dict:
            from app.services.agent_executor_service import agent_executor_service

            task = kwargs.get("task", "")
            extra_params: dict = {
                k: v for k, v in kwargs.items() if k != "task" and v is not None
            }

            session_id = 0
            try:
                # 创建子Agent session（标题标记来源）
                async with AsyncSessionLocal() as db:
                    session = await agent_executor_service.create_session(db, _agent_id)
                    session_id = session.id
                    title = f"[子Agent调用] {task[:40]}"
                    if len(task) > 40:
                        title += "..."
                    session.title = title
                    await db.commit()

                # 在后台执行子Agent（传入 handler_ref 以转发工具审批事件）
                agent_task = asyncio.create_task(
                    _run_sub_agent(
                        session_id,
                        task,
                        extra_params,
                        handler_ref=handler_ref,
                        agent_id=_agent_id,
                        agent_name=_agent_name,
                    )
                )

                # 阻塞等待，定期发心跳保持 SSE
                while True:
                    try:
                        result = await asyncio.wait_for(
                            asyncio.shield(agent_task), timeout=HEARTBEAT_INTERVAL
                        )
                        return result
                    except asyncio.TimeoutError:
                        # 通过 writer 发心跳事件保持 SSE 连接
                        if hasattr(handler_ref, "_writer") and handler_ref._writer:
                            try:
                                handler_ref._writer(
                                    {
                                        "type": "sub_agent_progress",
                                        "data": {"agent": _agent_name},
                                    }
                                )
                            except Exception:
                                pass

            except asyncio.CancelledError:
                # 父Agent被取消 → 中断子Agent + 取消子Agent的工具审批等待
                if session_id:
                    from app.services.interrupt_service import interrupt_service
                    from app.services.tool_approval_service import tool_approval_service

                    interrupt_service.set_agent_interrupted(session_id)
                    tool_approval_service.cancel(session_id)
                raise

            except Exception as e:
                logger.error(f"子Agent执行失败: {e}", exc_info=True)
                return {"error": f"子Agent执行失败: {str(e)}"}

        tools.append(
            StructuredTool(
                name=f"ask_{tool_prefix}",
                description=ask_desc,
                func=None,
                coroutine=ask_agent,
                args_schema=ask_schema,
            )
        )

        return tools

    @classmethod
    def get_tool_config(cls, node: FlowNode, config: "LlmToolConfig") -> bool:
        """将子Agent节点配置添加到工具配置"""
        node_config = node.base_config or {}
        agent_id = node_config.get("agent_id")
        if agent_id:
            config.sub_agent_node_keys.append(node.node_key)
            config.sub_agent_configs[node.node_key] = {
                "agent_id": agent_id,
                "name": node.node_name or "子Agent",
            }
            return True
        return False

    @classmethod
    def get_tool_info(cls, node: FlowNode) -> list[dict]:
        node_key = node.node_key
        return [{"name": f"ask_{node_key}", "description": "将任务委派给子Agent执行"}]


async def _run_sub_agent(
    session_id: int,
    task: str,
    params: dict | None = None,
    handler_ref: Optional["SubAgentNodeHandler"] = None,
    agent_id: int = 0,
    agent_name: str = "",
) -> dict:
    """执行子Agent并返回结果

    当子Agent产生 tool_approval_required 事件时，通过父Agent的 writer 转发到父SSE流，
    附加 is_sub_agent / sub_agent_id / sub_session_id 上下文，供前端路由到正确的审批端点。
    转发后 await future.event.wait() 阻塞，直到前端完成审批。
    """
    from app.services.agent_executor_service import agent_executor_service
    from app.services.tool_approval_service import tool_approval_service

    content = ""
    async for event in agent_executor_service.chat_stream(
        session_id, task, params or {}
    ):
        event_type = event.get("type", "")
        if event_type == "flow_done":
            output = event.get("data", {}).get("output_data", {})
            content = output.get("content", "") if isinstance(output, dict) else ""
        elif event_type == "error":
            error_msg = event.get("data", {}).get("message", "未知错误")
            return {"error": f"子Agent执行出错: {error_msg}"}
        elif event_type == "tool_approval_required":
            if handler_ref and hasattr(handler_ref, "_writer") and handler_ref._writer:
                from app.agent_flow.flow_event import SubAgentToolApprovalEvent

                event_data = event.get("data", {})
                handler_ref._writer(
                    SubAgentToolApprovalEvent(
                        node_key=event_data.get("node_key", ""),
                        tool_calls=event_data.get("tool_calls", []),
                        approval_needed=event_data.get("approval_needed", []),
                        sub_agent_id=agent_id,
                        sub_session_id=session_id,
                        sub_agent_name=agent_name,
                    )
                )

            future = tool_approval_service.get_pending(session_id)
            if future:
                try:
                    await asyncio.wait_for(future.event.wait(), timeout=300)
                except asyncio.TimeoutError:
                    tool_approval_service.remove(session_id)

    return content

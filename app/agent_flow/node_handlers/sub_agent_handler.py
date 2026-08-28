"""
子Agent节点处理器

将Agent作为工具提供给父Agent调用。
子Agent使用独立托管 Run 执行，父Agent仅等待最终结果。
"""

import asyncio
import json
import logging
from typing import Any, Callable, Literal, Optional, TYPE_CHECKING

from langchain_core.tools import StructuredTool
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter
from pydantic import Field, create_model

from app.config.database import AsyncSessionLocal
from app.models.flow_node import FlowNode
from app.agent_flow.flow_context import FlowState
from app.agent_flow.node_handlers.base_handler import BaseNodeHandler, BaseNodeConfig
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.flow_event import (
    NodeStartEvent,
    NodeDoneEvent,
    SubAgentToolApprovalEvent,
)
from app.services.flow_service import flow_service
from app.services.agent_executor_service import format_exception_message

if TYPE_CHECKING:
    from app.agent_flow.tool_resolver import LlmToolConfig

logger = logging.getLogger(__name__)

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

    fields_def: dict[str, Any] = {
        "task": (str, Field(..., description="要委派给子Agent执行的任务描述")),
        "session_mode": (
            Literal["resume", "new"],
            Field(
                default="resume",
                description="会话模式：resume首次创建后复用，new每次创建新会话",
            ),
        ),
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
    子Agent事件由自身 Session 的 SSE 独立订阅，父Agent仅阻塞等待结果。
    """

    ConfigClass = SubAgentNodeConfig

    def __init__(self):
        super().__init__()
        self._writer: Optional[StreamWriter] = None
        self._parent_session_id = 0

    def _resolve_context(self, config: Optional[RunnableConfig]) -> None:
        """记录当前父Agent会话，供子Agent工具创建/复用 session。"""
        configurable = (config or {}).get("configurable", {})
        session_id = configurable.get("session_id")
        if not session_id:
            thread_id = str(configurable.get("thread_id") or "")
            if thread_id.startswith("agent_"):
                session_id = thread_id.removeprefix("agent_")
        try:
            self._parent_session_id = int(session_id or 0)
        except (TypeError, ValueError):
            self._parent_session_id = 0

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
            writer(
                NodeDoneEvent(
                    node_key=node.node_key, node_type=node.node_type, output_data={}
                )
            )
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

        # ---- ask 工具（阻塞等待托管子Agent） ----
        ask_desc = (
            f"将任务委派给子Agent「{agent_name}」执行。\n\n"
            f"{description}\n\n"
            f"调用后阻塞等待子Agent完成并返回结果。\n"
            "session_mode=resume 时首次创建后复用同一会话，适合连续任务；"
            "session_mode=new 时每次创建独立会话，适合互不依赖的并行任务。"
        )

        _agent_id = agent_id
        _agent_name = agent_name
        _file_list_fields = file_list_fields
        _parent_session_id = self._parent_session_id
        _parent_writer = self._writer

        async def ask_agent(**kwargs) -> dict | str:
            from app.services.agent_executor_service import agent_executor_service

            task = kwargs.get("task", "")
            session_mode = kwargs.get("session_mode", "resume")
            extra_params: dict = {
                k: v
                for k, v in kwargs.items()
                if k not in {"task", "session_mode"} and v is not None
            }

            try:
                # 创建子Agent session（标题标记来源）
                async with AsyncSessionLocal() as db:
                    if session_mode == "resume" and _parent_session_id:
                        session = await agent_executor_service.get_or_create_sub_agent_session(
                            db,
                            _agent_id,
                            _parent_session_id,
                            node.node_key,
                        )
                    else:
                        session = await agent_executor_service.create_session(
                            db, _agent_id
                        )
                    session_id = session.id
                    if session.title == "新对话":
                        title = f"[子Agent调用] {task[:40]}"
                        if len(task) > 40:
                            title += "..."
                        session.title = title
                        await db.commit()

                def forward_approval(event: dict[str, Any]) -> None:
                    event_data = event.get("data") or {}
                    if not _parent_writer:
                        return
                    _parent_writer(
                        SubAgentToolApprovalEvent(
                            node_key=event_data.get("node_key", ""),
                            tool_calls=event_data.get("tool_calls", []),
                            approval_needed=event_data.get("approval_needed", []),
                            sub_agent_id=_agent_id,
                            sub_session_id=session_id,
                            sub_agent_name=_agent_name,
                        )
                    )

                return await _run_sub_agent(
                    session_id,
                    task,
                    extra_params,
                    approval_callback=forward_approval if _parent_writer else None,
                )
            except Exception as e:
                logger.error(f"子Agent执行失败: {e}", exc_info=True)
                return {
                    "success": False,
                    "status": "error",
                    "error": f"子Agent执行失败: {format_exception_message(e)}",
                }

        tool = StructuredTool(
            name=f"ask_{tool_prefix}",
            description=ask_desc,
            func=None,
            coroutine=ask_agent,
            args_schema=ask_schema,
            metadata={
                "sub_agent": True,
            },
        )
        tools.append(tool)

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
    approval_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict | str:
    """启动托管子Agent并等待最终结果，不消费其 SSE 事件。"""
    from app.services.agent_executor_service import agent_executor_service

    run_id = agent_executor_service.start_chat_run(
        session_id,
        task,
        params or {},
        approval_callback=approval_callback,
    )
    try:
        result = await agent_executor_service.wait_run_result(session_id, run_id)
    except asyncio.CancelledError:
        cancel_error = await agent_executor_service._await_cancellation_safe(
            agent_executor_service.cancel_run(
                session_id,
                expected_run_id=run_id,
            )
        )
        if cancel_error:
            logger.warning(
                "父Agent取消时级联取消子Agent失败: session_id=%s, run_id=%s, error=%s",
                session_id,
                run_id,
                cancel_error,
            )
        raise
    except Exception:
        try:
            await agent_executor_service.cancel_run(
                session_id,
                expected_run_id=run_id,
            )
        except Exception:
            logger.warning(
                "异常退出时取消子Agent失败: session_id=%s, run_id=%s",
                session_id,
                run_id,
                exc_info=True,
            )
        raise

    status = result.get("status", "error")
    if status == "success":
        output_data = result.get("output_data") or {}
        content = output_data.get("content", "")
        return content if isinstance(content, str) else str(content or "")

    if status == "cancelled":
        return {
            "success": False,
            "status": "cancelled",
            "error": "子Agent执行已取消",
        }

    if status == "waiting_human":
        waiting_data = result.get("waiting_data") or {}
        question = waiting_data.get("question") or "等待人工输入"
        return {
            "success": False,
            "status": "waiting_human",
            "error": f"子Agent已暂停并等待人工输入: {question}",
        }

    error_detail = (result.get("error") or "").strip()
    if not error_detail.rstrip(":： "):
        error_detail = "未知错误"

    error_result = {
        "success": False,
        "status": str(status),
        "error": f"子Agent执行出错: {error_detail}",
    }
    output_data = result.get("output_data") or {}
    content = output_data.get("content")
    if content:
        error_result["content"] = content
    return error_result

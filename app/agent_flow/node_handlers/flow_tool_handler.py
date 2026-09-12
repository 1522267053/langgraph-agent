"""
Flow工具节点处理器

将已发布的 Flow 暴露为 Agent 可调用的工具，保留 Flow 的中断/人工审批能力。
与 sub_agent_handler 的关系：
- sub_agent 调子 Agent（独立 session）
- flow_tool 调子 Flow（独立 execution_id + LangGraph checkpoint 持久化中断）

单工具双模式（核心设计）：
- 工具名：`flow_<flow_id>_tool`（同一节点暴露一个工具）
- 参数 schema 把 Flow.input_schema.fields 中除 'message' 外的所有字段作为入参，
  再加上 `execution_id` / `human_input` 两个 resume 专用字段。
- LLM 调用时不传 execution_id → execute 模式（启动新执行）
- LLM 调用时传 execution_id + human_input → resume 模式（恢复等待中执行）
- 中断场景：Flow 中途 interrupt → 返回 {status:"interrupted", execution_id, prompt}
  → Agent 在下一轮决策里用同一工具传回 execution_id + human_input 继续
"""

import json
import logging
from typing import Any, Optional, TYPE_CHECKING

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.types import StreamWriter
from pydantic import Field, create_model

from app.agent_flow.flow_context import FlowState
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.node_handlers.base_handler import BaseNodeHandler, BaseNodeConfig
from app.config.database import AsyncSessionLocal
from app.models.flow import Flow
from app.models.flow_node import FlowNode
from app.services.flow_tool_service import flow_tool_service

if TYPE_CHECKING:
    from app.agent_flow.tool_resolver import LlmToolConfig

logger = logging.getLogger(__name__)


# Pydantic 类型映射（与 sub_agent_handler._TYPE_MAP 一致；field_list 复用）
_TYPE_MAP = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
}


class FlowToolNodeConfig(BaseNodeConfig):
    """Flow工具节点配置"""

    flow_id: int = Field(..., description="引用的 Flow ID（必须已发布且类型为 flow）")


def _parse_input_schema(input_schema: Any) -> list[dict]:
    """规范化 Flow.input_schema → fields 列表

    flow.input_schema 可能为 dict / str(json) / Pydantic 对象 / None。
    """
    if not input_schema:
        return []
    if isinstance(input_schema, str):
        try:
            input_schema = json.loads(input_schema)
        except (json.JSONDecodeError, TypeError):
            return []
    if hasattr(input_schema, "fields"):
        return list(input_schema.fields or [])
    if isinstance(input_schema, dict):
        return list(input_schema.get("fields") or [])
    return []


def _build_flow_tool_schema(flow: Flow, node_key: str):
    """根据 Flow 的 input_schema 构建 invoke 工具的 Pydantic 模型

    返回 (model_class, file_list_fields)，与 sub_agent_handler 风格一致。

    Schema 字段：
    - Flow.input_schema.fields 中除 'message' 外的字段（LLM 入参）
    - execution_id: Optional[int]（resume 模式标识）
    - human_input: Optional[str]（resume 模式必填）
    """
    model_name = f"FlowToolInvoke{node_key}Input"
    fields_def: dict[str, Any] = {}
    file_list_fields: set[str] = set()

    schema_fields = _parse_input_schema(getattr(flow, "input_schema", None))
    for sf in schema_fields:
        name = sf.get("name")
        field_type = sf.get("type", "string")
        description = sf.get("description", "")
        required = sf.get("required", False)

        if not name or name == "message":
            # 'message' 是 Agent 主入口字段，Flow-as-Tool 不使用
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

    # resume 模式专用字段
    fields_def["execution_id"] = (
        Optional[int],
        Field(
            default=None,
            description=(
                "resume 模式标识：上次调用返回 interrupted 时携带的 execution_id。"
                "不传或传 None = execute 模式（启动新执行）；"
                "提供 = resume 模式（恢复等待中的 Flow）。"
            ),
        ),
    )
    fields_def["human_input"] = (
        Optional[str],
        Field(
            default=None,
            description=(
                "resume 模式必填：恢复 Flow 时的人工输入内容。execute 模式忽略此字段。"
            ),
        ),
    )

    model = create_model(model_name, **fields_def)
    return model, file_list_fields


@NodeHandlerRegistry.register("flow_tool")
class FlowToolNodeHandler(BaseNodeHandler):
    """Flow工具节点处理器

    - 节点本身在 LangGraph 中是空操作（与 sub_agent 一致）
    - 通过 get_tool() 向 LLM 暴露单个 invoke 工具（execute/resume 双模式）
    - 实际执行走 flow_tool_service.invoke（带防嵌套自调）
    """

    node_type = "flow_tool"
    ConfigClass = FlowToolNodeConfig

    def __init__(self):
        super().__init__()
        self._writer: Optional[StreamWriter] = None

    def _resolve_writer(self, config: Optional[RunnableConfig]) -> None:
        """记录父 Agent 的 StreamWriter，供 invoke 时透传 Flow 中间事件"""
        # StreamWriter 通过 handler_registry 调用栈或外部注入；
        # sub_agent_handler 同样不通过 config 拿 writer（直接 self._writer），
        # 我们保持一致——后续在 LlmToolNodeHandler 集成时通过属性注入。
        pass

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        """node.execute 空操作（与 sub_agent / mcp 一致）"""
        if writer:
            from app.agent_flow.flow_event import NodeStartEvent, NodeDoneEvent

            writer(
                NodeStartEvent(
                    node_key=node.node_key,
                    node_type=node.node_type,
                    node_name=node.node_name,
                    input_data={},
                )
            )
            writer(
                NodeDoneEvent(
                    node_key=node.node_key,
                    node_type=node.node_type,
                    output_data={},
                )
            )
        return state

    @classmethod
    def allow_multiple_tool_connections(cls) -> bool:
        """允许多实例连接同一 LLM（同一 Agent 可调多个 Flow 工具）"""
        return True

    async def get_tool(self, node: FlowNode) -> list[StructuredTool]:
        """返回 Flow 工具（单工具：execute/resume 双模式）"""
        node_config = node.base_config or {}
        flow_id = node_config.get("flow_id")
        if not flow_id:
            return []

        async with AsyncSessionLocal() as db:
            flow = await self._get_flow(db, flow_id)

        if not flow:
            logger.warning(
                "Flow工具节点 %s 引用了不存在的 flow_id=%s", node.node_key, flow_id
            )
            return []

        flow_name = flow.name or f"flow_{flow_id}"
        tool_name = f"flow_{flow_id}_tool"

        tool_schema, _file_list_fields = _build_flow_tool_schema(flow, node.node_key)

        description = self._build_description(flow, flow_name)

        _flow_id = flow_id
        _node_key = node.node_key

        async def invoke_flow_tool(**kwargs) -> dict | str:
            """单入口：根据 execution_id 是否提供路由 execute / resume"""
            execution_id = kwargs.pop("execution_id", None)
            human_input = kwargs.pop("human_input", None)
            # 余下 kwargs 即 input_data 字段（None 字段剔除）
            input_data = {k: v for k, v in kwargs.items() if v is not None}

            try:
                result = await flow_tool_service.invoke(
                    flow_id=_flow_id,
                    input_data=input_data,
                    execution_id=execution_id,
                    human_input=human_input,
                )
                return result
            except Exception as e:
                logger.exception("Flow工具 invoke 异常: flow_id=%s", _flow_id)
                return {
                    "success": False,
                    "status": "error",
                    "error": f"Flow工具执行失败: {type(e).__name__}: {e}",
                }

        tool = StructuredTool(
            name=tool_name,
            description=description,
            func=None,
            coroutine=invoke_flow_tool,
            args_schema=tool_schema,
            metadata={
                "flow_tool": True,
                "flow_id": _flow_id,
            },
        )
        return [tool]

    @staticmethod
    async def _get_flow(db, flow_id: int) -> Optional[Flow]:
        """读取 Flow（不抛异常，缺失返回 None）"""
        from app.services.flow_service import flow_service

        return await flow_service.get_by_id(db, flow_id, raise_not_found=False)

    @staticmethod
    def _build_description(flow: Flow, flow_name: str) -> str:
        """构造工具 description（明确告诉 LLM 何时调、interrupt 后怎么办）"""
        base_desc = flow.description or ""

        parts = [
            f"调用已发布的 Flow「{flow_name}」执行任务。",
        ]
        if base_desc:
            parts.append(base_desc)
        parts.append("")
        parts.append(
            "单工具双模式：\n"
            "- 不传 execution_id = execute 模式（启动新执行）\n"
            "- 提供 execution_id + human_input = resume 模式（恢复等待中的 Flow）\n"
            ""
        )
        parts.append(
            "返回值说明：\n"
            '- status="completed" → 成功，output_data 含结果\n'
            '- status="interrupted" → Flow 中途等待人工输入，'
            "execution_id/prompt 字段给出；下一轮再次调用本工具，"
            "传 execution_id + human_input 即可继续\n"
            '- status="error" → 失败，error 字段说明原因'
        )
        return "\n".join(parts)

    @classmethod
    def get_tool_config(cls, node: FlowNode, config: "LlmToolConfig") -> bool:
        """将 Flow 工具节点配置写入 LlmToolConfig"""
        node_config = node.base_config or {}
        flow_id = node_config.get("flow_id")
        if flow_id:
            config.flow_tool_node_keys.append(node.node_key)
            config.flow_tool_configs[node.node_key] = {
                "flow_id": int(flow_id),
                "name": node.node_name or "Flow工具",
            }
            return True
        return False

    @classmethod
    def get_tool_info(cls, node: FlowNode) -> list[dict]:
        """返回工具元数据 [{name, description}]，供前端/UI 展示"""
        node_config = node.base_config or {}
        flow_id = node_config.get("flow_id")
        if not flow_id:
            return []
        return [
            {
                "name": f"flow_{flow_id}_tool",
                "description": (
                    f"调用已发布 Flow #{flow_id} 作为工具（保留中断/审批能力）"
                ),
            }
        ]

"""远程工具构造器

将 WebSocket 客户端注册的工具定义转换为 LangChain StructuredTool。
工具执行时通过 WebSocket 发送 tool_invoke 请求，等待客户端返回结果。

工具名加 ``remote__`` 前缀避免与流程图内工具冲突。
"""

import asyncio
import json
import logging
from typing import Any
from uuid import uuid4

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

logger = logging.getLogger(__name__)

_JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _json_schema_to_pydantic(tool_name: str, schema: dict) -> type[BaseModel]:
    """将 JSON Schema 转为 Pydantic 模型（用作 args_schema）

    支持常见类型（string/number/integer/boolean/array/object），
    不支持的类型回退为 str。
    """
    if not schema or schema.get("type") != "object":
        return create_model(f"{tool_name}_input", __base__=BaseModel)

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    fields: dict[str, tuple[type, Any]] = {}
    for prop_name, prop_schema in properties.items():
        prop_type = prop_schema.get("type", "string")
        py_type = _JSON_TYPE_MAP.get(prop_type, str)
        description = prop_schema.get("description", prop_name)

        if prop_name in required:
            fields[prop_name] = (py_type, Field(..., description=description))
        else:
            default = prop_schema.get("default")
            fields[prop_name] = (
                py_type,
                Field(default=default, description=description),
            )

    return create_model(f"{tool_name}_input", __base__=BaseModel, **fields)


def create_remote_tool(tool_def: dict, conn: Any) -> StructuredTool:
    """将客户端注册的工具定义转为 StructuredTool

    Args:
        tool_def: ``{"name": str, "description": str, "parameters": dict}``
        conn: WSConnection 对象（含 websocket 和 pending_calls）

    Returns:
        StructuredTool，执行时通过 WS 发送 tool_invoke 并等待 tool_result
    """
    raw_name = tool_def.get("name", "")
    if not raw_name:
        raise ValueError("工具定义缺少 name 字段")

    prefixed_name = f"remote__{raw_name}"
    description = tool_def.get("description", f"远程工具: {raw_name}")
    timeout = getattr(conn, "tool_timeout", 120)
    args_schema = _json_schema_to_pydantic(raw_name, tool_def.get("parameters", {}))

    async def remote_coro(**kwargs) -> str:
        call_id = uuid4().hex
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        conn.pending_calls[call_id] = future

        try:
            await conn.websocket.send_json(
                {
                    "type": "tool_invoke",
                    "data": {
                        "call_id": call_id,
                        "name": raw_name,
                        "args": kwargs,
                    },
                }
            )
        except Exception as e:
            conn.pending_calls.pop(call_id, None)
            return json.dumps(
                {"success": False, "error": f"发送工具调用失败: {e}"},
                ensure_ascii=False,
            )

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return (
                result
                if isinstance(result, str)
                else json.dumps(result, ensure_ascii=False)
            )
        except asyncio.TimeoutError:
            conn.pending_calls.pop(call_id, None)
            return json.dumps(
                {
                    "success": False,
                    "error": f"远程工具 {raw_name} 执行超时（{timeout}秒）",
                },
                ensure_ascii=False,
            )

    return StructuredTool(
        name=prefixed_name,
        description=description,
        func=None,
        coroutine=remote_coro,
        args_schema=args_schema,
    )

"""
节点处理器基类
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional, Any, TYPE_CHECKING

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.types import StreamWriter
from pydantic import BaseModel, Field

from app.models.flow_node import FlowNode
from app.agent_flow.flow_event import ErrorEvent
from app.agent_flow.variable_resolver import VariableResolver, variable_resolver

if TYPE_CHECKING:
    from app.agent_flow.flow_event import FlowEvent
    from app.agent_flow.tool_resolver import LlmToolConfig
    from app.agent_flow.flow_context import FlowState

logger = logging.getLogger(__name__)

# ---- 公共数据模型 ----


class NodeVariable(BaseModel):
    """节点变量（输入/输出通用，与前端对齐）"""

    name: str = Field("", description="变量名")
    source: str = Field("", description="来源路径")
    type: Optional[str] = Field(None, description="数据类型")


class BaseNodeConfig(BaseModel):
    """节点配置基类，提供输入输出变量的公共字段"""

    model_config = {"extra": "ignore"}
    input_variables: list[NodeVariable] = []
    output_variables: list[NodeVariable] = []


# ---- Pydantic → 配置描述 ----

_PYDANTIC_TYPE_MAP = {
    "string": "string",
    "number": "number",
    "integer": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}


def _resolve_schema_ref(ref: str, defs: dict, depth: int = 0) -> dict:
    """递归解析 $ref 引用，展开嵌套模型的字段描述"""
    if depth > 3:  # 防御循环引用
        return {}
    ref_name = ref.rsplit("/", 1)[-1]
    model_def = defs.get(ref_name, {})
    props = model_def.get("properties", {})
    required = set(model_def.get("required", []))
    result: dict[str, Any] = {"type": "object", "properties": {}}
    for pname, pprop in props.items():
        ptype = pprop.get("type", "string")
        p_any = pprop.get("anyOf", [])
        desc = {
            "type": _PYDANTIC_TYPE_MAP.get(ptype, ptype),
            "required": pname in required
            and not any(t.get("type") == "null" for t in p_any),
            "description": pprop.get("description") or pprop.get("title") or pname,
        }
        if "default" in pprop:
            desc["default"] = pprop["default"]
        # 递归展开嵌套引用
        items_ref = pprop.get("items", {}).get("$ref")
        if items_ref:
            desc["type"] = "array"
            desc["items"] = _resolve_schema_ref(items_ref, defs, depth + 1)
        elif "$ref" in pprop:
            desc["type"] = "object"
            ref_def = _resolve_schema_ref(pprop["$ref"], defs, depth + 1)
            desc["properties"] = ref_def.get("properties", {})
        else:
            for any_entry in p_any:
                if "$ref" in any_entry:
                    desc["type"] = "object"
                    ref_def = _resolve_schema_ref(any_entry["$ref"], defs, depth + 1)
                    desc["properties"] = ref_def.get("properties", {})
                    break
        result["properties"][pname] = desc
    return result


def _schema_from_pydantic(model_cls: type[BaseModel]) -> list[dict]:
    """从 Pydantic 模型类自动提取配置字段描述，递归展开嵌套引用"""
    schema = model_cls.model_json_schema()
    properties = schema.get("properties", {})
    required_set = set(schema.get("required", []))
    defs = schema.get("$defs", {})

    fields = []
    for name, prop in properties.items():
        # 跳过 allOf 引用（嵌套模型）
        if "allOf" in prop:
            ref = prop["allOf"][0].get("$ref", "")
            ref_name = ref.rsplit("/", 1)[-1]
            if ref_name in defs:
                fields.append(
                    {
                        "name": name,
                        "type": "object",
                        "required": name in required_set,
                        "description": prop.get("description")
                        or prop.get("title")
                        or name,
                        "properties": _resolve_schema_ref(ref, defs).get(
                            "properties", {}
                        ),
                    }
                )
            continue

        # 跳过直接 $ref（非 Optional 嵌套模型）
        if "$ref" in prop:
            ref_name = prop["$ref"].rsplit("/", 1)[-1]
            if ref_name in defs:
                fields.append(
                    {
                        "name": name,
                        "type": "object",
                        "required": name in required_set,
                        "description": prop.get("description")
                        or prop.get("title")
                        or name,
                        "properties": _resolve_schema_ref(prop["$ref"], defs).get(
                            "properties", {}
                        ),
                    }
                )
            continue

        any_of = prop.get("anyOf", [])
        field_type = prop.get("type", "string")
        is_optional = any(t.get("type") == "null" for t in any_of)

        mapped_type = _PYDANTIC_TYPE_MAP.get(field_type, "string")

        field_desc: dict[str, Any] = {
            "name": name,
            "type": mapped_type,
            "required": name in required_set and not is_optional,
            "description": prop.get("description") or prop.get("title") or name,
        }
        if "default" in prop:
            field_desc["default"] = prop["default"]
        elif "default" not in field_desc and name in model_cls.model_fields:
            fi = model_cls.model_fields[name]
            if fi.default_factory is not None:
                field_desc["default"] = fi.default_factory()
        if "enum" in prop:
            field_desc["options"] = prop["enum"]
        # 透传 json_schema_extra 中的自定义键
        for extra_key in ("options", "label"):
            if extra_key in prop and extra_key not in field_desc:
                field_desc[extra_key] = prop[extra_key]

        # 展开 array 类型的嵌套 items 引用
        items_ref = prop.get("items", {}).get("$ref")
        if items_ref and mapped_type == "array":
            field_desc["items"] = _resolve_schema_ref(items_ref, defs)

        # 展开 anyOf 中的 $ref（如 Optional[SomeModel]）
        if "type" not in prop and any("$ref" in e for e in any_of):
            for any_entry in any_of:
                if "$ref" in any_entry:
                    field_desc["type"] = "object"
                    ref_def = _resolve_schema_ref(any_entry["$ref"], defs)
                    field_desc["properties"] = ref_def.get("properties", {})
                    break

        fields.append(field_desc)

    return fields


class BaseNodeHandler(ABC):
    """
    节点处理器基类
    所有节点处理器都应该继承此类
    """

    ConfigClass: Optional[type[BaseModel]] = None

    def __init__(self, resolver: Optional[VariableResolver] = None):
        """
        初始化处理器

        Args:
            resolver: 变量解析器，默认使用全局单例
        """
        self._resolver = resolver or variable_resolver

    def _get_config(self, node: FlowNode) -> Any:
        """
        解析节点配置

        如果定义了 ConfigClass，解析为 Pydantic 模型实例（extra="ignore" 兼容旧数据）；
        否则返回原始 dict。

        Args:
            node: 节点对象

        Returns:
            ConfigClass 实例或原始 dict
        """
        raw = node.base_config or {}
        if self.ConfigClass is not None:
            return self.ConfigClass.model_validate(raw)
        return raw

    @abstractmethod
    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState | dict:
        """
        执行节点逻辑

        Args:
            node: 节点对象
            state: 当前流程状态
            config: LangGraph 运行配置
            writer: StreamWriter 用于流式输出事件

        Returns:
            更新后的流程状态或状态更新字典
        """
        pass

    # ---- 事件发送 ----

    @staticmethod
    def _emit(writer: Optional[StreamWriter], event: FlowEvent) -> None:
        """安全发送事件（writer 为 None 时跳过）"""
        if writer:
            writer(event)

    def _emit_error(
        self,
        writer: Optional[StreamWriter],
        node_key: str,
        message: str,
    ) -> None:
        """发送错误事件到流式输出"""
        self._emit(writer, ErrorEvent(message=message, node_key=node_key))

    # ---- 配置校验 ----

    def _require_config(
        self,
        config: dict,
        key: str,
        node_key: str,
        label: str,
        state: FlowState,
        writer: Optional[StreamWriter] = None,
    ) -> Any | None:
        """
        校验必填配置项，缺失时同时写入 state 错误并发送流式事件

        Args:
            config: 配置字典
            key: 配置键名
            node_key: 节点 key（用于记录错误）
            label: 中文标签（用于错误消息）
            state: 流程状态
            writer: StreamWriter（可选，传入时同步发送错误事件）

        Returns:
            配置值（存在且非空时），否则返回 None
        """
        value = config.get(key)
        if not value:
            msg = f"{label}不能为空"
            logger.warning("节点[%s]缺少必填配置: %s(%s)", node_key, label, key)
            state.add_error(node_key, msg)
            self._emit_error(writer, node_key, msg)
            return None
        return value

    def check_config(
        self,
        config: dict,
        node_key: str,
        state: FlowState,
        writer: Optional[StreamWriter] = None,
    ) -> dict | None:
        """
        校验必填配置，子类重写此方法声明必填项

        失败时通过 _require_config 自动写入 state 错误并发送流式事件。
        成功返回校验后的值字典，供 execute() 直接使用。

        Args:
            config: 配置字典
            node_key: 节点 key
            state: 流程状态
            writer: StreamWriter

        Returns:
            校验通过的值字典，失败返回 None
        """
        return {}

    def _render_template(
        self, template: str, state: FlowState, context: Optional[dict] = None
    ) -> str:
        """渲染模板字符串

        Args:
            template: 模板字符串
            state: 流程状态
            context: 额外的变量上下文（如 input_variables 解析后的结果）
        """
        return self._resolver.render_template(template, state, context)

    def _resolve_config(
        self, config: Any, state: FlowState, context: Optional[dict] = None
    ) -> Any:
        """递归解析配置字典中所有值（模板渲染 + context 自动填充）"""
        return self._resolver.resolve_config(config, state, context)

    # ---- 变量操作 ----

    def _resolve_variable(self, source: str, state: FlowState) -> Any:
        """
        解析变量来源

        支持的格式：
        - input.xxx: 访问输入数据
        - variables.xxx: 访问流程变量
        - output.xxx: 访问输出数据

        Args:
            source: 变量来源表达式
            state: 流程状态

        Returns:
            解析后的变量值
        """
        return self._resolver.resolve(source, state)

    def _resolve_input_variables(
        self, input_variables: list[NodeVariable], state: FlowState
    ) -> dict:
        """批量解析 input_variables，返回 {name: resolved_value}"""
        context: dict[str, Any] = {}
        for var in input_variables:
            if var.name and var.source:
                context[var.name] = self._resolve_variable(var.source, state)
        return context

    @staticmethod
    def _get_output_var_names(node: FlowNode, defaults: list[str]) -> list[str]:
        """从节点配置中读取 output_variables 的名称列表，为空时回退到 defaults"""
        raw = node.base_config or {}
        output_vars = raw.get("output_variables")
        if output_vars and isinstance(output_vars, list):
            names = [
                v.get("name", "") if isinstance(v, dict) else getattr(v, "name", "")
                for v in output_vars
            ]
            names = [n for n in names if n]
            if names:
                return names
        return list(defaults)

    def _variable_exists(self, source: str, state: FlowState) -> bool:
        """
        检查变量是否存在

        Args:
            source: 变量来源表达式
            state: 流程状态

        Returns:
            变量是否存在
        """
        return self._resolver.exists(source, state)

    def _get_nested_value(self, data: dict, path: str) -> Any:
        """
        获取嵌套字典中的值

        Args:
            data: 字典数据
            path: 点分隔的路径

        Returns:
            嵌套值，不存在则返回 None
        """
        return self._resolver._get_nested_value(data, path)

    def _nested_key_exists(self, data: dict, path: str) -> bool:
        """
        检查嵌套路径是否存在

        Args:
            data: 字典数据
            path: 点分隔的路径

        Returns:
            路径是否存在
        """
        return self._resolver._nested_key_exists(data, path)

    # ---- 工具与提示词 ----

    async def get_tool(
        self, node: FlowNode
    ) -> Optional[BaseTool] | list[BaseTool] | None:
        """
        返回该节点作为工具时的定义

        Args:
            node: 节点对象

        Returns:
            单个工具、工具列表或不支持时返回 None
        """
        return None

    async def get_system_prompt_hint(self, node: FlowNode) -> Optional[str]:
        """
        返回要追加到 LLM system_prompt 的提示片段

        工具提供者节点可重写此方法，向 LLM 注入使用说明。
        LlmToolNodeHandler 会在构建 system_prompt 时自动收集
        所有已连接工具节点的 hint 并追加。

        Args:
            node: 节点对象

        Returns:
            提示文本片段，不需要时返回 None
        """
        return None

    # ---- 执行结果内容 ----

    @classmethod
    def get_input_content(
        cls,
        node: FlowNode,
        state: FlowState,
        resolver: VariableResolver,
        config: Optional[dict] = None,
    ) -> Optional[dict]:
        """
        获取节点的输入内容（用于执行结果显示）

        子类可重写此方法，返回节点执行时的输入变量信息

        Args:
            node: 节点对象
            state: 流程状态
            resolver: 变量解析器
            config: 已合并的配置（可选，若传入则直接使用，避免重复合并）

        Returns:
            输入内容字典，格式为 {变量名: 值}，默认返回 None
        """
        return None

    @classmethod
    def get_output_content(
        cls,
        node: FlowNode,
        state: FlowState,
        resolver: VariableResolver,
        config: Optional[dict] = None,
    ) -> Optional[dict]:
        """
        获取节点的输出内容（用于执行结果显示）

        子类可重写此方法，返回节点执行后的输出变量信息

        Args:
            node: 节点对象
            state: 流程状态
            resolver: 变量解析器
            config: 已合并的配置（可选，若传入则直接使用，避免重复合并）

        Returns:
            输出内容字典，格式为 {变量名: 值}，默认返回 None
        """
        return None

    # ---- 工具连接配置 ----

    @classmethod
    def allow_multiple_tool_connections(cls) -> bool:
        """
        是否允许同一类型的多个工具节点连接到同一个 LLM 节点

        默认允许。某些工具节点（如 skill）使用固定工具名，
        多实例连接会导致工具名冲突，需要返回 False 限制为单实例。

        Returns:
            True 表示允许，False 表示同一 LLM 只能连接一个该类型节点
        """
        return True

    @classmethod
    def get_tool_config(cls, node: FlowNode, config: "LlmToolConfig") -> bool:
        """
        将节点工具配置合并到 LlmToolConfig

        子类可重写此方法，将工具节点的配置添加到 LLM 工具配置中

        Args:
            node: 工具节点
            config: LLM工具配置对象（会被修改）

        Returns:
            bool: 是否处理了该节点
        """
        return False

    @classmethod
    def get_default_config(cls) -> dict:
        """
        返回节点配置的默认值字典

        基于 ConfigClass 的 field default 生成，用于 AI 创建节点时补全缺失字段。
        无 ConfigClass 的节点类型返回空字典。

        Returns:
            dict: 所有字段带默认值的配置字典
        """
        if cls.ConfigClass is not None:
            instance = cls.ConfigClass.model_construct()
            return instance.model_dump(mode="json")
        return {}

    @classmethod
    def get_config_schema(cls) -> list[dict]:
        """
        返回节点配置字段描述

        供 API 返回给前端/AI，描述 base_config 中各字段的名称、类型、是否必填等。
        子类应重写此方法返回具体的配置字段列表。

        Returns:
            配置字段列表，每项为 dict:
            {
                "name": str,           # 字段名
                "type": str,           # 类型：string/number/boolean/array/object
                "required": bool,      # 是否必填
                "description": str,    # 中文说明
                "default": Any,        # 默认值（可选）
                "options": list,       # 可选值列表（可选）
            }
        """
        if cls.ConfigClass is not None:
            return _schema_from_pydantic(cls.ConfigClass)
        return []

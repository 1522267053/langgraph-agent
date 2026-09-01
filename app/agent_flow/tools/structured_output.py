"""
JSON 结构化输出工具（structured_output 虚拟工具）

参考 langchain create_agent 的 ToolStrategy：把用户定义的字段树包装为
StructuredTool 绑定给模型，模型完成信息收集后调用该工具，其参数
（function calling 协议保证为合法 JSON）即结构化输出结果。字段树支持
object 子字段与数组元素字段（item_type=object + children），递归不限层级。

与普通工具一致走 handle_tool_calls 统一执行链路（事件展示/截断/doom loop
检测天然复用）：调用时先做必需工具清单门控，再按 Pydantic 校验参数；
校验失败返回 {"error": ...} 由模型自动修正重试，成功记录 accepted 状态
供主循环感知并终止 ReAct。
"""

import logging
from typing import Any, Callable, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

logger = logging.getLogger(__name__)

# 结构化输出虚拟工具名（必需工具清单中的固定成员）
OUTPUT_TOOL_NAME = "structured_output"

_TOOL_DESCRIPTION = (
    "输出最终结构化结果。完成全部信息收集后必须调用本工具，"
    "以参数形式给出符合字段定义的 JSON 数据；调用后任务即结束，不要再输出其他内容。"
)

# 字段 type -> Python 类型（动态构建 Pydantic 模型用）
_FIELD_TYPE_MAP: dict[str, type] = {
    "string": str,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}

# 数组元素 type 白名单（元素不支持直接嵌套数组，需要数组语义时用对象包裹其字段）
_ITEM_TYPE_MAP: dict[str, type] = {
    "string": str,
    "number": float,
    "boolean": bool,
    "object": dict,
}

_DEFAULT_TYPE = "string"
_DEFAULT_ITEM_TYPE = "string"

_ITEM_TYPE_LABELS = {
    "string": "字符串",
    "number": "数字",
    "boolean": "布尔",
    "object": "对象",
}


class StructuredOutputService:
    """structured_output 工具服务：字段表格 <-> 工具 schema/参数校验/调用状态

    一次节点执行内构建一个实例，由 llm_tool_handler 持有：
    - build_tool(): 生成绑定给模型的工具（走普通工具执行链路）
    - parse_args(): Pydantic 参数校验（工具函数体内调用）
    - build_prompt(): 生成追加到 system_prompt 的结构化输出引导
    - accepted / accepted_result: 主循环据此感知调用成功并读取结果
    """

    def __init__(self, json_fields: Any):
        """Args:
        json_fields: 字段树定义 [{name, type, description, required,
                    item_type(仅array), children(object子字段/数组元素字段)}]
        """
        self.fields = self._normalize_fields(json_fields)
        self.accepted = False
        self.accepted_result: Optional[dict] = None

    @property
    def enabled(self) -> bool:
        """字段定义是否有效（无有效字段时 JSON 模式不生效）"""
        return bool(self.fields)

    # ---- 字段表格清洗 ----

    @staticmethod
    def _normalize_fields(json_fields: Any) -> list[dict]:
        """递归过滤无效项、规范 type/item_type/children，保证 name 非空"""
        fields: list[dict] = []
        for item in json_fields or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            ftype = str(item.get("type") or _DEFAULT_TYPE).strip().lower()
            if ftype not in _FIELD_TYPE_MAP:
                ftype = _DEFAULT_TYPE
            field: dict = {
                "name": name,
                "type": ftype,
                "description": str(item.get("description") or ""),
                "required": bool(item.get("required")),
            }
            if ftype == "array":
                item_type = (
                    str(item.get("item_type") or _DEFAULT_ITEM_TYPE).strip().lower()
                )
                if item_type not in _ITEM_TYPE_MAP:
                    item_type = _DEFAULT_ITEM_TYPE
                field["item_type"] = item_type
            # children 仅在 object 或 数组元素为对象 时生效，递归清洗
            children = item.get("children")
            if isinstance(children, list) and (
                ftype == "object"
                or (ftype == "array" and field["item_type"] == "object")
            ):
                field["children"] = StructuredOutputService._normalize_fields(children)
            fields.append(field)
        return fields

    # ---- 工具构建 ----

    def build_model(self) -> Optional[type[BaseModel]]:
        """字段树 -> 动态 Pydantic 模型（无有效字段时返回 None）"""
        if not self.fields:
            return None
        return self._build_model_from_fields(self.fields, "StructuredOutput")

    @classmethod
    def _build_model_from_fields(
        cls, fields: list[dict], model_name: str
    ) -> type[BaseModel]:
        model_fields: dict[str, tuple] = {}
        for idx, f in enumerate(fields):
            pyd_type = cls._field_annotation(f, f"{model_name}_{idx}")
            if f["required"]:
                model_fields[f["name"]] = (
                    pyd_type,
                    Field(..., description=f["description"]),
                )
            else:
                model_fields[f["name"]] = (
                    Optional[pyd_type],
                    Field(default=None, description=f["description"]),
                )
        return create_model(model_name, **model_fields)

    @classmethod
    def _field_annotation(cls, field: dict, nested_model_name: str) -> Any:
        """单个字段 -> Python 类型注解（object/数组元素为对象时递归生成嵌套模型）"""
        ftype = field["type"]
        if ftype == "object":
            children = field.get("children") or []
            if children:
                return cls._build_model_from_fields(children, nested_model_name)
            return dict
        if ftype == "array":
            item_type = field.get("item_type") or _DEFAULT_ITEM_TYPE
            if item_type == "object":
                children = field.get("children") or []
                if children:
                    return list[
                        cls._build_model_from_fields(children, nested_model_name)
                    ]
                return list[dict]
            return list[_ITEM_TYPE_MAP[item_type]]
        return _FIELD_TYPE_MAP[ftype]

    def build_tool(
        self,
        get_called_tools: Optional[Callable[[], set]] = None,
        required_tools: Optional[list[str]] = None,
    ) -> StructuredTool:
        """构建 structured_output 工具（走普通工具执行链路）

        工具函数体不访问外部资源：调用时先做必需工具清单门控，再按
        Pydantic 校验参数；校验失败返回 {"error": ...} 由模型自动修正，
        成功记录 accepted 状态供主循环感知并终止 ReAct。

        Args:
            get_called_tools: 已调用工具集合的 getter（清单门控用，None 跳过门控）
            required_tools: 必需工具清单（与 get_called_tools 配合使用）
        """
        model = self.build_model()
        if model is None:
            raise ValueError("结构化输出字段定义无效，无法构建 structured_output 工具")

        def _run(**kwargs) -> dict:
            if get_called_tools is not None and required_tools:
                missing = [t for t in required_tools if t not in get_called_tools()]
                if missing:
                    return {
                        "error": (
                            f"必需工具未调用完成：{'、'.join(missing)}。"
                            "请先调用上述工具完成信息收集，再输出结构化结果。"
                        )
                    }
            parsed, error = self.parse_args(kwargs)
            if error is not None:
                return {
                    "error": (
                        f"structured_output 参数校验失败：{error}。"
                        "请修正参数后重新调用该工具。"
                    )
                }
            self.accepted = True
            self.accepted_result = parsed
            return {"structured_output": parsed}

        return StructuredTool(
            name=OUTPUT_TOOL_NAME,
            description=_TOOL_DESCRIPTION,
            args_schema=model.model_json_schema(),
            func=_run,
        )

    # ---- 参数校验（工具函数体内调用） ----

    def parse_args(self, args: Any) -> tuple[Optional[dict], Optional[str]]:
        """校验 structured_output 调用参数

        Args:
            args: 工具调用参数（应为 JSON 对象）

        Returns:
            (parsed_dict, None) 校验通过，parsed_dict 为规范化后的字段值；
            (None, error_msg) 校验失败，error_msg 为反馈给模型的错误说明
        """
        model = self.build_model()
        if model is None:
            return None, "未定义结构化输出字段"
        if not isinstance(args, dict):
            return None, "工具调用参数不是有效的 JSON 对象"
        try:
            obj = model.model_validate(args)
        except Exception as e:
            return None, f"{e}"
        return obj.model_dump(), None

    # ---- Prompt 引导 ----

    def build_prompt(self) -> str:
        """生成追加到 system_prompt 的结构化输出引导（含字段树说明）"""
        lines = self._describe_fields(self.fields, 0)
        field_desc = "\n".join(lines)
        return (
            f"\n\n# 结构化输出要求\n"
            f"完成信息收集后，你必须调用 `{OUTPUT_TOOL_NAME}` 工具输出最终结果，"
            f"其参数即为结构化 JSON 数据，调用后任务结束。字段定义：\n{field_desc}\n"
            f"不要用文字回复最终结果，最终结果只能通过该工具的参数给出。"
        )

    @staticmethod
    def _describe_fields(fields: list[dict], depth: int) -> list[str]:
        """递归缩进描述字段树（模型可感知 object/数组元素的子字段定义）"""
        lines: list[str] = []
        indent = "  " * depth
        for f in fields:
            req = "必填" if f["required"] else "可选"
            line = f"{indent}- {f['name']} ({f['type']}, {req})"
            if f["description"]:
                line += f"：{f['description']}"
            lines.append(line)
            if f["type"] == "array":
                item_type = f.get("item_type") or _DEFAULT_ITEM_TYPE
                lines.append(
                    f"{indent}  - 元素类型：{_ITEM_TYPE_LABELS.get(item_type, item_type)}"
                )
            children = f.get("children") or []
            if children:
                prefix = "元素字段" if f["type"] == "array" else "子字段"
                lines.append(f"{indent}  - {prefix}：")
                lines.extend(
                    StructuredOutputService._describe_fields(children, depth + 2)
                )
        return lines

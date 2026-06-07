"""
统一变量解析器

提供变量解析、模板渲染、配置合并等通用功能。

数据源优先级（无前缀时）：
1. context（input_variables 映射的临时变量）
2. input（state.input_data，普通嵌套 dict）
3. variables（state.variables，flat dict，key 用 . 分隔）
"""

from typing import Any, Optional
import re
import json

from simpleeval import simple_eval, EvalWithCompoundTypes

from app.agent_flow.flow_context import FlowState

_KNOWN_PREFIXES = {"input", "variables", "output", "nodes"}


class VariablePrefix:
    """变量路径前缀常量（前后端统一）"""

    INPUT = "input"
    VARIABLES = "variables"
    OUTPUT = "output"
    NODES = "nodes"


class VariableResolver:
    """
    统一的变量解析服务

    支持：
    - input.xxx: 访问输入数据
    - variables.xxx: 访问流程变量
    - output.xxx: 访问输出数据
    - nodes.xxx.yyy: 访问节点输出变量
    - xxx: 无前缀时按 context → input → variables 优先级查找
    - [n]: 数组索引语法（如 images[0].id）
    """

    # ---- 公开 API ----

    def resolve(
        self,
        source: str,
        state: "FlowState",
        context: Optional[dict] = None,
    ) -> Any:
        """解析变量路径，返回原始值

        Args:
            source: 变量路径（如 "input.name", "images[0].id", "nodes.llm_1.output"）
            state: 流程状态
            context: 额外变量上下文（input_variables 映射结果）
        """
        prefix, path = self._split_prefix(source)

        # 有已知前缀 → 直接路由到对应数据源
        if prefix in _KNOWN_PREFIXES:
            return self._resolve_by_prefix(prefix, path, source, state)

        # 无前缀 → context → input → variables
        if context is not None:
            first_key = self._first_key(source)
            if first_key in context:
                parent = context[first_key]
                remaining = source[len(first_key) :].lstrip(".")
                return (
                    self._get_nested_value(parent, remaining) if remaining else parent
                )

        if path or prefix in state.input_data:
            val = self._get_nested_value(state.input_data, source)
            if val is not None:
                return val

        return self._resolve_flat_or_nested(state.variables, source)

    def render_template(
        self,
        template: str,
        state: "FlowState",
        context: Optional[dict] = None,
    ) -> str:
        """渲染模板字符串，将 {{xxx}} 替换为解析后的值

        Args:
            template: 模板字符串（如 "你好 {{input.name}}"）
            state: 流程状态
            context: 额外变量上下文
        """

        def replace_var(match):
            var_path = match.group(1).strip()
            value = self.resolve(var_path, state, context)
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False)
            return str(value) if value is not None else ""

        return re.sub(r"\{\{([^}]+)\}\}", replace_var, template)

    def resolve_config(
        self,
        config: Any,
        state: "FlowState",
        context: Optional[dict] = None,
    ) -> Any:
        """递归解析配置字典中所有值

        - dict: 遍历值递归解析，空字符串若 context 有同名 key 则自动填充
        - list: 递归解析每个元素
        - str（非空）: render_template 渲染
        - 其他类型: 原样返回
        """
        if isinstance(config, dict):
            result = {}
            for k, v in config.items():
                if isinstance(v, str) and v == "" and context and k in context:
                    result[k] = context[k]
                else:
                    result[k] = self.resolve_config(v, state, context)
            return result
        if isinstance(config, list):
            return [self.resolve_config(item, state, context) for item in config]
        if isinstance(config, str) and config:
            return self.render_template(config, state, context)
        return config

    def exists(
        self,
        source: str,
        state: "FlowState",
        context: Optional[dict] = None,
    ) -> bool:
        """检查变量路径是否存在"""
        return self.resolve(source, state, context) is not None

    def resolve_safe(
        self,
        source: str,
        state: "FlowState",
        context: Optional[dict] = None,
        default: Any = None,
    ) -> Any:
        """安全解析变量，失败返回默认值"""
        try:
            result = self.resolve(source, state, context)
            return result if result is not None else default
        except Exception:
            return default

    def resolve_all(
        self,
        mappings: list[dict],
        state: "FlowState",
        context: Optional[dict] = None,
    ) -> dict:
        """批量解析变量映射

        Args:
            mappings: [{"name": "var1", "source": "input.xxx"}]
        """
        result = {}
        for mapping in mappings:
            name = mapping.get("name", "")
            source = mapping.get("source", "")
            if name and source:
                result[name] = self.resolve(source, state, context)
        return result

    def evaluate_condition(self, condition: dict, state: "FlowState") -> bool:
        """评估条件表达式（simpleeval）"""
        if not condition:
            return True
        condition_type = condition.get("type", "expression")
        if condition_type == "default":
            return True
        if condition_type != "expression":
            return False
        expression = condition.get("expression", "true")
        try:
            evaluator = EvalWithCompoundTypes(
                names={
                    "variables": state.variables,
                    "input": state.input_data,
                    "output": state.output_data,
                    "iteration_count": state.iteration_count,
                    "max_iterations": state.max_iterations,
                }
            )
            return bool(simple_eval(expression, evaluator))
        except Exception:
            return False

    # ---- 前缀路由 ----

    def _resolve_by_prefix(
        self, prefix: str, path: str, full_source: str, state: "FlowState"
    ) -> Any:
        """按前缀路由到对应数据源"""
        if prefix == "input":
            return (
                self._get_nested_value(state.input_data, path)
                if path
                else state.input_data
            )
        if prefix == "output":
            return (
                self._get_nested_value(state.output_data, path)
                if path
                else state.output_data
            )
        if prefix == "variables":
            return (
                self._resolve_flat_or_nested(state.variables, path)
                if path
                else state.variables
            )
        # nodes → 在 variables 中查找（nodes.xxx 前缀）
        if prefix == "nodes":
            return (
                self._resolve_flat_or_nested(state.variables, full_source)
                if path
                else {
                    k: v for k, v in state.variables.items() if k.startswith("nodes.")
                }
            )
        return None

    # ---- variables flat dict 专用 ----

    def _resolve_flat_or_nested(self, variables: dict, full_path: str) -> Any:
        """在 flat dict 中解析变量，支持 [n] 数组索引

        state.variables 是 flat dict（key 用 . 分隔如 "nodes.llm_1.output"），
        但 value 本身可能是嵌套 dict 或 list。
        先尝试 flat key 精确匹配，失败后逐段缩短做嵌套遍历。
        """
        flat_val = variables.get(full_path)
        if flat_val is not None:
            return flat_val

        tokens = self._parse_path(full_path)

        for split_idx in range(len(tokens) - 1, 0, -1):
            key_tokens = tokens[:split_idx]
            remaining_tokens = tokens[split_idx:]

            if any(isinstance(t, int) for t in key_tokens):
                continue

            flat_key = ".".join(key_tokens)
            flat_val = variables.get(flat_key)
            if flat_val is not None and isinstance(flat_val, dict | list):
                remaining = self._tokens_to_path(remaining_tokens)
                return self._get_nested_value(flat_val, remaining)

        return None

    # ---- 路径解析工具 ----

    @staticmethod
    def _split_prefix(source: str) -> tuple[str, str]:
        """拆分前缀和剩余路径

        "input.name" → ("input", "name")
        "images[0].id" → ("images[0]", "id")  — 无已知前缀时 path 保留完整
        """
        parts = source.split(".", 1)
        prefix = parts[0].split("[")[0]  # 去掉 [n] 后缀再判断前缀
        if prefix in _KNOWN_PREFIXES:
            return prefix, parts[1] if len(parts) > 1 else ""
        return "", source

    @staticmethod
    def _first_key(source: str) -> str:
        """提取路径的第一个 key（. 或 [ 之前的部分）"""
        return source.split(".")[0].split("[")[0]

    @staticmethod
    def _parse_path(path: str) -> list[str | int]:
        """将路径字符串解析为键/索引列表

        "images[0].id" → ["images", 0, "id"]
        "[1].id" → [1, "id"]
        "data.nested[1].name" → ["data", "nested", 1, "name"]
        """
        segments: list[str | int] = []
        for part in path.split("."):
            if "[" in part and part.endswith("]"):
                key = part[: part.index("[")]
                idx_str = part[part.index("[") + 1 : -1]
                if key:
                    segments.append(key)
                try:
                    segments.append(int(idx_str))
                except ValueError:
                    segments.append(idx_str)
            else:
                segments.append(part)
        return segments

    @staticmethod
    def _tokens_to_path(tokens: list[str | int]) -> str:
        """将键/索引列表还原为路径字符串

        [1, "id"] → "[1].id"
        ["images", 0, "id"] → "images[0].id"
        """
        parts: list[str] = []
        for t in tokens:
            if isinstance(t, int):
                if parts:
                    parts[-1] = f"{parts[-1]}[{t}]"
                else:
                    parts.append(f"[{t}]")
            else:
                parts.append(t)
        return ".".join(parts)

    def _get_nested_value(self, data: dict | list, path: str) -> Any:
        """获取嵌套字典/列表中的值，支持 [n] 数组索引"""
        keys = self._parse_path(path)
        value: Any = data
        for key in keys:
            if isinstance(key, int):
                if isinstance(value, list) and -len(value) <= key < len(value):
                    value = value[key]
                else:
                    return None
            elif isinstance(value, dict):
                value = value.get(key)
            else:
                return None
        return value

    def _nested_key_exists(self, data: dict | list, path: str) -> bool:
        """检查嵌套路径是否存在，支持 [n] 数组索引"""
        keys = self._parse_path(path)
        current: Any = data
        for key in keys:
            if isinstance(key, int):
                if isinstance(current, list) and -len(current) <= key < len(current):
                    current = current[key]
                else:
                    return False
            elif isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return False
        return True


variable_resolver = VariableResolver()

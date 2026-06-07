"""
控制节点处理器
包含开始、结束、条件分支三种控制节点的处理器
"""

from typing import Any, Optional
import logging
from pydantic import BaseModel, Field
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter
from app.models.flow_node import FlowNode
from app.agent_flow.flow_context import FlowState
from app.agent_flow.node_handlers.base_handler import BaseNodeHandler
from app.agent_flow.handler_registry import NodeHandlerRegistry

logger = logging.getLogger(__name__)


class StartNodeConfig(BaseModel):
    model_config = {"extra": "ignore"}
    _card_key: Optional[str] = None
    _card_input_mappings: list = []


@NodeHandlerRegistry.register("start")
class StartNodeHandler(BaseNodeHandler):
    ConfigClass = StartNodeConfig
    """
    开始节点处理器

    标记流程的起始点，记录开始节点key到流程变量。
    对于子流程的 start 节点，执行输入映射（将主流程变量映射到子流程输入）。
    """

    @classmethod
    def get_config_schema(cls) -> list[dict]:
        return [
            {
                "name": "input_variables",
                "type": "array",
                "label": "输入变量",
                "description": "流程入口的输入参数定义",
                "default": [
                    {
                        "name": "message",
                        "type": "string",
                        "description": "用户消息",
                        "required": True,
                    }
                ],
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "字段名称",
                        },
                        "type": {
                            "type": "string",
                            "description": "字段类型",
                            "default": "string",
                        },
                        "description": {
                            "type": "string",
                            "description": "字段描述",
                        },
                        "placeholder": {
                            "type": "string",
                            "description": "输入框占位提示文本",
                        },
                        "required": {
                            "type": "boolean",
                            "description": "是否必填",
                            "default": False,
                        },
                        "accept": {
                            "type": "string",
                            "description": "允许的文件类型",
                        },
                        "multiple": {
                            "type": "boolean",
                            "description": "是否允许多文件",
                            "default": False,
                        },
                        "max_size": {
                            "type": "integer",
                            "description": "最大文件大小(MB)",
                        },
                    },
                },
            }
        ]

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        state.set_variable("_start_node", node.node_key)

        cfg = self._get_config(node)
        card_key = cfg._card_key
        input_mappings = cfg._card_input_mappings

        if card_key and input_mappings:
            self._execute_input_mappings(card_key, input_mappings, state)

        return state

    @classmethod
    def get_input_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        """
        获取 start 节点的输入内容

        Returns:
            流程的初始输入数据
        """
        return state.input_data if state.input_data else None

    def _execute_input_mappings(
        self, card_key: str, input_mappings: list, state: FlowState
    ) -> None:
        """
        执行输入映射

        将主流程变量映射到子流程的输入作用域：
        variables.__card_<card_key>__input_<field>

        Args:
            card_key: CARD 节点 key
            input_mappings: 输入映射配置 [{cardField, source}]
            state: 流程状态

        Raises:
            ValueError: 源变量不存在时抛出
        """
        for mapping in input_mappings:
            card_field = mapping.get("card_field", "")
            source = mapping.get("source", "")

            if not card_field or not source:
                continue

            if not self._variable_exists(source, state):
                raise ValueError(
                    f"能力卡片[{card_key}]输入映射失败: 源变量 '{source}' 不存在"
                )

            value = self._resolve_variable(source, state)
            state.set_node_variable(card_key, f"input_{card_field}", value)


class EndNodeConfig(BaseModel):
    model_config = {"extra": "ignore"}
    _card_key: Optional[str] = None
    _card_output_mappings: list = []
    output_variables: Optional[list] = None
    output_mapping: Optional[dict] = None


@NodeHandlerRegistry.register("end")
class EndNodeHandler(BaseNodeHandler):
    ConfigClass = EndNodeConfig
    """
    结束节点处理器

    负责将流程中的变量映射到最终输出数据。
    对于子流程的 end 节点，执行输出映射（将子流程输出映射到主流程变量）。
    """

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        state.set_variable("_end_node", node.node_key)
        cfg = self._get_config(node)

        card_key = cfg._card_key
        output_mappings = cfg._card_output_mappings
        output_variables = cfg.output_variables

        if card_key and output_mappings:
            sub_flow_output = {}
            if output_variables:
                for var in output_variables:
                    name = var.get("name", "")
                    source = var.get("source", "")
                    if name and source:
                        value = self._resolve_variable(source, state)
                        sub_flow_output[name] = value

            self._execute_output_mappings(
                card_key, output_mappings, sub_flow_output, state
            )
        else:
            if output_variables:
                for var in output_variables:
                    name = var.get("name", "")
                    source = var.get("source", "")
                    if name and source:
                        value = self._resolve_variable(source, state)
                        state.output_data[name] = value
            else:
                state.output_data["variables"] = state.variables.copy()
                if cfg.output_mapping:
                    for key, mapping in cfg.output_mapping.items():
                        if mapping in state.variables:
                            state.output_data[key] = state.variables[mapping]

        return state

    @classmethod
    def get_output_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        """
        获取 end 节点的输出内容

        子流程的 end 节点：从 output_variables 解析实际输出。
        主流程的 end 节点：返回 output_data。

        Returns:
            输出内容字典
        """
        if config is None:
            config = node.base_config or {}
        card_key = config.get("_card_key")
        output_variables = config.get("output_variables")
        if card_key and output_variables:
            output = {}
            for var in output_variables:
                name = var.get("name", "")
                source = var.get("source", "")
                if name and source:
                    value = resolver.resolve(source, state)
                    if value is not None:
                        output[name] = value
            return output if output else None
        return state.output_data if state.output_data else None

    def _execute_output_mappings(
        self,
        card_key: str,
        output_mappings: list,
        sub_flow_output: dict,
        state: FlowState,
    ) -> None:
        """
        执行输出映射

        按 output_mappings 配置，将子流程输出写入父流程变量。
        兼容前端字段名 (card_field/target_variable) 和标准字段名 (source/target)。
        target_variable 为空时回退写入 nodes.{card_key}.{card_field}。

        Args:
            card_key: CARD 节点 key
            output_mappings: 输出映射配置 [{card_field, target_variable}] 或 [{source, target}]
            sub_flow_output: 子流程收集的输出 {field_name: value}
            state: 流程状态
        """
        for mapping in output_mappings:
            card_field = mapping.get("card_field", "") or mapping.get("source", "")
            target_variable = mapping.get("target_variable", "") or mapping.get(
                "target", ""
            )
            if not card_field:
                continue
            value = sub_flow_output.get(card_field)
            if value is None:
                continue
            if target_variable:
                if target_variable.startswith("variables."):
                    state.set_variable(target_variable[len("variables.") :], value)
                elif target_variable.startswith("nodes."):
                    path = target_variable[6:]
                    parts = path.split(".", 1)
                    if len(parts) == 2:
                        state.set_node_variable(parts[0], parts[1], value)
                    else:
                        state.set_variable(target_variable, value)
                else:
                    state.set_variable(target_variable, value)
            else:
                state.set_node_variable(card_key, card_field, value)


class ConditionRule(BaseModel):
    model_config = {"extra": "ignore"}
    variable: str = Field("", description="变量路径")
    operator: str = Field(
        "==",
        description="比较操作符",
        json_schema_extra={
            "options": [
                "==",
                "!=",
                ">",
                ">=",
                "<",
                "<=",
                "contains",
                "not_contains",
                "is_empty",
                "is_not_empty",
                "starts_with",
                "ends_with",
            ]
        },
    )
    value: str = Field("", description="比较值")


class ConditionConfig(BaseModel):
    model_config = {"extra": "ignore"}
    logic: str = Field("and", description="逻辑关系（and/or）")
    rules: list[ConditionRule] = Field(default=[], description="条件规则列表")


@NodeHandlerRegistry.register("condition")
class ConditionNodeHandler(BaseNodeHandler):
    ConfigClass = ConditionConfig
    """
    条件分支节点处理器
    根据条件规则决定流程走向
    支持前端配置格式：{logic: "and"|"or", rules: [{variable, operator, value}]}
    评估结果存储到 _condition_branch 变量，值为 "true" 或 "false"
    """

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        cfg = self._get_config(node)

        logic = cfg.logic
        rules = cfg.rules

        if not rules:
            state.set_variable("_condition_branch", "true")
            return state

        result = self._evaluate_rules(rules, logic, state)
        state.set_variable("_condition_branch", "true" if result else "false")
        return state

    @classmethod
    def get_input_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        """
        获取 condition 节点的输入内容

        Returns:
            条件规则和变量值
        """
        if config is None:
            config = node.base_config or {}

        input_data = {
            "logic": config.get("logic", "and"),
            "rules": config.get("rules", []),
        }

        rules = config.get("rules", [])
        for rule in rules:
            variable = rule.get("variable", "")
            if variable and resolver.exists(variable, state):
                input_data[f"var_{variable}"] = resolver.resolve(variable, state)

        return input_data if input_data else None

    @classmethod
    def get_output_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        """
        获取 condition 节点的输出内容

        Returns:
            条件判断结果
        """
        condition_branch = state.get_variable("_condition_branch")
        return {"branch": condition_branch} if condition_branch else None

    def _evaluate_rules(self, rules: list, logic: str, state: FlowState) -> bool:
        """
        评估条件规则列表

        Args:
            rules: 规则列表 [{variable, operator, value}]
            logic: 逻辑关系 "and" 或 "or"
            state: 流程状态

        Returns:
            条件评估结果
        """
        results = []

        for rule in rules:
            variable = rule.variable
            operator = rule.operator
            compare_value = rule.value

            if not variable:
                logger.warning("条件节点规则缺少 variable 字段，规则将被跳过: %s", rule)
                continue

            actual_value = self._resolve_variable(variable, state)
            rule_result = self._evaluate_rule(actual_value, operator, compare_value)
            results.append(rule_result)

        if not results:
            return True

        if logic == "or":
            return any(results)
        return all(results)

    def _evaluate_rule(
        self, actual_value: Any, operator: str, compare_value: str
    ) -> bool:
        """
        评估单个规则

        Args:
            actual_value: 实际变量值
            operator: 操作符
            compare_value: 比较值

        Returns:
            规则评估结果
        """
        try:
            # bool 类型特殊处理：兼容 "true"/"false" 字符串和布尔值
            if isinstance(actual_value, bool) or isinstance(compare_value, bool):
                bool_map = {
                    True: True,
                    False: False,
                    "true": True,
                    "false": False,
                    "True": True,
                    "False": False,
                    "1": True,
                    "0": False,
                }
                av = bool_map.get(actual_value, bool(actual_value))
                cv = bool_map.get(compare_value, bool(compare_value))
                if operator == "==":
                    return av == cv
                elif operator == "!=":
                    return av != cv
                elif operator == "is_empty":
                    return not av
                elif operator == "is_not_empty":
                    return bool(av)
                return False

            if operator == "==":
                return str(actual_value) == str(compare_value)
            elif operator == "!=":
                return str(actual_value) != str(compare_value)
            elif operator == ">":
                return float(actual_value) > float(compare_value)
            elif operator == ">=":
                return float(actual_value) >= float(compare_value)
            elif operator == "<":
                return float(actual_value) < float(compare_value)
            elif operator == "<=":
                return float(actual_value) <= float(compare_value)
            elif operator == "contains":
                return str(compare_value) in str(actual_value)
            elif operator == "not_contains":
                return str(compare_value) not in str(actual_value)
            elif operator == "is_empty":
                if actual_value is None:
                    return True
                if isinstance(actual_value, str) and actual_value == "":
                    return True
                if isinstance(actual_value, (list, dict)) and len(actual_value) == 0:
                    return True
                return False
            elif operator == "is_not_empty":
                if actual_value is None:
                    return False
                if isinstance(actual_value, str) and actual_value == "":
                    return False
                if isinstance(actual_value, (list, dict)) and len(actual_value) == 0:
                    return False
                return True
            elif operator == "starts_with":
                return str(actual_value).startswith(str(compare_value))
            elif operator == "ends_with":
                return str(actual_value).endswith(str(compare_value))
            else:
                return False
        except Exception:
            return False

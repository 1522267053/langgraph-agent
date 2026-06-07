"""
循环节点处理器

使用 LangGraph 原生子图模式：循环体构建为独立的 StateGraph 子图，
LoopNodeHandler 在 execute() 中迭代调用子图。

支持两种执行模式：
- 串行模式（concurrency=1）：逐次 ainvoke 子图
- 并发模式（concurrency>1）：asyncio.gather 并发 ainvoke 子图

支持三种循环模式：
- count: 固定次数循环
- condition: 条件表达式循环
- for_each: 遍历数组循环

自动注入循环变量：loop_index（当前索引）、loop_count（总次数）、loop_item（当前元素）
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from simpleeval import simple_eval, EvalWithCompoundTypes
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter

from app.models.flow_node import FlowNode
from app.agent_flow.flow_context import FlowState
from app.agent_flow.node_handlers.base_handler import BaseNodeHandler
from app.agent_flow.variable_resolver import VariableResolver
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.execution_context import get_execution_context
from app.agent_flow.loop_subgraph import LoopSubgraphBuilder
from app.agent_flow.subgraph_runner import SubgraphRunner

logger = logging.getLogger(__name__)


class LoopNodeConfig(BaseModel):
    model_config = {"extra": "ignore"}
    loop_mode: str = Field(
        "count",
        description="循环模式",
        json_schema_extra={"options": ["count", "condition", "for_each"]},
    )
    max_count: int = Field(10, description="固定次数循环的最大次数")
    condition_expression: str = Field(
        "", description="条件表达式（condition 模式下生效）"
    )
    for_each_source: str = Field(
        "", description="数组来源变量路径（for_each 模式下生效）"
    )
    for_each_item_type: Optional[str] = Field(
        None, description="遍历元素类型（for_each 模式下生效）"
    )
    break_on_error: bool = Field(True, description="迭代出错时是否中断循环")
    concurrency: int = Field(1, description="并发执行数（1 为串行）")
    input_mappings: list = Field(default=[], description="输入映射列表")
    output_variables: list[dict] = Field(default=[], description="输出变量列表")


@NodeHandlerRegistry.register("loop")
class LoopNodeHandler(BaseNodeHandler):
    ConfigClass = LoopNodeConfig
    """
    循环节点处理器

    构建循环体子图，按配置的循环模式和执行模式迭代执行。
    """

    def check_config(
        self,
        config: dict,
        node_key: str,
        state: FlowState,
        writer: Optional[StreamWriter] = None,
    ) -> dict | None:
        """校验循环节点必填配置"""
        loop_mode = config.get("loop_mode", "count")
        max_count = config.get("max_count", 10)
        if isinstance(max_count, str):
            try:
                max_count = int(max_count)
            except (ValueError, TypeError):
                max_count = 10

        condition_expression = config.get("condition_expression", "")
        for_each_source = config.get("for_each_source", "")

        if loop_mode == "condition":
            result = self._require_config(
                config, "condition_expression", node_key, "条件表达式", state, writer
            )
            if not result:
                return None
            condition_expression = result

        if loop_mode == "for_each":
            result = self._require_config(
                config, "for_each_source", node_key, "数组来源", state, writer
            )
            if not result:
                return None
            for_each_source = result

        return {
            "loop_mode": loop_mode,
            "max_count": max_count,
            "condition_expression": condition_expression,
            "for_each_source": for_each_source,
            "for_each_item_type": config.get("for_each_item_type"),
            "break_on_error": config.get("break_on_error", True),
            "concurrency": config.get("concurrency", 1),
            "input_mappings": config.get("input_mappings", []),
        }

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        loop_key = node.node_key

        checked = self.check_config(node.base_config or {}, loop_key, state, writer)
        if not checked:
            return state

        loop_mode = checked["loop_mode"]
        max_count = checked["max_count"]
        condition_expression = checked["condition_expression"]
        for_each_source = checked["for_each_source"]
        break_on_error = checked["break_on_error"]
        concurrency = checked["concurrency"]
        input_mappings = checked["input_mappings"]
        is_concurrent = concurrency > 1

        loop_prefix = f"{loop_key}__"

        ctx = get_execution_context()
        if not ctx or not ctx.expanded_flow:
            state.add_error(loop_key, "循环执行上下文未初始化")
            return state

        # ---- 收集子图节点和边 ----
        sub_nodes = [
            n for n in ctx.expanded_flow.nodes if n.node_key.startswith(loop_prefix)
        ]
        sub_edges = [
            e
            for e in ctx.expanded_flow.edges
            if hasattr(e, "source_node_key")
            and e.source_node_key.startswith(loop_prefix)
            and hasattr(e, "target_node_key")
            and e.target_node_key.startswith(loop_prefix)
        ]

        if not sub_nodes:
            state.add_error(loop_key, f"循环节点 {loop_key} 没有子图节点")
            return state

        # ---- 构建迭代列表 ----
        iteration_items: List[Any] = []
        if loop_mode == "for_each" and for_each_source:
            loop_array = self._resolve_variable(for_each_source, state)
            if isinstance(loop_array, (list, tuple)):
                iteration_items = list(loop_array)
            else:
                logger.warning(
                    f"循环节点[{loop_key}]的 for_each_source '{for_each_source}' 不是数组类型: {type(loop_array)}"
                )
        elif loop_mode == "condition" and condition_expression:
            iteration_items = self._resolve_condition_items(
                condition_expression, state, max_count
            )
        else:
            iteration_items = list(range(max_count))

        total_count = len(iteration_items)

        # ---- 构建循环体子图 ----
        llm_handler = ctx.handler_map.get("llm")
        loop_name = node.node_name or loop_key
        parent_path = (
            f"{ctx.parent_path} > {loop_name}" if ctx.parent_path else loop_name
        )
        subgraph = LoopSubgraphBuilder(
            parent_key=loop_key,
            sub_nodes=sub_nodes,
            sub_edges=sub_edges,
            handler_map=ctx.handler_map,
            llm_handler=llm_handler,
            parent_node_name=loop_name,
            parent_path=parent_path,
        ).build()

        # ---- 执行迭代 ----
        if is_concurrent:
            results = await self._execute_concurrent(
                subgraph,
                loop_key,
                iteration_items,
                input_mappings,
                break_on_error,
                concurrency,
                state,
                config,
                writer,
            )
        else:
            results = await self._execute_sequential(
                subgraph,
                loop_key,
                iteration_items,
                input_mappings,
                break_on_error,
                state,
                config,
                writer,
            )

        # ---- 收集输出 ----
        state.set_variable("loop_index", total_count - 1)
        state.set_variable("loop_count", total_count)
        state.set_variable("loop_item", None)

        aggregated: Dict[str, List[Any]] = {}
        for r in results:
            if r is None or "error" in r:
                continue
            for key, value in r.items():
                aggregated.setdefault(key, []).append(value)

        for key, values in aggregated.items():
            state.set_node_variable(loop_key, key, values)

        return state

    async def _execute_sequential(
        self,
        subgraph,
        loop_key: str,
        items: List[Any],
        input_mappings: list,
        break_on_error: bool,
        state: FlowState,
        config: Optional[RunnableConfig],
        writer: Optional[StreamWriter],
    ) -> List[dict]:
        """串行模式：逐次流式调用子图"""
        results: List[dict] = []
        total_count = len(items)
        ctx = get_execution_context()
        execution_id = ctx.execution_id if ctx else 0

        for i, item in enumerate(items):
            state.set_variable("loop_index", i)
            state.set_variable("loop_count", total_count)
            state.set_variable("loop_item", item)

            if input_mappings:
                self._execute_input_mappings(loop_key, input_mappings, state)

            try:
                if writer:
                    writer(
                        {
                            "type": "loop_iteration_start",
                            "node_key": loop_key,
                            "loop_index": i,
                            "loop_count": total_count,
                        }
                    )

                result = await SubgraphRunner.stream(
                    subgraph,
                    state,
                    config,
                    writer,
                    execution_id,
                    node_name_formatter=lambda e: (
                        f"{e.get('node_name', '')}"
                        f"({e.get('parent_path', '')}，"
                        f"迭代{e.get('loop_index', 0) + 1})"
                        if e.get("parent_path")
                        else e.get("node_name", "")
                    ),
                )

                if writer:
                    writer(
                        {
                            "type": "loop_iteration_done",
                            "node_key": loop_key,
                            "loop_index": i,
                            "loop_count": total_count,
                        }
                    )

                output = self._collect_output(loop_key, result, i)
                results.append(output)

                if isinstance(result, dict):
                    if "variables" in result:
                        state.variables.update(result["variables"])
                    if "output_data" in result:
                        state.output_data.update(result["output_data"])
                    if "conversation_messages" in result:
                        state.conversation_messages.update(
                            result["conversation_messages"]
                        )

            except Exception as exc:
                logger.error(
                    f"循环节点[{loop_key}]迭代 {i}/{total_count} 执行失败: {exc}"
                )
                results.append({"error": str(exc), "loop_index": i})

                if break_on_error:
                    state.add_error(
                        loop_key,
                        f"迭代 {i} 执行失败（已中止）: {exc}",
                    )
                    break

        return results

    async def _execute_concurrent(
        self,
        subgraph,
        loop_key: str,
        items: List[Any],
        input_mappings: list,
        break_on_error: bool,
        concurrency: int,
        state: FlowState,
        config: Optional[RunnableConfig],
        writer: Optional[StreamWriter],
    ) -> List[dict]:
        """并发模式：asyncio.gather 并发流式调用子图"""
        total_count = len(items)
        semaphore = asyncio.Semaphore(concurrency)
        results: List[dict] = [None] * total_count
        failed = asyncio.Event()
        ctx = get_execution_context()
        execution_id = ctx.execution_id if ctx else 0

        if input_mappings:
            self._execute_input_mappings(loop_key, input_mappings, state)

        async def run_iteration(index: int, item: Any) -> None:
            if break_on_error and failed.is_set():
                return
            async with semaphore:
                if break_on_error and failed.is_set():
                    return
                try:
                    iter_state = state.model_copy(deep=True)
                    iter_state.set_variable("loop_index", index)
                    iter_state.set_variable("loop_count", total_count)
                    iter_state.set_variable("loop_item", item)
                    iter_state.errors = []

                    result = await SubgraphRunner.stream(
                        subgraph,
                        iter_state,
                        config,
                        writer,
                        execution_id,
                        node_name_formatter=lambda e: (
                            f"{e.get('node_name', '')}"
                            f"({e.get('parent_path', '')}，"
                            f"迭代{e.get('loop_index', 0) + 1})"
                            if e.get("parent_path")
                            else e.get("node_name", "")
                        ),
                    )
                    output = self._collect_output(loop_key, result, index)
                    results[index] = output

                except Exception as exc:
                    logger.error(
                        f"循环节点[{loop_key}]并发迭代 {index} 执行失败: {exc}"
                    )
                    results[index] = {
                        "loop_index": index,
                        "error": str(exc),
                    }
                    if break_on_error:
                        failed.set()

        await asyncio.gather(
            *(run_iteration(i, item) for i, item in enumerate(items)),
            return_exceptions=False,
        )

        return results

    def _collect_output(
        self, loop_key: str, subgraph_result, loop_index: int = 0
    ) -> dict:
        """从子图结果中收集输出变量，自动处理迭代后缀"""
        output: Dict[str, Any] = {}

        ctx = get_execution_context()
        if not ctx or not ctx.expanded_flow:
            return output

        loop_prefix = f"{loop_key}__"
        end_node = None
        for n in ctx.expanded_flow.nodes:
            if n.node_key.startswith(loop_prefix) and n.node_type == "end":
                end_node = n
                break

        if not end_node:
            return output

        output_variables = (end_node.base_config or {}).get("output_variables", [])
        if not output_variables:
            return output

        if isinstance(subgraph_result, dict):
            result_vars = subgraph_result.get("variables", {})
            result_output = subgraph_result.get("output_data", {})
        else:
            result_vars = getattr(subgraph_result, "variables", {})
            result_output = getattr(subgraph_result, "output_data", {})

        for var in output_variables:
            name = var.get("name", "")
            source = var.get("source", "")
            if not name or not source:
                continue

            value = None

            if source.startswith("nodes."):
                parts = source.split(".", 2)
                if len(parts) >= 2:
                    if parts[1].startswith(loop_prefix):
                        prefixed_key = parts[1]
                    else:
                        prefixed_key = f"{loop_prefix}{parts[1]}"
                    iter_source = (
                        f"nodes.{prefixed_key}_iter_{loop_index}.{parts[2]}"
                        if len(parts) > 2
                        else f"nodes.{prefixed_key}_iter_{loop_index}"
                    )
                    value = result_vars.get(iter_source)
                if value is None:
                    value = result_vars.get(source)
            elif source.startswith("variables."):
                key = source[len("variables.") :]
                value = result_vars.get(key)
            elif source.startswith("input."):
                value = result_vars.get(source)
            else:
                value = result_vars.get(source)

            if value is None:
                value = result_output.get(name)

            if value is not None:
                output[name] = value

        return output

    def _execute_input_mappings(
        self, loop_key: str, input_mappings: list, state: FlowState
    ) -> None:
        """执行输入映射，将主流程变量映射到子流程输入作用域

        兼容 card_field 和 name 两种字段名。
        """
        for mapping in input_mappings:
            card_field = mapping.get("card_field") or mapping.get("name") or ""
            source = mapping.get("source", "")

            if not card_field or not source:
                continue

            if not self._variable_exists(source, state):
                raise ValueError(
                    f"循环节点[{loop_key}]输入映射失败: 源变量 '{source}' 不存在"
                )

            value = self._resolve_variable(source, state)
            state.set_node_variable(loop_key, f"input_{card_field}", value)

    def _resolve_condition_items(
        self, expression: str, state: FlowState, max_count: int
    ) -> List[int]:
        """评估条件表达式，返回可迭代的索引列表（最多 max_count 次）"""
        items = []
        for i in range(max_count):
            try:
                evaluator = EvalWithCompoundTypes(
                    names={
                        "variables": state.variables,
                        "input": state.input_data,
                        "output": state.output_data,
                        "loop_index": i,
                        "loop_count": max_count,
                        "loop_item": None,
                    }
                )
                if not bool(simple_eval(expression, evaluator)):
                    break
            except Exception:
                logger.warning(f"循环节点条件表达式评估失败（迭代 {i}）: {expression}")
                break
            items.append(i)
        return items

    @classmethod
    def get_input_content(
        cls,
        node: FlowNode,
        state: FlowState,
        resolver: VariableResolver,
        config: Optional[dict] = None,
    ) -> Optional[dict]:
        """获取循环节点的输入内容（循环模式、输入映射解析后的值）"""
        if config is None:
            config = node.base_config or {}

        loop_mode = config.get("loop_mode", "count")
        content: Dict[str, Any] = {"loop_mode": loop_mode}

        if loop_mode == "count":
            content["max_count"] = config.get("max_count", 10)
        elif loop_mode == "condition":
            content["condition_expression"] = config.get("condition_expression", "")
        elif loop_mode == "for_each":
            source = config.get("for_each_source", "")
            content["for_each_source"] = source
            if source:
                try:
                    value = resolver.resolve(source, state)
                    content["for_each_array"] = (
                        value if isinstance(value, (list, tuple)) else None
                    )
                except Exception:
                    content["for_each_array"] = None

        input_mappings = config.get("input_mappings", [])
        if input_mappings:
            mapped: Dict[str, Any] = {}
            for mapping in input_mappings:
                card_field = mapping.get("card_field") or mapping.get("name") or ""
                source = mapping.get("source", "")
                if not card_field or not source:
                    continue
                try:
                    value = resolver.resolve(source, state)
                    mapped[card_field] = value
                except Exception:
                    mapped[card_field] = None
            content["input_mappings"] = mapped

        return content

    @classmethod
    def get_output_content(
        cls,
        node: FlowNode,
        state: FlowState,
        resolver: VariableResolver,
        config: Optional[dict] = None,
    ) -> Optional[dict]:
        """获取循环节点的输出内容（从 state.variables 的 nodes.{loopKey} 命名空间读取）"""
        node_key = node.node_key
        prefix = f"nodes.{node_key}."
        output: Dict[str, Any] = {}

        for key, value in state.variables.items():
            if key.startswith(prefix):
                var_name = key[len(prefix) :]
                if not var_name.startswith("input_"):
                    output[var_name] = value

        return output if output else None

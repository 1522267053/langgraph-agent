"""
能力卡片节点处理器

运行时动态加载引用的外部流程，构建为独立 StateGraph 子图执行。
支持嵌套引用（卡片内引用其他卡片）和嵌套循环节点。
"""

import copy
import logging
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter

from app.config.database import AsyncSessionLocal
from app.models.flow_edge import FlowEdge
from app.models.flow_node import FlowNode, NodeType
from app.agent_flow.execution_context import (
    ExecutionContext,
    execution_context_scope,
    get_execution_context,
)
from app.agent_flow.flow_context import FlowState
from app.agent_flow.card_subgraph import CardSubgraphBuilder
from app.agent_flow.subgraph_runner import SubgraphRunner
from app.agent_flow.node_handlers.base_handler import BaseNodeHandler
from app.agent_flow.variable_resolver import variable_resolver
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.services.flow_service import flow_service

logger = logging.getLogger(__name__)


class CardNodeConfig(BaseModel):
    model_config = {"extra": "ignore"}
    input_mappings: list = []
    output_mappings: list = []


@NodeHandlerRegistry.register("card")
class CardNodeHandler(BaseNodeHandler):
    ConfigClass = CardNodeConfig
    """
    能力卡片节点处理器

    运行时加载 ref_flow_id 对应的外部流程，重命名子流程节点/边，
    注入输入输出映射，构建独立 StateGraph 子图并执行。
    """

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        card_key = node.node_key
        cfg = self._get_config(node)
        ref_flow_id = node.ref_flow_id

        if not ref_flow_id:
            state.add_error(card_key, "能力卡片节点未指定引用的流程")
            return state

        # ---- 加载引用流程 ----
        async with AsyncSessionLocal() as db:
            sub_flow = await flow_service.get_with_nodes_and_edges(db, ref_flow_id)
        if not sub_flow:
            state.add_error(card_key, f"引用的流程不存在: {ref_flow_id}")
            return state

        # ---- 递归展开子流程中的卡片节点 ----
        expanded = await self._expand_sub_flow_cards(sub_flow, visited={sub_flow.id})

        # ---- 重命名子流程节点/边（card_key__ 前缀）----
        input_mappings = cfg.input_mappings
        output_mappings = cfg.output_mappings
        renamed = self._rename_sub_flow(
            expanded["nodes"],
            expanded["edges"],
            card_key,
            input_mappings,
            output_mappings,
            sub_flow,
        )
        sub_nodes = renamed["nodes"]
        sub_edges = renamed["edges"]

        # ---- 构建子图执行上下文 ----
        ctx = get_execution_context()
        if not ctx:
            state.add_error(card_key, "执行上下文未初始化")
            return state

        llm_handler = ctx.handler_map.get("llm")
        card_name = node.node_name or card_key
        parent_path = (
            f"{ctx.parent_path} > {card_name}" if ctx.parent_path else card_name
        )
        subgraph = CardSubgraphBuilder(
            parent_key=card_key,
            sub_nodes=sub_nodes,
            sub_edges=sub_edges,
            handler_map=ctx.handler_map,
            llm_handler=llm_handler,
            parent_node_name=card_name,
            parent_path=parent_path,
        ).build()

        # ---- 创建子流程执行上下文（支持嵌套循环/卡片）----
        card_ctx = ExecutionContext()
        card_ctx.handler_map = ctx.handler_map
        card_ctx.llm_kwargs = ctx.llm_kwargs
        card_ctx.expanded_flow = type(
            "CardExpandedFlow",
            (),
            {
                "id": sub_flow.id,
                "name": sub_flow.name,
                "nodes": sub_nodes,
                "edges": sub_edges,
                "input_schema": sub_flow.input_schema,
                "output_schema": sub_flow.output_schema,
            },
        )()
        card_ctx.execution_id = ctx.execution_id
        card_ctx.parent_path = parent_path

        # ---- 执行输入映射 ----
        self._execute_input_mappings(card_key, input_mappings, state)

        # ---- 在子流程上下文中执行子图 ----
        if writer:
            writer(
                {
                    "type": "card_start",
                    "node_key": card_key,
                    "node_type": "card",
                    "node_name": node.node_name or card_key,
                    "card_node_name": node.node_name or card_key,
                    "ref_flow_id": ref_flow_id,
                    "ref_flow_name": sub_flow.name or "",
                }
            )

        try:
            async with execution_context_scope(card_ctx):
                result = await SubgraphRunner.stream(
                    subgraph,
                    state,
                    config,
                    writer,
                    ctx.execution_id,
                    node_name_formatter=lambda e: (
                        f"{e.get('node_name', '')}({e.get('parent_path', '')})"
                        if e.get("parent_path")
                        else e.get("node_name", "")
                    ),
                    warn_no_values=True,
                )
        except Exception as exc:
            logger.error(f"能力卡片[{card_key}]执行失败: {exc}")
            state.add_error(card_key, f"能力卡片执行失败: {exc}")
            if writer:
                writer(
                    {
                        "type": "card_done",
                        "node_key": card_key,
                        "node_type": "card",
                        "error": str(exc),
                    }
                )
            return state

        # ---- 收集输出 ----
        output = self._collect_output(card_key, result, sub_nodes)
        for key, value in output.items():
            state.set_node_variable(card_key, key, value)

        if writer:
            writer(
                {
                    "type": "card_done",
                    "node_key": card_key,
                    "node_type": "card",
                    "output_data": output if output else None,
                }
            )

        # 合并子图状态到父状态
        if isinstance(result, dict):
            if "variables" in result:
                state.variables.update(result["variables"])
            if "output_data" in result:
                state.output_data.update(result["output_data"])
            if "conversation_messages" in result:
                state.conversation_messages.update(result["conversation_messages"])

        return state

    async def _expand_sub_flow_cards(
        self, flow: Any, visited: set[int]
    ) -> Dict[str, List]:
        """递归展开子流程中的卡片节点，并重写引用卡片节点的边"""
        expanded_nodes: List[FlowNode] = []
        expanded_edges: List[FlowEdge] = []
        card_expansion_map: Dict[str, Dict[str, str]] = {}

        for sub_node in flow.nodes:
            if sub_node.node_type == NodeType.CARD.value and sub_node.ref_flow_id:
                if sub_node.ref_flow_id in visited:
                    continue
                visited.add(sub_node.ref_flow_id)

                async with AsyncSessionLocal() as db:
                    inner_flow = await flow_service.get_with_nodes_and_edges(
                        db, sub_node.ref_flow_id
                    )
                if not inner_flow:
                    continue

                inner_expanded = await self._expand_sub_flow_cards(
                    inner_flow, visited.copy()
                )
                card_config = sub_node.base_config or {}
                inner_input = card_config.get("input_mappings", [])
                inner_output = card_config.get("output_mappings", [])
                renamed = self._rename_sub_flow(
                    inner_expanded["nodes"],
                    inner_expanded["edges"],
                    sub_node.node_key,
                    inner_input,
                    inner_output,
                    inner_flow,
                )
                expanded_nodes.extend(renamed["nodes"])
                expanded_edges.extend(renamed["edges"])

                inner_start = next(
                    (
                        n
                        for n in renamed["nodes"]
                        if n.node_type == NodeType.START.value
                    ),
                    None,
                )
                inner_end = next(
                    (n for n in renamed["nodes"] if n.node_type == NodeType.END.value),
                    None,
                )
                card_expansion_map[sub_node.node_key] = {
                    "start": inner_start.node_key
                    if inner_start
                    else f"{sub_node.node_key}__start",
                    "end": inner_end.node_key
                    if inner_end
                    else f"{sub_node.node_key}__end",
                }
            else:
                expanded_nodes.append(sub_node)

        for edge in flow.edges:
            source = edge.source_node_key
            target = edge.target_node_key

            if source in card_expansion_map:
                source = card_expansion_map[source]["end"]
            if target in card_expansion_map:
                target = card_expansion_map[target]["start"]

            expanded_edges.append(
                FlowEdge(
                    flow_id=edge.flow_id,
                    source_node_key=source,
                    target_node_key=target,
                    source_handle=edge.source_handle,
                    target_handle=edge.target_handle,
                    condition=edge.condition,
                    label=edge.label,
                )
            )

        return {"nodes": expanded_nodes, "edges": expanded_edges}

    def _rename_sub_flow(
        self,
        nodes: List[FlowNode],
        edges: List[FlowEdge],
        card_key: str,
        input_mappings: list,
        output_mappings: list,
        sub_flow: Any,
    ) -> Dict[str, List]:
        """重命名子流程节点/边，注入输入输出映射"""
        sub_start_node = None
        sub_end_node = None
        sub_other_nodes: List[FlowNode] = []

        for n in nodes:
            if n.node_type == NodeType.START.value and "__" not in n.node_key:
                sub_start_node = n
            elif n.node_type == NodeType.END.value and "__" not in n.node_key:
                sub_end_node = n
            else:
                sub_other_nodes.append(n)

        expanded_nodes: List[FlowNode] = []
        expanded_edges: List[FlowEdge] = []

        # ---- 替换 start 节点 ----
        if sub_start_node:
            new_start_node = FlowNode(
                flow_id=sub_flow.id if hasattr(sub_flow, "id") else 0,
                node_type=NodeType.START.value,
                node_key=f"{card_key}__{sub_start_node.node_key}",
                node_name=f"[输入映射] {sub_start_node.node_name or '开始'}",
                position_x=sub_start_node.position_x,
                position_y=sub_start_node.position_y,
                base_config={
                    "_card_key": card_key,
                    "_card_input_mappings": input_mappings,
                    "_sub_flow_input_schema": sub_flow.input_schema,
                },
                ref_flow_id=None,
            )
            expanded_nodes.append(new_start_node)

        # ---- 替换 end 节点 ----
        if sub_end_node:
            original_output_vars = (sub_end_node.base_config or {}).get(
                "output_variables", []
            )
            rewritten_output_vars = []
            for var in original_output_vars:
                source = var.get("source", "")
                new_var = dict(var)
                if source.startswith("nodes."):
                    parts = source.split(".", 2)
                    if len(parts) >= 2:
                        parts[1] = f"{card_key}__{parts[1]}"
                        new_var["source"] = ".".join(parts)
                elif source.startswith("input."):
                    field = source[len("input.") :]
                    new_var["source"] = f"nodes.{card_key}.input_{field}"
                rewritten_output_vars.append(new_var)

            new_end_node = FlowNode(
                flow_id=sub_flow.id if hasattr(sub_flow, "id") else 0,
                node_type=NodeType.END.value,
                node_key=f"{card_key}__{sub_end_node.node_key}",
                node_name=f"[输出映射] {sub_end_node.node_name or '结束'}",
                position_x=sub_end_node.position_x,
                position_y=sub_end_node.position_y,
                base_config={
                    "_card_key": card_key,
                    "_card_output_mappings": output_mappings,
                    "output_variables": rewritten_output_vars,
                },
                ref_flow_id=None,
            )
            expanded_nodes.append(new_end_node)

        # ---- 先构建完整映射，再统一重写 config（避免节点顺序依赖）----
        node_key_mapping: Dict[str, str] = {}
        for sub_node in sub_other_nodes:
            node_key_mapping[sub_node.node_key] = f"{card_key}__{sub_node.node_key}"

        for sub_node in sub_other_nodes:
            new_key = node_key_mapping[sub_node.node_key]

            new_node = FlowNode(
                flow_id=sub_flow.id if hasattr(sub_flow, "id") else 0,
                node_type=sub_node.node_type,
                node_key=new_key,
                node_name=sub_node.node_name,
                position_x=sub_node.position_x,
                position_y=sub_node.position_y,
                base_config=self._rewrite_variable_refs(
                    sub_node.base_config, card_key, node_key_mapping
                ),
                ref_flow_id=sub_node.ref_flow_id,
            )
            expanded_nodes.append(new_node)

        # ---- 重命名边 ----
        for sub_edge in edges:
            old_source = sub_edge.source_node_key
            old_target = sub_edge.target_node_key

            if sub_start_node and old_source == sub_start_node.node_key:
                new_source = f"{card_key}__{sub_start_node.node_key}"
            else:
                new_source = node_key_mapping.get(old_source, old_source)

            if sub_end_node and old_target == sub_end_node.node_key:
                new_target = f"{card_key}__{sub_end_node.node_key}"
            else:
                new_target = node_key_mapping.get(old_target, old_target)

            expanded_edges.append(
                FlowEdge(
                    flow_id=sub_flow.id if hasattr(sub_flow, "id") else 0,
                    source_node_key=new_source,
                    target_node_key=new_target,
                    source_handle=sub_edge.source_handle,
                    target_handle=sub_edge.target_handle,
                    condition=sub_edge.condition,
                    label=sub_edge.label,
                )
            )

        return {"nodes": expanded_nodes, "edges": expanded_edges}

    _SKIP_KEYS = frozenset(
        {
            "_card_key",
            "_card_input_mappings",
            "_card_output_mappings",
            "_sub_flow_input_schema",
        }
    )
    _INPUT_PATTERN = re.compile(r"\binput\.(\w+(?:\.\w+)*)\b")

    @classmethod
    def _rewrite_variable_refs(
        cls,
        config: Optional[dict],
        card_key: str,
        node_key_mapping: Optional[Dict[str, str]] = None,
    ) -> Optional[dict]:
        """递归重写 config 中变量引用: input.xxx → nodes.{card_key}.input_xxx, nodes.{old}.xxx → nodes.{new}.xxx"""
        if not config:
            return config
        rewritten = copy.deepcopy(config)
        cls._rewrite_input_refs(rewritten, card_key)
        if node_key_mapping:
            cls._rewrite_nodes_refs(rewritten, node_key_mapping)
        return rewritten

    @classmethod
    def _rewrite_input_refs(cls, obj: Any, card_key: str) -> None:
        """递归扫描并重写所有字符串中的 input.xxx 引用"""
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in cls._SKIP_KEYS:
                    continue
                if isinstance(value, str):
                    obj[key] = cls._INPUT_PATTERN.sub(
                        f"nodes.{card_key}.input_\\1", value
                    )
                else:
                    cls._rewrite_input_refs(value, card_key)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, str):
                    obj[i] = cls._INPUT_PATTERN.sub(f"nodes.{card_key}.input_\\1", item)
                else:
                    cls._rewrite_input_refs(item, card_key)

    @classmethod
    def _rewrite_nodes_refs(cls, obj: Any, node_key_mapping: Dict[str, str]) -> None:
        """递归扫描并重写所有字符串中的 nodes.{old_key}. 引用为 nodes.{new_key}."""
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in cls._SKIP_KEYS:
                    continue
                if isinstance(value, str):
                    obj[key] = cls._apply_nodes_mapping(value, node_key_mapping)
                else:
                    cls._rewrite_nodes_refs(value, node_key_mapping)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, str):
                    obj[i] = cls._apply_nodes_mapping(item, node_key_mapping)
                else:
                    cls._rewrite_nodes_refs(item, node_key_mapping)

    @classmethod
    def _apply_nodes_mapping(cls, text: str, node_key_mapping: Dict[str, str]) -> str:
        """将字符串中 nodes.{old_key}. 替换为 nodes.{new_key}.（按 key 长度降序避免部分匹配）"""
        for old_key in sorted(node_key_mapping, key=len, reverse=True):
            new_key = node_key_mapping[old_key]
            text = text.replace(f"nodes.{old_key}.", f"nodes.{new_key}.")
        return text

    def _execute_input_mappings(
        self, card_key: str, input_mappings: list, state: FlowState
    ) -> None:
        """执行输入映射：将父流程变量写入卡片命名空间"""
        for mapping in input_mappings:
            card_field = mapping.get("card_field", "")
            source = mapping.get("source", "")
            if not card_field or not source:
                continue
            if not self._variable_exists(source, state):
                state.add_error(card_key, f"输入映射失败: 源变量 '{source}' 不存在")
                continue
            value = self._resolve_variable(source, state)
            state.set_node_variable(card_key, f"input_{card_field}", value)

    def _collect_output(
        self, card_key: str, subgraph_result, sub_nodes: List[FlowNode]
    ) -> Dict[str, Any]:
        """从子图结果中收集输出变量"""
        output: Dict[str, Any] = {}

        end_node = None
        for n in sub_nodes:
            if n.node_type == NodeType.END.value:
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

        result_state = FlowState.model_validate(
            {
                "variables": result_vars,
                "output_data": result_output,
                "input_data": {},
            }
        )

        for var in output_variables:
            name = var.get("name", "")
            source = var.get("source", "")
            if not name or not source:
                continue

            value = variable_resolver.resolve(source, result_state)
            if value is None:
                value = result_vars.get(source)
            if value is None:
                value = result_output.get(name)

            output[name] = value

        return output

    @classmethod
    def get_input_content(
        cls,
        node: FlowNode,
        state: FlowState,
        resolver,
        config: Optional[dict] = None,
    ) -> Optional[dict]:
        """获取能力卡片节点的输入内容（input_mappings 解析后的值）"""
        if config is None:
            config = node.base_config or {}

        input_mappings = config.get("input_mappings", [])
        if not input_mappings:
            return None

        content: Dict[str, Any] = {}
        for mapping in input_mappings:
            card_field = mapping.get("card_field", "")
            source = mapping.get("source", "")
            if not card_field or not source:
                continue
            try:
                value = resolver.resolve(source, state)
                content[card_field] = value
            except Exception:
                content[card_field] = None

        return content if content else None

    @classmethod
    def get_output_content(
        cls,
        node: FlowNode,
        state: FlowState,
        resolver,
        config: Optional[dict] = None,
    ) -> Optional[dict]:
        """获取能力卡片节点的输出内容（从 nodes.{cardKey} 命名空间读取，排除 input_ 前缀）"""
        node_key = node.node_key
        prefix = f"nodes.{node_key}."
        output: Dict[str, Any] = {}

        for key, value in state.variables.items():
            if key.startswith(prefix):
                var_name = key[len(prefix) :]
                if not var_name.startswith("input_"):
                    output[var_name] = value

        return output if output else None

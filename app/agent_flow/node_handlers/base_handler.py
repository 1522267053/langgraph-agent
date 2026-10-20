"""
节点处理器基类
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from abc import ABC, abstractmethod
from typing import Optional, Any, TYPE_CHECKING, Sequence

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.types import StreamWriter
from pydantic import BaseModel, Field

from app.models.flow_node import FlowNode
from app.agent_flow.flow_event import ErrorEvent
from app.agent_flow.execution_context import get_execution_context
from app.agent_flow.variable_resolver import VariableResolver, variable_resolver
from app.services.interrupt_service import interrupt_service

if TYPE_CHECKING:
    from app.agent_flow.deferred_tools import ToolProvider
    from app.agent_flow.flow_event import FlowEvent
    from app.agent_flow.tool_resolver import LlmToolConfig
    from app.agent_flow.flow_context import FlowState

logger = logging.getLogger(__name__)

# ---- 公共数据模型 ----


class NodeVariable(BaseModel):
    """节点变量（输入/输出通用，与前端对齐）"""

    name: str = Field(default="", description="变量名")
    source: str = Field(default="", description="来源路径")
    type: Optional[str] = Field(
        default=None,
        description="数据类型(string/number/boolean/object/array/file_list)",
    )


class BaseNodeConfig(BaseModel):
    """节点配置基类，提供输入输出变量的公共字段"""

    model_config = {"extra": "ignore"}
    input_variables: list[NodeVariable] = []
    output_variables: list[NodeVariable] = []
    # 输出变量可编辑开关（元配置）：False=只读，保存时剥离 base_config.output_variables，
    # 执行时以 ConfigClass 预设为准；True=保留显式配置。经 config-schema 自动下发给前端
    output_variables_editable: bool = Field(
        default=False,
        description=(
            "输出变量是否允许节点配置自定义。False（默认）= 只读，"
            "保存时剥离 base_config.output_variables，执行时以 ConfigClass 预设为准；"
            "True = 保留显式配置，执行与展示均按配置的变量名读写"
        ),
    )
    # ---- 工具审批（仅 Agent 模式生效；继承 BaseNodeHandler 的工具节点可直接调用
    # self._request_tool_approval；如 shell/ssh 等"命令工具"会用到）----
    approval_required_tools: list[str] = Field(
        default_factory=list,
        description=(
            "执行前需用户确认的工具名白名单（完整工具名，如 shell_executor）。"
            "仅 Agent 模式生效，留空表示全部命令直接执行。"
        ),
    )
    approval_required_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "需用户确认的命令正则模式（Python re 语法，逐条 re.search(command, ...)）。"
            "仅 Agent 模式生效；适用于『命令工具』节点按内容拦截危险命令。"
        ),
    )


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
                field_desc["default"] = fi.default_factory() # type: ignore
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


def is_output_variables_editable(node_type: str) -> bool:
    """判断指定节点类型的输出变量是否允许节点配置自定义

    读取该类型 ConfigClass 的 output_variables_editable 字段默认值
    （元配置，与 _strip_output_variables 的剥离豁免判断共用）。

    Args:
        node_type: 节点类型标识

    Returns:
        True=可编辑（保存时保留 base_config.output_variables）；False=只读。
        未注册或无 ConfigClass 的类型一律 False。
    """
    from app.agent_flow.handler_registry import NodeHandlerRegistry

    handler_cls = NodeHandlerRegistry.get_handler_class(node_type)
    if handler_cls is None:
        handler_cls = NodeHandlerRegistry._get_factory_handler_class(node_type)
    config_cls = getattr(handler_cls, "ConfigClass", None)
    if config_cls is None:
        return False
    return bool(
        config_cls.model_fields.get("output_variables_editable").default
        if config_cls.model_fields.get("output_variables_editable")
        else False
    )


class BaseNodeHandler(ABC):
    """
    节点处理器基类
    所有节点处理器都应该继承此类
    """

    ConfigClass: Optional[type[BaseNodeConfig]] = None

    # 按需加载（延迟域）族策略：节点 lazy_load 缺省时本族工具的默认归属。
    #   "bind" = 始终具名绑定（内置族默认：高频核心能力，bind 的 token 成本低、
    #            调用零往返，进延迟域纯属损失）
    #   "auto" = 参与阈值判定（外部工具族如 MCP：候选数 > DEFERRED_TOOL_THRESHOLD
    #            才整体进延迟域，否则回退全量）
    # 三层策略优先级：节点 base_config.lazy_load > handler.defer_policy > "bind"。
    # 仅 "auto" 族会被登记进延迟域候选；内置族想延迟需节点配置 lazy_load=true。
    defer_policy: str = "bind"

    def __init__(self, resolver: Optional[VariableResolver] = None):
        """
        初始化处理器

        Args:
            resolver: 变量解析器，默认使用全局单例
        """
        self._resolver = resolver or variable_resolver
        # Agent 模式上下文注入槽（由 setup_tool_handlers 写入）：
        # _writer 用于 SSE 事件 emit，_session_id 用于 tool_approval_service 路由。
        # 非 Agent 模式保持 None / 0，对应工具审批钩子直接跳过。
        self._writer: Optional[StreamWriter] = None
        self._session_id: int = 0
        # Flow 模式执行记录 ID（thread_id=flow_{id} 时由 _resolve_context 写入；
        # >0 表示 Flow 模式，工具审批钩子走 interrupt 路径）
        self._execution_id: int = 0
        # Flow 审批互斥锁：同一 handler 实例服务并行工具时串行化「查授权 →
        # 加入/创建审批批次」的快速决策段，防止并发写批次状态。
        # 批次合并语义见 _request_flow_batch_approval。
        self._flow_approval_lock = asyncio.Lock()
        # Flow 活跃审批批次：{"approval_id", "future", "tool_calls"}。
        # 并行工具进锁时若存在未决策批次则加入（整批一次弹窗一次决策），
        # 批次决策后由创建者清理。仅 Flow 路线使用（Agent 保持逐工具队列语义）。
        self._active_flow_batch: Optional[dict] = None
        # 审批正则缓存：_compiled_patterns_source 作为缓存 key（tuple 不可哈希问题规避）
        self._compiled_patterns: list[re.Pattern] = []
        self._compiled_patterns_source: Optional[tuple[str, ...]] = None

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

    def _is_cancelled(self, state: FlowState) -> bool:
        """协作式取消检查点（Flow 模式）：state 标志 + 全局中断服务。

        中断信号到达时写入 state.is_interrupted（幂等），供上游
        _execute_graph_stream 收尾时判定取消状态。循环迭代间、
        分段 sleep 等长耗时等待点应调用本方法实现及时停止。
        """
        if state.is_interrupted:
            return True
        ctx = get_execution_context()
        if ctx and interrupt_service.is_flow_interrupted(ctx.execution_id):
            state.set_interrupted()
            return True
        return False

    # ---- 工具审批钩子（基类默认实现，shell/ssh/python 等"命令工具"复用）----

    def _resolve_context(self, config: Optional[RunnableConfig]) -> tuple | None:
        """从 RunnableConfig 提取 session_id（question_handler / shell_handler 同模式）。

        setup_tool_handlers 在调用本方法前已注入 _writer，本方法只负责 session_id。
        Agent 模式：thread_id=agent_{id} → _session_id；
        Flow 模式：thread_id=flow_{id} → _execution_id（_session_id 保持 0，
        工具审批钩子据此分流到 interrupt 路径而非 SSE pending 路径）。
        """
        self._session_id = 0
        self._execution_id = 0
        configurable = (config or {}).get("configurable", {})
        session_id = configurable.get("session_id")
        if not session_id:
            thread_id = str(configurable.get("thread_id") or "")
            if thread_id.startswith("agent_"):
                session_id = thread_id.removeprefix("agent_")
            elif thread_id.startswith("flow_"):
                try:
                    self._execution_id = int(thread_id.removeprefix("flow_"))
                except (TypeError, ValueError):
                    self._execution_id = 0
        try:
            self._session_id = int(session_id or 0)
        except (TypeError, ValueError):
            self._session_id = 0

    def _ensure_patterns_compiled(self, patterns: list[str]) -> list[re.Pattern]:
        """懒编译 approval_required_patterns。

        按 patterns 内容做缓存 key：内容变更即重新编译，长度巧合不会复用旧缓存。
        非法正则单条跳过并记 warning，不阻塞其他。
        """
        key = tuple(patterns or ())
        if self._compiled_patterns_source == key:
            return self._compiled_patterns
        compiled: list[re.Pattern] = []
        for raw in patterns or ():
            s = (raw or "").strip()
            if not s:
                continue
            try:
                compiled.append(re.compile(s, re.IGNORECASE))
            except re.error:
                logger.warning("approval_required_patterns 跳过非法正则: %r", s)
        self._compiled_patterns = compiled
        self._compiled_patterns_source = key
        return compiled

    async def _check_and_request_approval(
        self,
        tool_name: str,
        tool_args: dict,
        cfg: BaseNodeConfig,
        content_for_pattern: str,
        node_key: str,
        force_reason: Optional[str] = None,
    ) -> Optional[str]:
        """工具审批统一钩子：白名单 + 正则 → request approval。

        调用方需在工具闭包最前面 await 一次，按返回值决定是否放行：

            approval_result = await self._check_and_request_approval(...)
            if approval_result not in (None, "approved"):
                return {"error": "用户未批准...", "success": False}

        Args:
            force_reason: 非空时跳过白名单/正则命中判断，**强制走审批**
                （沙箱 opaque 命令/白名单外写入场景：策略 ask 要求弹窗，
                但节点未配置 approval_required_*，命中判断会直接放行——
                命中语义与强制审批语义必须分开）。免审授权仍生效。

        Returns:
            None：未命中审批条件（继续执行工具）；
            "approved"：用户已批准；
            "rejected" / "timeout"：拒绝/超时。

        双模式分流：
        - Agent 模式（_session_id > 0 且 _writer 非空）：SSE + pending service 路径；
        - Flow 模式（_execution_id > 0）：LangGraph interrupt 路径（复用
          waiting_human 机制，执行暂停落库，前端 Flow 页审批弹窗，resume 恢复）；
        - 两者皆非：直接 None 放行（纯 Flow 无审批配置时零开销）。
        """
        if self._session_id <= 0 and self._execution_id <= 0:
            return None

        if force_reason:
            # 强制审批：跳过命中判断，reason 直接采用 force_reason
            hit_tool = False
            hit_pattern = False
            matched_pattern: Optional[str] = None
            approval_reason = force_reason
        else:
            approval_tools = list(getattr(cfg, "approval_required_tools", []) or [])
            approval_patterns = list(getattr(cfg, "approval_required_patterns", []) or [])

            # 1) 工具名白名单命中
            hit_tool = tool_name in approval_tools
            # 2) 命令/内容正则命中（content_for_pattern 为空时跳过正则，避免误命中）
            hit_pattern = False
            matched_pattern = None
            if content_for_pattern and approval_patterns:
                compiled = self._ensure_patterns_compiled(approval_patterns)
                for i, p in enumerate(compiled):
                    if p.search(content_for_pattern):
                        hit_pattern = True
                        matched_pattern = approval_patterns[i]
                        break

            if not (hit_tool or hit_pattern):
                return None

            # 拼装审批原因（reason），前端 ToolApprovalEvent 直接展示
            reason_parts: list[str] = []
            if hit_tool:
                reason_parts.append("工具已配置为需审批")
            if hit_pattern and matched_pattern is not None:
                reason_parts.append(f"命令匹配危险模式: {matched_pattern}")
            approval_reason = "; ".join(reason_parts) or None

        # 3) 免审授权命中：用户曾对该工具选「批准并记住」→ 直接放行。
        # 查询失败按未授权处理（fail-closed，不因授权表异常扩大放行面）
        try:
            if await self._has_session_grant(tool_name):
                logger.info(
                    "免审授权命中: session_id=%s, execution_id=%s, tool=%s（自动放行）",
                    self._session_id,
                    self._execution_id,
                    tool_name,
                )
                return "approved"
        except Exception as e:
            logger.warning("免审授权查询失败，按需审批处理: %s", e)

        # 4) 模式分流：Flow 与 Agent 统一走 tool_approval_service（future 等待）。
        # Flow 曾用 LangGraph interrupt 路线，但 interrupt 会暂停整张图并在
        # resume 后从头重执行节点——LLM 被重新调用生成新工具调用，旧工具段在
        # 前端永远停在「执行中」（新事件是新 tool_call_id），且多批审批连环
        # 弹窗。future 路线：图不暂停，SSE 推审批事件，await Event 等决策，
        # 与 Agent 模式完全同构（tool_approval_service 注释亦说明此设计动机）。
        # session key：Agent 用 session_id，Flow 用 execution_id（两域主键独立，
        # tool_approval_service 队列按 key 隔离，互不干扰）。
        if self._session_id <= 0 and self._execution_id > 0:
            # Flow 整批审批：锁内只做「查授权 → 加入/创建批次」的快速决策段，
            # **锁必须在本调用返回前释放**——收集窗口与 Event 等待都在锁外，
            # 否则兄弟工具永远拿不到锁（自我死锁，模拟测试实锤）。
            async with self._flow_approval_lock:
                try:
                    if await self._has_session_grant(tool_name):
                        logger.info(
                            "免审授权命中(锁内双检): execution_id=%s, tool=%s（自动放行）",
                            self._execution_id,
                            tool_name,
                        )
                        return "approved"
                except Exception as e:
                    logger.warning("免审授权查询失败，按需审批处理: %s", e)
                # 锁内快速段：加入/创建批次（创建者先不发事件），拿到 (future, approval_id)
                future, approval_id, is_creator, call_id = await self._join_or_create_batch(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    node_key=node_key,
                    approval_reason=approval_reason,
                )
            # ---- 锁外：创建者走收集窗口收敛成员后发全量事件 ----
            if is_creator:
                await self._collect_and_emit_batch(
                    approval_id=approval_id,
                    node_key=node_key,
                    approval_reason=approval_reason,
                )
            return await self._await_batch_decision(
                future=future,
                approval_id=approval_id,
                is_creator=is_creator,
                call_id=call_id,
            )

        if self._session_id > 0 and self._writer is not None:
            # Agent 模式（方案B 统一）：并发工具同样合并为单批次一次弹窗，
            # 复用 Flow 的三方法结构（队列 key= session_id，域隔离见
            # _approval_queue_key）。单工具调用时批次天然只有 1 个成员，
            # 行为与旧的逐工具队列一致；子Agent 审批仍走独立转发链路。
            async with self._flow_approval_lock:
                try:
                    if await self._has_session_grant(tool_name):
                        logger.info(
                            "免审授权命中(锁内双检): session_id=%s, tool=%s（自动放行）",
                            self._session_id,
                            tool_name,
                        )
                        return "approved"
                except Exception as e:
                    logger.warning("免审授权查询失败，按需审批处理: %s", e)
                future, approval_id, is_creator, call_id = await self._join_or_create_batch(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    node_key=node_key,
                    approval_reason=approval_reason,
                )
            if is_creator:
                await self._collect_and_emit_batch(
                    approval_id=approval_id,
                    node_key=node_key,
                    approval_reason=approval_reason,
                )
            return await self._await_batch_decision(
                future=future,
                approval_id=approval_id,
                is_creator=is_creator,
                call_id=call_id,
            )

        # 无 writer 的 Agent 场景（子Agent 内部等）退回逐工具路径
        return await self._request_tool_approval(
            tool_name=tool_name,
            tool_args=tool_args,
            node_key=node_key,
            approval_reason=approval_reason,
        )

    def _approval_queue_key(self) -> int:
        """审批批次队列 key：Agent 用 session_id，Flow 用 execution_id。

        两域主键独立自增会撞号，tool_approval_service 队列按 key 隔离，
        语义等同域隔离。批次状态 _active_flow_batch 也以本 key 路由。
        """
        return self._session_id if self._session_id > 0 else self._execution_id

    async def _has_session_grant(self, tool_name: str) -> bool:
        """查询当前域是否已有该工具的免审授权（session_tool_grant 表）。

        scope 必须与查询域一致：Agent 查 scope=agent（session_id=会话ID），
        Flow 查 scope=flow（session_id=执行记录ID）——两域主键独立自增会撞号，
        缺 scope 条件会导致授权误命中。
        """
        from sqlalchemy import select

        from app.config.database import AsyncSessionLocal
        from app.models.session_tool_grant import SessionToolGrant

        if self._session_id > 0:
            scope, domain_id = "agent", self._session_id
        else:
            scope, domain_id = "flow", self._execution_id

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(SessionToolGrant.id).where(
                    SessionToolGrant.scope == scope,
                    SessionToolGrant.session_id == domain_id,
                    SessionToolGrant.tool_name == tool_name,
                    SessionToolGrant.is_delete == 0,
                )
            )
            return result.first() is not None

    async def _grant_flow_tool(self, tool_name: str, approval_id: str) -> None:
        """写 Flow 执行级免审授权（scope=flow，session_id 列存 execution_id）"""
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        from app.config.database import AsyncSessionLocal
        from app.models.session_tool_grant import SessionToolGrant

        async with AsyncSessionLocal() as db:
            stmt = sqlite_insert(SessionToolGrant).values(
                scope="flow",
                session_id=self._execution_id,
                tool_name=tool_name,
                granted_by=approval_id,
            )
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["scope", "session_id", "tool_name"]
            )
            await db.execute(stmt)
            await db.commit()

    async def _request_tool_approval(
        self,
        tool_name: str,
        tool_args: dict,
        node_key: str,
        approval_reason: Optional[str] = None,
    ) -> str:
        """工具审批钩子（question_handler 模式：register + emit + await）。

        调用方需先确认 self._session_id > 0 且 self._writer 非空，否则会因写入空
        session 或无 writer 报错。Returns: "approved" / "rejected" / "timeout"。
        """
        # 函数内 import：避免基类加载时强依赖 tool_approval_service / flow_event
        # （这两个模块在工具链启动期才就绪；基类是几乎所有 handler 的共同祖先）
        from app.agent_flow.flow_event import ToolApprovalEvent
        from app.constants.timing import USER_RESPONSE_TIMEOUT_SECONDS
        from app.services.tool_approval_service import tool_approval_service

        # 队列 key：Agent 用 session_id，Flow 用 execution_id（两域主键独立自增，
        # 混用会撞号；tool_approval_service 队列按 key 隔离，语义等同域隔离）
        approval_key = self._session_id if self._session_id > 0 else self._execution_id

        approval_id = uuid.uuid4().hex[:16]
        future = tool_approval_service.register(
            approval_key,
            approval_id,
            tool_calls=[
                {
                    "id": f"call_{uuid.uuid4().hex[:24]}",
                    "name": tool_name,
                    "args": tool_args,
                }
            ],
            approval_needed=[tool_name],
            approval_reason=approval_reason,
        )
        try:
            if self._writer is None:
                raise RuntimeError("StreamWriter 未注入，无法发送审批事件")
            self._writer(
                ToolApprovalEvent(
                    node_key=node_key,
                    approval_id=approval_id,
                    tool_calls=[
                        {
                            "id": approval_id,
                            "name": tool_name,
                            "args": tool_args,
                        }
                    ],
                    approval_needed=[tool_name],
                    expires_in=USER_RESPONSE_TIMEOUT_SECONDS,
                    approval_reason=approval_reason,
                )
            )
        except Exception as e:  # 事件发送失败按拒绝处理，避免继续执行未知命令
            logger.warning("审批事件发送失败，按拒绝处理: %s", e)
            tool_approval_service.remove(approval_key, approval_id)
            # 审计补写：发送失败即 fail-closed 拒绝（asked 行已落，补终态）
            tool_approval_service._audit.audit_decided(
                approval_key, approval_id, "rejected"
            )
            return "rejected"

        try:
            await asyncio.wait_for(
                future.event.wait(), timeout=USER_RESPONSE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            tool_approval_service.remove(approval_key, approval_id)
            # 审计补写：超时终态（与用户决策竞争时先到先得，幂等）
            tool_approval_service.mark_timeout(approval_key, approval_id)
            return "timeout"

        tool_approval_service.remove(approval_key, approval_id)
        return future.result or "rejected"

    async def _join_or_create_batch(
        self,
        tool_name: str,
        tool_args: dict,
        node_key: str,
        approval_reason: Optional[str] = None,
    ) -> tuple[Any, str, bool, str]:
        """锁内快速段：加入活跃批次或创建新批次（创建者先不发事件）。

        调用方必须已持有 _flow_approval_lock。返回 (future, approval_id, is_creator, call_id)。
        call_id 是本工具调用在批次内的唯一标识（逐工具决策的路由键）——
        每次调用独立生成，批内不重复；等待方凭它从 per_tool_decisions 取结局。
        """
        from app.services.tool_approval_service import tool_approval_service

        approval_key = self._approval_queue_key()
        call_id = f"call_{uuid.uuid4().hex[:24]}"

        if (
            self._active_flow_batch is not None
            and tool_approval_service.is_pending(
                approval_key, self._active_flow_batch["approval_id"]
            )
        ):
            # 加入现有批次（慢兄弟兜底：创建者已发事件后才进锁的场景）
            batch = self._active_flow_batch
            batch["tool_calls"].append(
                {"id": call_id, "name": tool_name, "args": tool_args}
            )
            if approval_reason and approval_reason not in batch["reasons"]:
                batch["reasons"].append(approval_reason)
            return batch["future"], batch["approval_id"], False, call_id

        approval_id = uuid.uuid4().hex[:16]
        future = tool_approval_service.register(
            approval_key,
            approval_id,
            tool_calls=[
                {"id": call_id, "name": tool_name, "args": tool_args}
            ],
            approval_needed=[tool_name],
            approval_reason=approval_reason,
        )
        self._active_flow_batch = {
            "approval_id": approval_id,
            "future": future,
            "tool_calls": [{"id": call_id, "name": tool_name, "args": tool_args}],
            "reasons": [approval_reason] if approval_reason else [],
        }
        return future, approval_id, True, call_id

    async def _collect_and_emit_batch(
        self, approval_id: str, node_key: str, approval_reason: Optional[str] = None
    ) -> None:
        """锁外收集窗口：等并行兄弟进锁加入（动态收敛），稳定后发全量事件。

        创建者专用。窗口总时长 ~1s（10 轮 × 100ms），连续一轮无新增即收敛。
        """
        from app.agent_flow.flow_event import ToolApprovalEvent
        from app.constants.timing import USER_RESPONSE_TIMEOUT_SECONDS
        from app.services.tool_approval_service import tool_approval_service

        approval_key = self._approval_queue_key()
        last_count = 1
        for _ in range(10):
            await asyncio.sleep(0.1)
            async with self._flow_approval_lock:
                batch = self._active_flow_batch
                cur = (
                    len(batch["tool_calls"])
                    if batch and batch["approval_id"] == approval_id
                    else last_count
                )
            if cur > last_count:
                last_count = cur
            else:
                break

        # 取最终成员快照发全量事件（前端弹窗渲染全部工具卡片）
        async with self._flow_approval_lock:
            batch = self._active_flow_batch
            tool_calls = (
                list(batch["tool_calls"])
                if batch and batch["approval_id"] == approval_id
                else []
            )
            reasons = (
                list(batch["reasons"])
                if batch and batch["approval_id"] == approval_id
                else []
            )
        try:
            if self._writer is None:
                raise RuntimeError("StreamWriter 未注入，无法发送审批事件")
            self._writer(
                ToolApprovalEvent(
                    node_key=node_key,
                    approval_id=approval_id,
                    tool_calls=tool_calls,
                    approval_needed=[tc["name"] for tc in tool_calls],
                    expires_in=USER_RESPONSE_TIMEOUT_SECONDS,
                    approval_reason="; ".join(reasons) or approval_reason,
                    total=len(tool_calls),
                )
            )
        except Exception as e:
            logger.warning("审批事件发送失败，按拒绝处理: %s", e)
            tool_approval_service.remove(approval_key, approval_id)
            self._active_flow_batch = None
            tool_approval_service._audit.audit_decided(
                approval_key, approval_id, "rejected"
            )

    async def _await_batch_decision(
        self,
        future: Any,
        approval_id: str,
        is_creator: bool,
        call_id: str = "",
    ) -> str:
        """锁外等批次决策（Event 由 tool_approval_service.resolve 触发）。

        创建者负责清理批次状态（决策后/超时后）；加入者只读结果。

        决策粒度分发（方案B）：
        - future.per_tool_decisions 非空（逐工具决策）：按本调用的 call_id 取
          各自结局，缺失项 fail-closed 视为 rejected；
        - 否则整批粒度：直接返回 result（approved / rejected / cancelled）。

        Returns:
            "approved" / "rejected" / "timeout"（本工具调用的最终结局）
        """
        from app.constants.timing import USER_RESPONSE_TIMEOUT_SECONDS
        from app.services.tool_approval_service import tool_approval_service

        approval_key = self._approval_queue_key()
        try:
            await asyncio.wait_for(
                future.event.wait(), timeout=USER_RESPONSE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            if is_creator:
                tool_approval_service.remove(approval_key, approval_id)
                self._active_flow_batch = None
                tool_approval_service.mark_timeout(approval_key, approval_id)
            return "timeout"

        if is_creator:
            tool_approval_service.remove(approval_key, approval_id)
            self._active_flow_batch = None

        per_tool = future.per_tool_decisions
        if per_tool and call_id:
            return per_tool.get(call_id, "rejected")
        return future.result or "rejected"

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

    async def _check_direct_execution_approval(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig],
        content_for_pattern: str,
    ) -> Optional[str]:
        """直执行节点（Flow 主图 default 边）的审批检查（python/api 复用）。

        直执行节点没有 LLM 工具调用入口，审批钩子此前不生效——本方法补齐：
        在 execute 开头调用，按节点配置的 approval_required_tools/patterns 判定。
        工具名约定为 {node_type}_executor_{node_key}（与 python 工具路径同源）。

        Returns:
            None：未命中审批条件（继续执行）；
            "approved"：用户已批准（Agent=SSE 路径 / Flow=interrupt 路径）；
            "rejected" / "timeout"：拒绝/超时（调用方应中止执行并记错误）。
        """
        # 直执行入口此前未调 _resolve_context（工具路径才调），先解析运行域
        self._resolve_context(config)

        cfg = self._get_config(node)
        tool_name = f"{node.node_type}_executor_{node.node_key}"
        return await self._check_and_request_approval(
            tool_name=tool_name,
            tool_args={"content": content_for_pattern[:2000]},
            cfg=cfg,
            content_for_pattern=content_for_pattern,
            node_key=node.node_key,
        )

    @classmethod
    def _resolve_output_var_names(cls, node: FlowNode) -> list[str]:
        """解析节点的输出变量名列表（唯一事实源 = ConfigClass.output_variables）

        保存时非 editable 节点的 base_config.output_variables 已被剥离，
        以 ConfigClass 预设默认为准；editable 节点
        （output_variables_editable=True）则优先取显式配置。
        execute 写入与 get_output_content 读取共用本方法，两端名称永远同源。

        用 model_construct 轻量构造（同 get_default_config），仅读取字段
        默认值，不触发全字段校验（如 LlmNodeConfig.user_prompt 必填，
        读取输出名的场景不应被迫提供无关必填字段）。

        Args:
            node: 流程节点对象

        Returns:
            输出变量名列表（保持 ConfigClass 定义顺序，过滤空名）
        """
        if cls.ConfigClass is None:
            return []
        raw = node.base_config or {}
        explicit = raw.get("output_variables")
        if explicit and isinstance(explicit, list):
            names = [
                v.get("name", "") if isinstance(v, dict) else v.name for v in explicit
            ]
            names = [n for n in names if n]
            if names:
                return names
        cfg:BaseNodeConfig = cls.ConfigClass.model_construct()
        return [v.name for v in cfg.output_variables if v.name]

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

    async def get_tool(self, node: FlowNode) -> Optional[BaseTool] | Sequence[BaseTool]:
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

    async def get_runtime_reminder(self, node: FlowNode) -> Optional[str]:
        """
        返回要拼入消息层 <system-reminder> 的运行时提醒片段

        与 get_system_prompt_hint（静态内容，进 system_prompt 利于前缀缓存）相对，
        本方法返回动态内容（工作目录、会话状态等），随每轮 LLM 调用临时注入，
        不进入 system_prompt/checkpoint/DB。需要动态提醒的子 handler 重写此方法。
        同一 handler 单例服务多个工具节点时只收集一次。

        Args:
            node: 节点对象

        Returns:
            提醒文本片段，不需要时返回 None
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
    def get_tool_info(cls, node: "FlowNode") -> list[dict]:
        """返回工具元数据 [{name, description}]，不实际执行

        职责：① 前端 connected-tools 展示；② 延迟域索引数据源
        （get_provider 默认实现）。**必须保持同步、零建连、纯静态**——
        禁止在此加 DB 查询/建连等动态逻辑（会拖慢前端 API 且破坏
        延迟域零建连约定）；需要动态索引的族重写 get_provider 返回
        自定义 provider（见 mcp_handler.McpToolProvider）。

        Args:
            node: 节点对象

        Returns:
            工具信息列表，每项包含 name 和 description 字段
        """
        return []

    def get_providers(
        self, node: "FlowNode", writer=None, session_id: int = 0, execution_id: int = 0
    ) -> Sequence["ToolProvider"]:
        """返回按需加载延迟域的 ToolProvider 列表（默认单 provider 形态）

        与 `get_provider` 的关系：本方法是延迟域登记的**唯一入口**——族内 provider
        粒度可能不同于节点粒度（如 MCP 一族是「一 server 一 provider」，一个节点
        可贡献多个 provider），故统一返回列表；默认实现委托 `get_provider`。

        `writer`/`session_id`/`execution_id` 为审批依赖：重写者若在 provider 内部
        构造工具闭包并做审批，必须使用调用方透传的已注入实例值——自行 new 一个
        handler 空壳会让审批钩子静默放行（Agent 因 `_session_id<=0`，Flow 因
        `_execution_id<=0`，安全后门）。

        Args:
            node: 节点对象
            writer: StreamWriter（Agent 模式审批事件通道）
            session_id: 会话 ID（>0 Agent 模式审批生效）
            execution_id: 执行记录 ID（>0 Flow 模式审批走 interrupt 路径）

        Returns:
            provider 列表（空列表 = 该节点工具不参与延迟域）
        """
        provider = self.get_provider(node)
        return [provider] if provider is not None else []

    def get_provider(self, node: "FlowNode") -> Optional["ToolProvider"]:
        """返回按需加载延迟域的 ToolProvider（None=不参与延迟域）

        默认实现基于 get_tool_info（索引）+ get_tool（物化）组合：
        子类已实现这两个方法时零成本接入延迟域——只需节点配置
        tool_lazy=True，setup_tool_handlers 即登记本 provider。

        物化语义：调 get_tool 取真实工具对象按名匹配（内置族零建连）；
        需要特殊物化逻辑（如 MCP 真实建连+审批 wrap）的子类重写本方法
        返回自定义 provider（见 mcp_handler.McpToolProvider）；需要一节点
        多 provider 的族改重写 `get_providers`。

        Args:
            node: 节点对象

        Returns:
            ToolProvider 实例；默认 None（该族工具始终具名 bind）
        """
        if not self.get_tool_info(node):
            return None

        from app.agent_flow.deferred_tools import ToolProviderCompat

        handler = self
        # 物化缓存：同节点 N 个工具物化只调一次 get_tool（工具闭包重建
        # 有成本，且 knowledge/browser 的 get_tool 每次重建全部工具）
        _materialized_cache: dict[str, list] = {}

        async def _get_tools_cached() -> list:
            result = await handler.get_tool(node)
            if result is None:
                return []
            if isinstance(result, (list, tuple)):
                return list(result)
            return [result]

        async def _materialize(tool_name: str):
            if not _materialized_cache:
                _materialized_cache["tools"] = await _get_tools_cached()
            for t in _materialized_cache["tools"]:
                if t.name == tool_name:
                    return t
            return None

        async def _search(pattern: str, limit: int = 10) -> list[dict]:
            import re

            regex = re.compile(pattern, re.IGNORECASE)
            matched = []
            for t in handler.get_tool_info(node):
                if regex.search(t["name"]) or regex.search(t["description"]):
                    # 命中即物化提取真实 args_schema（内置族零建连，代价
                    # 可忽略）：模型看到参数格式才能一次调准 tool_execute，
                    # 返回 None schema 会导致盲调→校验失败→多烧一轮往返
                    tool = await _materialize(t["name"])
                    matched.append(
                        {
                            "name": t["name"],
                            "description": t["description"],
                            "input_schema": (
                                tool.args_schema if tool is not None else None
                            ),
                        }
                    )
                    if len(matched) >= limit:
                        break
            return matched

        return ToolProviderCompat(
            provider_key=f"{type(self).__name__}:{node.node_key}",
            list_meta_fn=lambda: [
                {"name": t["name"], "description": t["description"]}
                for t in self.get_tool_info(node)
            ],
            search_fn=_search,
            materialize_fn=_materialize,
        )

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

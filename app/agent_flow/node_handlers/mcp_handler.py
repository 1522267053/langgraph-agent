"""
MCP节点处理器

MCP节点支持两种工作模式（可共存，由连线拓扑决定）：
- 工具模式：节点通过「工具」把手连到 LLM 节点，工具由 LLM 自主调用
- 直接执行模式：节点通过「入/出」把手挂到主干流程，按配置的固定工具
  + 逐参数绑定（支持 {{变量}} 插值）直接执行，结果写入输出变量
"""

import logging
from typing import Optional, TYPE_CHECKING
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool

from langgraph.types import StreamWriter
from pydantic import Field

from app.config.database import AsyncSessionLocal
from app.models.flow_node import FlowNode
from app.agent_flow.flow_context import FlowState
from app.agent_flow.node_handlers.base_handler import (
    BaseNodeHandler,
    BaseNodeConfig,
    NodeVariable,
)
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.mcp_manager import mcp_tool_manager

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.agent_flow.tool_resolver import LlmToolConfig


class McpNodeConfig(BaseNodeConfig):
    """MCP 节点配置"""

    mcp_server_ids: list[int] = Field(default=[], description="MCP 服务器 ID 列表")
    # ---- 直接执行模式（挂到主干流程时生效）----
    tool_name: Optional[str] = Field(
        default=None, description="直接执行的目标工具名（mcp__<server>__<tool>）"
    )
    tool_args: dict = Field(
        default_factory=dict,
        description="工具参数（逐参数绑定值，支持 {{变量}} 插值）",
    )
    # 显式声明默认输出变量：变量选择器的 schema 兜底从此读取（与 API 节点
    # 声明 body/status_code/headers 同模式），旧节点 config 未声明时也可选。
    # type 标 object：MCP 工具返回值形态不定（str/dict/list），object 是
    # 最通用的标注（string 结果兼容，结构化结果可用子变量路径继续取字段）
    output_variables: list[NodeVariable] = [NodeVariable(name="result", type="object")]
    # 注：approval_required_tools / approval_required_patterns 字段已在 BaseNodeConfig 提供


@NodeHandlerRegistry.register("mcp")
class McpNodeHandler(BaseNodeHandler):
    """
    MCP节点处理器

    工具模式：工具加载在LLM节点中通过 tool_resolver 解析处理；
    直接执行模式：execute 按配置的固定工具+参数直接调用并写输出变量。
    """

    ConfigClass = McpNodeConfig

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        """
        执行MCP节点

        未配置 tool_name（纯工具模式）时为空操作，直接返回状态；
        配置了 tool_name 时按固定工具直接执行（主干流程挂载形态）。
        """
        cfg = self._get_config(node)
        if not cfg.tool_name:
            # 纯工具模式：MCP节点不参与执行流程，工具由LLM节点解析加载
            return state

        tool_name = cfg.tool_name
        try:
            # 1. 解析输入变量（input_variables → context），再对参数做 {{var}} 深度插值
            context = self._resolve_input_variables(cfg.input_variables or [], state)
            resolved_args = self._resolver.resolve_config(cfg.tool_args, state, context)

            # 2. 加载工具并按名匹配
            tool = await self._find_tool(node, tool_name)
            if tool is None:
                state.add_error(
                    node.node_key,
                    f"未找到MCP工具: {tool_name}（检查服务器配置或工具名）",
                )
                return state

            # 3. 校验参数并执行
            try:
                validated = tool.args_schema.model_validate(resolved_args or {})
                call_kwargs = {
                    k: getattr(validated, k)
                    for k in resolved_args
                    if hasattr(validated, k)
                }
            except Exception:
                call_kwargs = resolved_args or {}

            result = await tool.ainvoke(call_kwargs)

            # 4. 结果写入输出变量（默认 result，可通过 output_variables 改名）
            output_names = self._resolve_output_var_names(node)
            state.set_node_variable(node.node_key, output_names[0], result)
        except Exception as e:
            logger.exception(f"MCP节点直接执行失败: {e}")
            state.add_error(node.node_key, f"MCP工具 {tool_name} 执行失败: {str(e)}")

        return state

    async def _find_tool(self, node: FlowNode, tool_name: str) -> Optional[BaseTool]:
        """按名称在节点连接的 MCP 服务器中查找工具（优先缓存，不实际建连）"""
        config = node.base_config or {}
        mcp_server_ids = [int(i) for i in config.get("mcp_server_ids", [])]
        if not mcp_server_ids:
            return None

        async with AsyncSessionLocal() as db:
            tools = await mcp_tool_manager.get_tools(db, mcp_server_ids)
        for t in tools:
            if t.name == tool_name:
                return t
        return None

    @classmethod
    def get_input_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        """直接执行模式下展示解析后的工具参数（执行面板输入块）"""
        cfg = (config if config is not None else node.base_config) or {}
        if not cfg.get("tool_name"):
            return None
        try:
            input_vars = cfg.get("input_variables") or []
            context = {}
            for var in input_vars:
                name, source = var.get("name", ""), var.get("source", "")
                if name and source:
                    context[name] = resolver.resolve_safe(source, state)
            args = resolver.resolve_config(cfg.get("tool_args") or {}, state, context)
            return {
                "tool": cfg.get("tool_name"),
                **(args if isinstance(args, dict) else {}),
            }
        except Exception:
            return None

    @classmethod
    def get_output_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        """直接执行模式下展示工具结果（执行面板输出块）"""
        cfg = (config if config is not None else node.base_config) or {}
        if not cfg.get("tool_name"):
            return None
        # 输出名来自 ConfigClass.output_variables（与 execute 写入同源）
        names = cls._resolve_output_var_names(node)
        if not names:
            return None
        value = state.get_node_variable(node.node_key, names[0])
        if value is None:
            return None
        return {names[0]: value}

    async def get_tool(self, node: FlowNode) -> list[BaseTool]:
        """
        返回MCP服务器提供的所有工具，每个工具额外 wrap 一层工具审批钩子

        Args:
            node: 节点对象

        Returns:
            MCP工具列表
        """
        config = node.base_config or {}
        mcp_server_ids = config.get("mcp_server_ids", [])
        if not mcp_server_ids:
            return []

        try:
            async with AsyncSessionLocal() as db:
                base_tools = await mcp_tool_manager.get_tools(
                    db, [int(i) for i in mcp_server_ids]
                )
        except Exception as e:
            logger.exception(f"获取mcp工具失败:{str(e)}")
            return []

        # 工具审批：wrap 一层审批钩子（基类 _check_and_request_approval 统一处理白名单+正则）
        cfg = self._get_config(node)
        handler = self
        wrapped: list[BaseTool] = []
        for base_tool in base_tools:
            if not isinstance(base_tool, StructuredTool) or not base_tool.coroutine:
                wrapped.append(base_tool)
                continue
            tool_name = base_tool.name

            def _make_approval_wrapper(
                tool_name: str, original_coro, handler, cfg, node_key: str
            ):
                """工厂函数：为单个工具生成审批 wrapper。

                必须通过工厂隔离作用域——闭包是晚绑定的，若直接在 for 循环里定义
                async def，所有 wrapper 的 tool_name/original_coro 都会引用循环结束
                后的尾值（最后一个工具），导致全部工具实际执行同一个工具
                （args_schema 同理被替换成尾值 schema）。
                """

                async def approval_wrapped_coro(*args, **kwargs):
                    # 把 args/kwargs 平铺进 kwargs 后做正则匹配（args_schema 通常是 kwargs 形式）
                    flat: dict = dict(kwargs)
                    for i, v in enumerate(args):
                        flat.setdefault(f"_arg_{i}", v)
                    content = f"{tool_name} " + " ".join(
                        str(v)[:200] for v in flat.values()
                    )
                    approval_result = await handler._check_and_request_approval(
                        tool_name=tool_name,
                        tool_args=flat,
                        cfg=cfg,
                        content_for_pattern=content,
                        node_key=node_key,
                    )
                    if approval_result not in (None, "approved"):
                        return {
                            "error": (
                                f"用户未批准执行MCP工具 {tool_name}（{approval_result}）。"
                                "如需继续，请征得用户同意后重新调用。"
                            ),
                            "success": False,
                            "error_type": "approval_rejected",
                        }
                    return await original_coro(*args, **kwargs)

                return approval_wrapped_coro

            wrapped.append(
                StructuredTool(
                    name=tool_name,
                    description=base_tool.description,
                    args_schema=base_tool.args_schema,
                    coroutine=_make_approval_wrapper(
                        tool_name, base_tool.coroutine, handler, cfg, node.node_key
                    ),
                    response_format=base_tool.response_format,
                    metadata=base_tool.metadata,
                )
            )
        return wrapped

    @classmethod
    def get_tool_config(cls, node: FlowNode, config: "LlmToolConfig") -> bool:
        """将MCP服务器ID添加到工具配置"""
        node_config = node.base_config or {}
        mcp_ids = node_config.get("mcp_server_ids", [])
        if mcp_ids:
            config.mcp_server_ids.extend([int(i) for i in mcp_ids])
            return True
        return False

    @classmethod
    def get_tool_info(cls, node: FlowNode) -> list[dict]:
        """返回MCP服务器提供的工具信息（三级读取，尽量避免真实建连）

        ① 内存缓存（连接存活时）→ ② DB 持久缓存 McpToolCache（毫秒级，
        服务重启不丢）→ ③ 两者皆无（该服务器从未连过）才触发一次真实
        建连预热。每个工具返回 name/description + parameters（参数属性
        列表，供前端逐参数绑定表单渲染）。
        """
        cfg = node.base_config or {}
        mcp_server_ids = cfg.get("mcp_server_ids", [])
        if not mcp_server_ids:
            return []

        from app.agent_flow.mcp_manager import mcp_tool_manager

        tools: list[dict] = []
        cold_servers: list[int] = []
        for sid in mcp_server_ids:
            sid_int = int(sid)
            cached = mcp_tool_manager._tools_cache.get(sid_int)
            if cached:
                for t in cached:
                    tools.append(
                        {
                            "name": t.name,
                            "description": t.description or "",
                            "parameters": cls._extract_parameters(t),
                        }
                    )
            else:
                cold_servers.append(sid_int)

        # ② 内存缓存未命中的服务器，尝试 DB 持久缓存（同步方法内桥接 async）
        if cold_servers:
            db_tools = cls._load_tools_from_db_cache(cold_servers)
            for sid, db_list in db_tools.items():
                tools.extend(db_list)
                cold_servers.remove(sid)

        # ③ 从未连过的服务器：真实建连预热一次（此后走 ①/②）
        if cold_servers:
            cls._warm_up_servers(cold_servers)

        return tools

    @classmethod
    def _load_tools_from_db_cache(cls, server_ids: list[int]) -> dict[int, list[dict]]:
        """从 DB 工具缓存表读取工具元数据（毫秒级，无建连）"""
        import asyncio

        try:

            async def _read():
                from app.services.mcp_server_service import mcp_server_service
                from app.config.database import AsyncSessionLocal

                result: dict[int, list[dict]] = {}
                async with AsyncSessionLocal() as db:
                    for sid in server_ids:
                        rows = await mcp_server_service.get_tools_cache(db, sid)
                        if not rows:
                            continue
                        tools_list = []
                        for row in rows:
                            schema = row.tool_schema or {}
                            tools_list.append(
                                {
                                    "name": row.tool_name,
                                    "description": row.description or "",
                                    "parameters": cls._parameters_from_schema(schema),
                                }
                            )
                        if tools_list:
                            result[sid] = tools_list
                return result

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # 已在事件循环内（如 FastAPI 端点）：用独立线程跑新事件循环
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(asyncio.run, _read()).result(timeout=10)
            return asyncio.run(_read())
        except Exception:
            return {}

    @classmethod
    def _warm_up_servers(cls, server_ids: list[int]) -> None:
        """真实建连预热（仅内存+DB 缓存全未命中时触发，每次最多一次）"""
        import asyncio

        try:
            from app.agent_flow.mcp_manager import mcp_tool_manager
            from app.config.database import AsyncSessionLocal

            async def _warm():
                for sid in server_ids:
                    try:
                        async with AsyncSessionLocal() as db:
                            await mcp_tool_manager.get_tools(db, [sid])
                    except Exception:
                        pass

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(asyncio.run, _warm()).result(timeout=60)
            else:
                asyncio.run(_warm())
        except Exception:
            pass

    @staticmethod
    def _parameters_from_schema(schema: dict) -> list[dict]:
        """从原始 JSON Schema dict 提取参数属性列表（DB 缓存行无 args_schema 对象时用）"""
        try:
            props = schema.get("properties", {})
            required = set(schema.get("required", []))
            defs = schema.get("$defs", {})
            params = []
            for name, prop in props.items():
                prop_type = prop.get("type")
                if not prop_type and "$ref" in prop:
                    ref_name = prop["$ref"].rsplit("/", 1)[-1]
                    prop_type = "object" if ref_name in defs else None
                if not prop_type and "anyOf" in prop:
                    prop_type = next(
                        (
                            t.get("type")
                            for t in prop["anyOf"]
                            if t.get("type") and t.get("type") != "null"
                        ),
                        None,
                    )
                params.append(
                    {
                        "name": name,
                        "type": prop_type or "string",
                        "description": prop.get("description", ""),
                        "required": name in required,
                    }
                )
            return params
        except Exception:
            return []

    @staticmethod
    def _extract_parameters(tool: BaseTool) -> list[dict]:
        """从工具的 args_schema 提取参数属性列表（供前端逐参数表单）

        兼容两种 schema 形态（MCP 适配器给 dict，本地定义给 pydantic 模型），
        与 mcp_manager._get_tool_schema 同策略。
        """
        try:
            schema_obj = tool.args_schema
            if not schema_obj:
                return []
            if isinstance(schema_obj, dict):
                schema = schema_obj
            elif hasattr(schema_obj, "model_json_schema"):
                schema = schema_obj.model_json_schema()
            else:
                return []
        except Exception:
            return []
        return McpNodeHandler._parameters_from_schema(schema)

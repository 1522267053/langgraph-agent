"""
按需加载工具注册表（Deferred Tool Registry）

解决 MCP 等外部工具族膨胀导致的 tools 参数 token 膨胀：延迟域工具不进
bind_tools（模型不可直接具名调用），改由三个元工具提供发现与执行通道：

    tool_info_list  总览（工具名+一行描述，无 schema，分页）
    tool_search_tool_regex  精确查找（返回完整 input_schema）
    tool_execute    执行通道（tool_name + arguments JSON 字符串）

架构：机制与策略分离——
- ToolProvider 协议：任何工具族（MCP/knowledge/browser/...）实现同一接口，
  差异只在索引来源与物化成本（MCP 需建连，内置族直接取 handler 工具对象）
- DeferredToolRegistry：会话级单例，持有 provider 集合（会话内冻结）与
  物化缓存；审批 wrap 在 materialize 时基于真实工具名+参数执行，防
  tool_execute 成为审批后门

缓存不变量（前缀缓存安全）：
- bound tools 会话内逐字节恒定（三元工具 description 纯静态）
- 物化只进执行器（tool_execute coroutine 内部），永不回写 bound tools
- provider 集合在会话首次收集时冻结快照
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    Union,
    runtime_checkable,
)

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

# 单个工具 input_schema 序列化上限（超出截断，防巨型 schema 灌爆上下文）
SCHEMA_MAX_CHARS = 2000
# 描述一行截断长度（tool_info_list 总览用）
DESC_ONE_LINE_MAX = 100
# 延迟域自动启用阈值：候选工具总数超过此值才启用延迟域，否则回退全量注册
# （小工具集走三步发现流程的往返成本 > schema token 收益，机制不生效）
DEFERRED_TOOL_THRESHOLD = 20


def serialize_tool_schema(args_schema: Any) -> str:
    """工具 args_schema → JSON 字符串（截断），兼容 pydantic 模型与 dict 两种形态

    供 search 结果回传模型使用：模型看到参数格式才能一次调准 tool_execute。
    """
    if args_schema is None:
        return "{}"
    try:
        if isinstance(args_schema, dict):
            schema = args_schema
        elif hasattr(args_schema, "model_json_schema"):
            schema = args_schema.model_json_schema()
        else:
            return "{}"
        schema_str = json.dumps(schema, ensure_ascii=False)
    except Exception as e:
        logger.warning("工具 schema 序列化失败: %s", e)
        return "{}"
    if len(schema_str) > SCHEMA_MAX_CHARS:
        schema_str = schema_str[:SCHEMA_MAX_CHARS] + "…<truncated>"
    return schema_str


@runtime_checkable
class ToolProvider(Protocol):
    """工具族 provider 协议：一个族一个实现（MCP/knowledge/browser/...）

    索引方法（list_meta/search）必须零建连/零重负载——只读缓存或内存
    schema；materialize 才允许真实建连/取工具对象。
    """

    @property
    def provider_key(self) -> str:
        """provider 唯一标识（如 mcp:3、knowledge:kb_5），用于展示与去重"""
        ...

    async def list_meta(self) -> List[Dict[str, str]]:
        """全量工具索引：[{name, description}]（description 供总览截断）"""
        ...

    async def search(self, pattern: str, limit: int = 10) -> List[Dict[str, Any]]:
        """按正则匹配工具名+描述，返回 [{name, description, input_schema}]"""
        ...

    async def materialize(self, tool_name: str) -> Optional[BaseTool]:
        """物化单个工具为可执行 BaseTool（首次建连/取对象，含审批 wrap）"""
        ...


class ToolProviderCompat:
    """函数式 ToolProvider 适配器：内置工具族接入延迟域的轻量方式

    内置族（knowledge/browser 等）的工具 schema 在代码里现成，物化只需
    取 handler 工具对象——用三个函数即可接入，无需为每族写完整类。
    """

    def __init__(
        self,
        provider_key: str,
        list_meta_fn: Callable[[], List[Dict[str, str]]],
        search_fn: Callable[[str, int], Any],
        materialize_fn: Callable[[str], Any],
    ):
        self._key = provider_key
        self._list_meta_fn = list_meta_fn
        self._search_fn = search_fn
        self._materialize_fn = materialize_fn

    @property
    def provider_key(self) -> str:
        return self._key

    async def list_meta(self) -> List[Dict[str, str]]:
        result = self._list_meta_fn()
        if asyncio.iscoroutine(result):
            result = await result
        return result

    async def search(self, pattern: str, limit: int = 10) -> List[Dict[str, Any]]:
        result = self._search_fn(pattern, limit)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    async def materialize(self, tool_name: str) -> Optional[BaseTool]:
        result = self._materialize_fn(tool_name)
        if asyncio.iscoroutine(result):
            result = await result
        return result


@dataclass
class _MaterializedTool:
    """物化缓存条目"""

    tool: BaseTool
    provider_key: str


@dataclass
class DeferredSnapshot:
    """会话冻结快照：reminder/元工具输出只读此快照，会话内不变"""

    # provider_key -> 工具总数
    provider_counts: Dict[str, int] = field(default_factory=dict)
    # 全量索引（provider_key -> [{name, description}]），list_info 数据源
    index: Dict[str, List[Dict[str, str]]] = field(default_factory=dict)
    # 已知工具名 -> provider_key（execute 定位用）
    name_to_provider: Dict[str, str] = field(default_factory=dict)

    @property
    def total_tools(self) -> int:
        return sum(self.provider_counts.values())

    @property
    def provider_keys(self) -> List[str]:
        return list(self.provider_counts.keys())


class DeferredToolRegistry:
    """会话级延迟工具注册表

    生命周期：一次 LLM 节点执行（setup_tool_handlers）内创建并冻结；
    物化缓存跨 ReAct 轮次复用（同一工具二次调用不重建连）。
    """

    def __init__(self):
        self._providers: Dict[str, ToolProvider] = {}
        self._snapshot: Optional[DeferredSnapshot] = None
        self._materialized: Dict[str, _MaterializedTool] = {}
        self._materialize_lock = asyncio.Lock()

    # ---- 注册与冻结（setup 阶段一次性完成） ----

    def register(self, provider: ToolProvider) -> None:
        """登记 provider（不加载任何工具索引）"""
        self._providers[provider.provider_key] = provider

    async def freeze(self) -> DeferredSnapshot:
        """构建冻结快照：拉取全部 provider 的索引（只读缓存，零建连）

        必须在 bind_tools 前调用一次；此后 snapshot 不再变化。
        """
        snapshot = DeferredSnapshot()
        for key, provider in self._providers.items():
            try:
                metas = await provider.list_meta()
            except Exception as e:
                logger.warning("延迟域 provider %s 索引失败，跳过: %s", key, e)
                metas = []
            snapshot.provider_counts[key] = len(metas)
            snapshot.index[key] = metas
            for meta in metas:
                snapshot.name_to_provider[meta["name"]] = key
        self._snapshot = snapshot
        return snapshot

    @property
    def snapshot(self) -> Optional[DeferredSnapshot]:
        return self._snapshot

    @property
    def total_tools(self) -> int:
        return self._snapshot.total_tools if self._snapshot else 0

    def is_deferred(self, tool_name: str) -> bool:
        """工具名是否属于延迟域（bound tools 之外的可执行工具）"""
        return (
            self._snapshot is not None and tool_name in self._snapshot.name_to_provider
        )

    # ---- 发现（零建连，读快照） ----

    def list_info(
        self, provider: str = "", page_size: int = 50, page_idx: int = 0
    ) -> Dict[str, Any]:
        """总览：工具名+一行截断描述（无 schema）"""
        if self._snapshot is None:
            return {"success": False, "error": "注册表未初始化"}
        entries: List[Dict[str, str]] = []
        keys = (
            [provider]
            if provider and provider in self._snapshot.index
            else self._snapshot.provider_keys
        )
        for key in keys:
            for meta in self._snapshot.index.get(key, []):
                desc = (meta.get("description") or "").strip()
                if len(desc) > DESC_ONE_LINE_MAX:
                    desc = desc[:DESC_ONE_LINE_MAX] + "…"
                entries.append(
                    {"provider": key, "name": meta["name"], "description": desc}
                )
        ps = max(1, int(page_size))
        pi = max(0, int(page_idx))
        page = entries[pi * ps : (pi + 1) * ps]
        return {
            "success": True,
            "tools": page,
            "total": len(entries),
            "page_idx": pi,
            "page_size": ps,
            "providers": self._snapshot.provider_counts,
        }

    async def search(self, pattern: str, limit: int = 10) -> Dict[str, Any]:
        """精确查找：正则匹配工具名+描述，返回完整 input_schema"""
        if self._snapshot is None:
            return {"success": False, "error": "注册表未初始化"}
        pattern = (pattern or "").strip()
        if not pattern:
            return {
                "success": False,
                "error": "pattern 不能为空（总览用 tool_info_list）；"
                '示例: pattern="issue|pull" 查 issue 相关工具',
            }
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return {"success": False, "error": f"正则无效: {e}"}

        results: List[Dict[str, Any]] = []
        for key, provider in self._providers.items():
            if len(results) >= limit:
                break
            try:
                matched = await provider.search(pattern, limit=limit - len(results))
            except Exception as e:
                logger.warning("延迟域 provider %s 搜索失败: %s", key, e)
                continue
            for item in matched:
                results.append(
                    {
                        "provider": key,
                        "name": item.get("name", ""),
                        "description": (item.get("description") or "")[:500],
                        "input_schema": serialize_tool_schema(item.get("input_schema")),
                    }
                )
        return {
            "success": True,
            "tools": results,
            "total": len(results),
            "note": "用 tool_execute(tool_name=..., arguments='JSON') 执行查到的工具",
        }

    # ---- 物化（首次建连/取对象，缓存复用） ----

    async def materialize(self, tool_name: str) -> BaseTool:
        """物化工具供 tool_execute 调用。

        Raises:
            KeyError: 工具名不在延迟域
            ValueError: 物化失败（建连失败/工具不存在等）
        """
        if self._snapshot is None:
            raise KeyError("注册表未初始化")
        provider_key = self._snapshot.name_to_provider.get(tool_name)
        if provider_key is None:
            raise KeyError(
                f"工具 {tool_name} 不在延迟域；用 tool_info_list 查看可用工具，"
                "或 tool_search 按关键词查找"
            )
        async with self._materialize_lock:
            cached = self._materialized.get(tool_name)
            if cached is not None:
                return cached.tool
            provider = self._providers[provider_key]
            tool = await provider.materialize(tool_name)
            if tool is None:
                raise ValueError(
                    f"工具 {tool_name} 物化失败：provider {provider_key} "
                    "未返回该工具（可能已被服务器移除，缓存索引过期）"
                )
            self._materialized[tool_name] = _MaterializedTool(
                tool=tool, provider_key=provider_key
            )
            logger.info("延迟工具已物化: %s (provider=%s)", tool_name, provider_key)
            return tool


def create_deferred_meta_tools(
    registry: DeferredToolRegistry,
) -> List[BaseTool]:
    """构建三元元工具（description 纯静态——前缀缓存不变量）

    返回 [tool_info_list, tool_search_tool_regex, tool_execute]，全部为
    普通 StructuredTool，走 handle_tool_calls 统一执行链路（审批/截断/
    ToolCall 事件在外层生效，零特殊处理）。
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    class InfoListInput(BaseModel):
        """tool_info_list 输入"""

        provider: str = Field(
            "", description="按 provider 过滤（如 mcp:3），空=全部；分页查看用"
        )
        page_size: int = Field(50, description="每页条数（默认 50）")
        page_idx: int = Field(0, description="页码（0 起）")

    class SearchInput(BaseModel):
        """tool_search 输入"""

        pattern: str = Field(
            ...,
            description=(
                "正则表达式，匹配工具名与描述（不区分大小写）。"
                '示例: "issue|pull"、"^github_"、"send.*message"'
            ),
        )
        limit: int = Field(10, description="最多返回条数（默认 10）")

    class ExecuteInput(BaseModel):
        """tool_execute 输入"""

        tool_name: str = Field(
            ..., description="要执行的延迟域工具名（来自 tool_info_list/tool_search）"
        )
        arguments: Union[dict, str] = Field(
            default_factory=dict,
            description=(
                '工具参数，直接传 JSON 对象（如 {"query": "test"}，推荐）；'
                "也兼容 JSON 字符串形式。以工具的 input_schema 为准，无参工具传 {}"
            ),
        )

    async def tool_info_list(
        provider: str = "", page_size: int = 50, page_idx: int = 0
    ) -> dict:
        return registry.list_info(
            provider=provider, page_size=page_size, page_idx=page_idx
        )

    async def tool_search(pattern: str, limit: int = 10) -> dict:
        return await registry.search(pattern, limit=limit)

    async def tool_execute(
        tool_name: str, arguments: Union[dict, str, None] = None
    ) -> dict:
        try:
            tool = await registry.materialize(tool_name)
        except KeyError as e:
            return {"success": False, "error": str(e)}
        except ValueError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"工具物化失败: {type(e).__name__}: {e}"}

        # 归一化 arguments：object 直传（推荐，免双重编码转义错误）；
        # str 兼容解析 JSON（含双重编码自愈：'"{\\"k\\": 1}"'、'""' 空串）；
        # None/缺省 = 无参
        args: dict = {}
        if isinstance(arguments, dict):
            args = arguments
        elif isinstance(arguments, str) and arguments.strip():
            try:
                parsed = json.loads(arguments)
                # 双重编码自愈：模型常把 object/空串再编码一层字符串
                # （如 '"{\\"k\\": 1}"'、'""'）——剥一层后重试一次
                if isinstance(parsed, str):
                    stripped = parsed.strip()
                    if not stripped:
                        parsed = {}
                    elif stripped.startswith("{"):
                        parsed = json.loads(stripped)
                if not isinstance(parsed, dict):
                    return {
                        "success": False,
                        "error": (
                            f"arguments 必须是 JSON 对象（收到 "
                            f'{type(parsed).__name__}），如 {{"key": "value"}}；'
                            "无参工具传 {{}} 或直接省略 arguments"
                        ),
                    }
                args = parsed
            except json.JSONDecodeError as e:
                return {
                    "success": False,
                    "error": (
                        f"arguments JSON 解析失败（位置 {e.pos}）: {e.msg}；"
                        "推荐直接传 JSON 对象（不要用字符串包裹），或用 tool_search "
                        "查看该工具的 input_schema 确认参数格式"
                    ),
                }

        # 参数校验：走工具自身 args_schema（错误详情回传，模型自修正）
        try:
            schema = getattr(tool, "args_schema", None)
            if schema is not None:
                if isinstance(schema, type) and issubclass(schema, BaseModel):
                    # pydantic 模型类：完整校验（内置族工具）
                    args = schema(**args).model_dump(exclude_none=True)
                elif isinstance(schema, dict):
                    # 原始 JSON schema dict（langchain_mcp_adapters 的 MCP
                    # 工具形态）：只做 required 必填轻量校验后透传——
                    # 真实参数校验由 MCP 服务器端执行
                    missing = [k for k in schema.get("required", []) if k not in args]
                    if missing:
                        return {
                            "success": False,
                            "error": (
                                f"缺少必填参数: {missing}；"
                                f"用 tool_search(pattern='{tool_name}') 查看 "
                                "input_schema 后补齐"
                            ),
                        }
        except Exception as e:
            return {
                "success": False,
                "error": (
                    f"参数校验失败: {e}；用 tool_search(pattern='{tool_name}') "
                    "查看 input_schema 后修正参数"
                ),
            }

        try:
            result = await tool.ainvoke(args)
        except Exception as e:
            return {
                "success": False,
                "error": f"工具执行失败: {type(e).__name__}: {e}",
            }
        # 成功时直接透传工具原始输出（不包 success/tool/result 壳）：
        # 模型少剥一层、少耗 token；失败路径仍为 {"success": False, "error"}
        if result is None:
            return {"note": f"工具 {tool_name} 执行成功，无返回内容"}
        return result

    return [
        StructuredTool(
            name="tool_info_list",
            description=(
                "查看延迟加载工具域的总览菜单（工具名+一行说明，不含参数详情）。"
                "当需要的能力没有现成工具时，先用它看看有哪些可用工具，"
                "再用 tool_search 查参数详情，最后 tool_execute 执行"
            ),
            args_schema=InfoListInput,
            func=None,
            coroutine=tool_info_list,
        ),
        StructuredTool(
            name="tool_search_tool_regex",
            description=(
                "按正则精确查找延迟域工具，返回完整参数 schema（input_schema）。"
                "已知要找什么能力时直接用它（比 tool_info_list 更精准）；"
                "查到后用 tool_execute 执行"
            ),
            args_schema=SearchInput,
            func=None,
            coroutine=tool_search,
        ),
        StructuredTool(
            name="tool_execute",
            description=(
                "执行延迟域工具（tool_info_list/tool_search 查到的）。"
                "arguments 直接传该工具 input_schema 约定的 JSON 对象"
                "（推荐，免转义错误）；返回值为工具的原始输出（不再包装）；"
                '失败时返回 {"success": false, "error": 原因}，修正后重试即可'
            ),
            args_schema=ExecuteInput,
            func=None,
            coroutine=tool_execute,
        ),
    ]


def build_deferred_reminder(snapshot: DeferredSnapshot) -> str:
    """构建延迟域 system-reminder 片段（纯静态快照，会话内不变）"""
    if snapshot.total_tools == 0:
        return ""
    providers_desc = ", ".join(
        f"{key}({count}个)" for key, count in snapshot.provider_counts.items()
    )
    return (
        f"<deferred_tools>\n"
        f"以下工具族按需加载（共 {snapshot.total_tools} 个工具，未直接绑定）：\n"
        f"{providers_desc}\n"
        f"使用流程：tool_info_list 看总览 → tool_search_tool_regex 查参数 schema "
        f"→ tool_execute 执行。这些工具不在你的直接工具列表中，"
        f"必须通过上述三个元工具访问。\n"
        f"</deferred_tools>"
    )

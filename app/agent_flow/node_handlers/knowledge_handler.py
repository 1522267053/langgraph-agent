"""
知识库节点处理器

支持两种使用方式：
1. 作为独立执行节点：根据输入变量查询知识库，返回匹配的文档片段
2. 作为工具提供者：连接到LLM节点时提供三层知识库导航工具 + 知识沉淀工具
   （实现见 tools/knowledge_toolkit.py，本 handler 只负责注册与依赖注入）

工具清单（knowledge_{node_key}_ 前缀）：
- search: 全局向量搜索（优先AI沉淀，兜底原始文档）
- title_search: 获取文档列表 / 文档标题树
- get_paragraphs: 获取标题下的段落内容
- adjacent: 查看相邻段落
- title_lookup: 段落反向查找标题
- save_insight / list_insight / delete_insight: 知识沉淀管理
- save_document / update_document / delete_document / list_document:
  文档编辑（按 enable_document_edit 开关注册）
"""

import logging
from typing import Optional, TYPE_CHECKING
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.types import StreamWriter
from pydantic import BaseModel, Field, BeforeValidator
from typing import Annotated

from app.models.flow_node import FlowNode
from app.agent_flow.flow_context import FlowState
from app.agent_flow.node_handlers.base_handler import (
    BaseNodeHandler,
    BaseNodeConfig,
    NodeVariable,
)
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.tools.knowledge_toolkit import (
    KnowledgeToolKit,
    KNOWLEDGE_CITATION_PROMPT,
)
from app.config.database import AsyncSessionLocal
from app.services.knowledge_base_service import knowledge_base_service
from app.services.knowledge_title_service import knowledge_title_service

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.agent_flow.tool_resolver import LlmToolConfig


class KnowledgeSearchInput(BaseModel):
    """知识库检索工具输入参数（独立节点模式）"""

    query: str = Field(..., description="检索查询文本")


# AI 写入文档的字符数上限（content 列为 Text 类型）
_MAX_DOCUMENT_CHARS = 20000


class KnowledgeNodeConfig(BaseNodeConfig):
    output_variables: list[NodeVariable] = [
        NodeVariable(name="result"),
    ]
    knowledge_base_id: Annotated[
        Optional[int], BeforeValidator(lambda v: None if not v else int(v))
    ] = None
    knowledge_base_name: str = ""
    top_k: Optional[int] = Field(default=5, ge=1, le=50)
    enable_document_edit: bool = True
    # 混合检索参数（None/缺省时回退 settings 全局默认值）
    hybrid_enabled: Optional[bool] = True
    rrf_k: Optional[int] = Field(default=60, ge=1)
    keyword_top_k: Optional[int] = Field(default=20, ge=1, le=100)


@NodeHandlerRegistry.register("knowledge")
class KnowledgeNodeHandler(BaseNodeHandler):
    """
    知识库节点处理器

    工具模式下提供三层导航 + 知识沉淀（实现见 tools/knowledge_toolkit.py）。
    """

    ConfigClass = KnowledgeNodeConfig

    # 知识库族参与 auto 阈值判定：每节点 8~12 个工具（带 node_key 前缀），
    # 多知识库节点时是弹性膨胀源；索引名与 get_tool 工具名同源
    # （knowledge_{node_key}_*），物化走基类默认实现（get_tool 零建连）。
    defer_policy = "auto"

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState | dict:
        """执行知识库节点（独立节点模式，使用全局向量搜索）"""

        input_data = self.__class__.get_input_content(
            node, state, self._resolver, node.base_config or {}
        )

        knowledge_base_id = input_data.get("knowledge_base_id") if input_data else None
        top_k = input_data.get("top_k", 5) if input_data else 5
        cfg = self._get_config(node)

        if not knowledge_base_id:
            state.add_error(node.node_key, "未配置知识库")
            return state

        query_text = str(input_data.get("query", "")) if input_data else ""

        if not query_text:
            state.add_error(node.node_key, "查询文本为空，请配置输入变量")
            return state

        try:
            async with AsyncSessionLocal() as db:
                results = await knowledge_title_service.search(
                    db,
                    knowledge_base_id,
                    query_text,
                    top_k,
                    hybrid_enabled=cfg.hybrid_enabled,
                    rrf_k=cfg.rrf_k,
                    keyword_top_k=cfg.keyword_top_k,
                )
                score_map = {r["segment_id"]: r.get("score") for r in results}
                method_map = {
                    r["segment_id"]: r.get("retrieval_method") for r in results
                }
                references = await knowledge_title_service.resolve_segment_references(
                    db,
                    knowledge_base_id,
                    [r["segment_id"] for r in results],
                    score_by_segment_id=score_map,
                    retrieval_method_by_segment_id=method_map,
                )
            marker_map = {
                reference["segment_id"]: reference["citation_marker"]
                for reference in references
            }
            results = [
                {**result, "citation_marker": marker_map.get(result["segment_id"])}
                for result in results
                if result["segment_id"] in marker_map
            ]
            output_names = self._resolve_output_var_names(node)
            result_name = output_names[0] if output_names else "result"
            state.set_node_variable(node.node_key, result_name, results)
            state.set_node_variable(node.node_key, "knowledge_references", references)
        except Exception as e:
            state.add_error(node.node_key, f"知识库查询失败: {str(e)}")
            logger.exception(e)

        return state

    @classmethod
    def get_input_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        """获取Knowledge节点的输入内容"""
        if config is None:
            config = node.base_config or {}
        input_data = {}

        input_vars = config.get("input_variables", [])
        for var in input_vars:
            name = var.get("name", "")
            source = var.get("source", "")
            if name and source:
                value = resolver.resolve_safe(source, state)
                input_data[name] = value

        if config.get("knowledge_base_id"):
            input_data["knowledge_base_id"] = config.get("knowledge_base_id")
        if config.get("top_k"):
            input_data["top_k"] = config.get("top_k", 5)

        return input_data if input_data else None

    @classmethod
    def get_output_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        """获取Knowledge节点的输出内容"""
        output = {}
        # 默认输出名来自 ConfigClass.output_variables（与 execute 写入同源）
        for name in cls._resolve_output_var_names(node):
            value = state.get_node_variable(node.node_key, name)
            if value is not None:
                output[name] = value

        return output if output else None

    async def get_system_prompt_hint(self, node: FlowNode) -> Optional[str]:
        """获取知识库系统提示词（含沉淀层说明）"""
        cfg = self._get_config(node)
        knowledge_base_id = cfg.knowledge_base_id

        if not knowledge_base_id:
            return None

        try:
            async with AsyncSessionLocal() as db:
                kb = await knowledge_base_service.get_by_id(db, knowledge_base_id)
                if not kb:
                    return None

                name = kb.name or "知识库"
                description = kb.description or ""

                # 工具名规则必须写「knowledge_<node_key>_<动作>」模式而非渲染真实
                # node_key：node_key 是用户任意命名，逐字渲染会让每个 knowledge
                # 节点产生一份不同文本，破坏 system_prompt 前缀缓存（AGENTS.md
                # 强制不变量）。静态模式化描述跨节点逐字节一致，模型按运行时
                # 工具列表中的真实前缀套用即可。
                static_prefix = (
                    "\n\n## 知识库\n"
                    "你已连接知识库，可通过工具浏览其文档内容。"
                    "知识库工具名均带节点前缀，模式为 `knowledge_<node_key>_<动作>`"
                    "（<node_key> 为该知识库节点的标识，以运行时工具列表中的实际名称为准，"
                    "不要按裸短名如 knowledge_save_insight 拼名）。\n"
                    "\n沉淀策略（仅在以下时机使用 knowledge_<node_key>_save_insight）：\n"
                    "1. 综合多个段落得出完整答案时 — 保存总结（question写原始问题，answer写综合答案）\n"
                    "2. 多次导航拼凑出复杂主题全貌时 — 保存最终结论\n"
                    "3. 重复回答同类问题时 — 首次整理后保存，后续搜索可命中\n"
                    "4. 发现跨文档的关联知识时 — 保存分析结论\n"
                    "\n不需要保存的情况：直接引用单个段落、临时性回答、不确定准确的信息\n"
                    "保存前可用 knowledge_<node_key>_list_insight 查看已有沉淀，避免重复保存同类结论\n"
                )
                if cfg.enable_document_edit:
                    static_prefix += (
                        "\n文档沉淀（knowledge_<node_key>_save_document"
                        " / knowledge_<node_key>_update_document"
                        " / knowledge_<node_key>_delete_document）：\n"
                        "当用户要求把知识整理进知识库、或成体系的知识值得长期沉淀时，写成 Markdown 文档：\n"
                        "1. 保存前先用 knowledge_<node_key>_title_search 查看文档列表，"
                        "同主题文档已存在则用 knowledge_<node_key>_update_document 全量覆盖更新（不是追加）\n"
                        "2. 内容用 # 层级标题组织，系统会按标题生成标题树并分段向量化\n"
                        "3. 保存后约1分钟完成分段向量化，期间检索不到属正常现象，不要重复保存\n"
                        "零散的单点结论仍优先用 knowledge_<node_key>_save_insight，成体系资料用文档沉淀\n"
                    )
                static_prefix += (
                    "\n工具调用路径：知识库工具可能未直接绑定在你的工具列表中（按需加载），"
                    "此时通过 tool_info_list → tool_search_tool_regex → tool_execute 三步访问；"
                    "动作名清单：search/title_search/get_paragraphs/adjacent/title_lookup/"
                    "save_insight/list_insight/delete_insight"
                    "[/save_document/update_document/delete_document/list_document]"
                )
                dynamic_suffix = f"\n知识库名称：{name}"
                if description:
                    dynamic_suffix += f"\n简介：{description}"
                return static_prefix + KNOWLEDGE_CITATION_PROMPT + dynamic_suffix
        except Exception:
            return None

    async def get_tool(self, node: FlowNode) -> list[BaseTool] | None:
        """组装 KnowledgeToolKit（节点配置解析后注入）"""
        cfg = self._get_config(node)
        knowledge_base_id = cfg.knowledge_base_id

        if not knowledge_base_id:
            return None

        kit = KnowledgeToolKit(
            kb_id=knowledge_base_id,
            node_name=node.node_name or "知识库",
            tool_prefix=f"knowledge_{node.node_key}",
            enable_document_edit=cfg.enable_document_edit,
            hybrid_enabled=cfg.hybrid_enabled,
            rrf_k=cfg.rrf_k,
            keyword_top_k=cfg.keyword_top_k,
        )
        return list(await kit.build_tools())

    @classmethod
    def get_tool_config(cls, node: FlowNode, config: "LlmToolConfig") -> bool:
        """将Knowledge节点配置添加到工具配置"""
        config.knowledge_node_keys.append(node.node_key)
        node_config = node.base_config or {}
        config.knowledge_configs[node.node_key] = {
            "knowledge_base_id": node_config.get("knowledge_base_id"),
            "top_k": node_config.get("top_k", 5),
            "score_threshold": node_config.get("score_threshold", 0.5),
            "name": node_config.get("node_name", "知识库导航"),
            "description": node_config.get("description", "三层知识库导航工具"),
        }
        return True

    @classmethod
    def get_tool_info(cls, node: FlowNode) -> list[dict]:
        """委托 toolkit 单源派生（含幽灵索引守卫，描述与 get_tool 逐字节一致）"""
        return KnowledgeToolKit.tool_info(node)

    # get_provider：继承基类默认实现（get_tool_info 索引 + get_tool 物化），
    # auto 族由 setup_tool_handlers 按阈值判定登记进延迟域

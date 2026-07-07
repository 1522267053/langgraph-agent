"""
知识库节点处理器

支持两种使用方式：
1. 作为独立执行节点：根据输入变量查询知识库，返回匹配的文档片段
2. 作为工具提供者：连接到LLM节点时提供三层知识库导航工具 + 知识沉淀工具
   - knowledge_search: 全局向量搜索（优先AI沉淀，兜底原始文档）
   - knowledge_title_search: 获取文档列表 / 文档标题树
   - knowledge_get_paragraphs: 获取标题下的段落内容
   - knowledge_adjacent: 查看相邻段落
   - knowledge_title_lookup: 段落反向查找标题
   - knowledge_save_insight: 保存知识沉淀（供后续复用）
   - knowledge_delete_insight: 删除知识沉淀
"""

import logging
from typing import Optional, TYPE_CHECKING
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool, BaseTool
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
from app.config.database import AsyncSessionLocal
from app.services.knowledge_base_service import knowledge_base_service
from app.services.knowledge_title_service import knowledge_title_service
from app.services.knowledge_insight_service import knowledge_insight_service

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.agent_flow.tool_resolver import LlmToolConfig


class KnowledgeSearchInput(BaseModel):
    """知识库检索工具输入参数（独立节点模式）"""

    query: str = Field(..., description="检索查询文本")


class TitleSearchInput(BaseModel):
    """标题搜索工具输入参数"""

    doc_id: Optional[int] = Field(
        None,
        description="文档ID。不传时返回知识库下的文档列表，传入时返回该文档的标题树",
    )


class GetParagraphsInput(BaseModel):
    """获取段落工具输入参数"""

    title_id: int = Field(..., description="标题ID，通过 knowledge_title_search 获取")


class AdjacentInput(BaseModel):
    """相邻段落工具输入参数"""

    segment_id: int = Field(
        ..., description="段落ID，通过 knowledge_get_paragraphs 获取"
    )
    direction: str = Field(
        "both",
        description="查看方向：prev（上一个）、next（下一个）、both（上下都看）",
    )


class TitleLookupInput(BaseModel):
    """标题反向查找工具输入参数"""

    segment_id: int = Field(
        ..., description="段落ID，通过 knowledge_get_paragraphs 获取"
    )


class VectorSearchInput(BaseModel):
    """全局向量搜索工具输入参数"""

    query: str = Field(..., description="语义搜索文本")
    top_k: int = Field(5, description="返回结果数量，默认5")


class SaveInsightInput(BaseModel):
    """保存知识沉淀工具输入参数"""

    question: str = Field(..., description="触发问题/查询（用于后续语义检索）")
    answer: str = Field(..., description="AI生成的知识沉淀内容")
    keywords: Optional[str] = Field(None, description="关键词（逗号分隔，辅助检索）")
    source_segment_ids: Optional[str] = Field(
        None, description="关联的段落ID列表（逗号分隔，用于溯源）"
    )


class DeleteInsightInput(BaseModel):
    """删除知识沉淀工具输入参数"""

    ids: str = Field(..., description="要删除的沉淀ID列表（逗号分隔）")


class KnowledgeNodeConfig(BaseNodeConfig):
    output_variables: list[NodeVariable] = [
        NodeVariable(name="result"),
    ]
    knowledge_base_id: Annotated[
        Optional[int], BeforeValidator(lambda v: None if not v else int(v))
    ] = None
    knowledge_base_name: str = ""
    top_k: int = 5


@NodeHandlerRegistry.register("knowledge")
class KnowledgeNodeHandler(BaseNodeHandler):
    """
    知识库节点处理器

    工具模式下提供三层导航 + 知识沉淀：
    1. knowledge_search → 全局向量搜索（优先AI沉淀，兜底原始文档）
    2. knowledge_title_search → 文档列表 / 标题树
    3. knowledge_get_paragraphs → 标题下的段落
    4. knowledge_adjacent → 相邻段落
    5. knowledge_title_lookup → 段落反向查找标题
    6. knowledge_save_insight → 保存知识沉淀
    7. knowledge_delete_insight → 删除知识沉淀
    """

    ConfigClass = KnowledgeNodeConfig

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

        if not knowledge_base_id:
            state.add_error(node.node_key, "未配置知识库")
            return state

        query_text = str(input_data.get("query", "")) if input_data else ""

        if not query_text:
            state.add_error(node.node_key, "查询文本为空，请配置输入变量")
            return state

        try:
            async with AsyncSessionLocal() as db:
                results = await knowledge_title_service.vector_search(
                    db, knowledge_base_id, query_text, top_k
                )
            output_names = self._get_output_var_names(node, ["result"])
            result_name = output_names[0] if output_names else "result"
            state.set_node_variable(node.node_key, result_name, results)
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
        if config is None:
            config = node.base_config or {}
        output = {}

        output_vars = config.get("output_variables", [])
        if output_vars:
            for var in output_vars:
                name = (
                    var.get("name", "")
                    if isinstance(var, dict)
                    else getattr(var, "name", "")
                )
                if name:
                    value = state.get_node_variable(node.node_key, name)
                    if value is not None:
                        output[name] = value
        else:
            value = state.get_node_variable(node.node_key, f"{node.node_key}_result")
            if value is not None:
                output[f"{node.node_key}_result"] = value

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

                static_prefix = (
                    "\n\n## 知识库\n"
                    "你已连接知识库，可通过工具浏览其文档内容。"
                    "\n\n沉淀策略（仅在以下时机使用 knowledge_save_insight）：\n"
                    "1. 综合多个段落得出完整答案时 — 保存总结（question写原始问题，answer写综合答案）\n"
                    "2. 多次导航拼凑出复杂主题全貌时 — 保存最终结论\n"
                    "3. 重复回答同类问题时 — 首次整理后保存，后续搜索可命中\n"
                    "4. 发现跨文档的关联知识时 — 保存分析结论\n"
                    "\n不需要保存的情况：直接引用单个段落、临时性回答、不确定准确的信息\n"
                )
                dynamic_suffix = f"\n知识库名称：{name}"
                if description:
                    dynamic_suffix += f"\n简介：{description}"
                return static_prefix + dynamic_suffix
        except Exception:
            return None

    async def get_tool(self, node: FlowNode) -> list[BaseTool] | None:
        """返回知识库三层导航工具集 + 知识沉淀工具"""
        cfg = self._get_config(node)
        knowledge_base_id = cfg.knowledge_base_id

        if not knowledge_base_id:
            return None

        node_name = node.node_name or "知识库"
        kb_id = knowledge_base_id
        tool_prefix = f"knowledge_{node.node_key}"

        # ---- 三层导航工具 ----

        async def title_search(doc_id: Optional[int] = None) -> str:
            """浏览知识库文档：不传doc_id返回文档列表，传入doc_id返回该文档的标题树"""
            async with AsyncSessionLocal() as db:
                if doc_id is None:
                    items = await knowledge_title_service.get_document_list(db, kb_id)
                    if not items:
                        return "知识库中没有文档"
                    result = []
                    for item in items:
                        result.append(
                            f"- [文档ID:{item.id}] {item.title}（{item.file_type}，{item.title_count}个标题）"
                        )
                    return "## 文档列表\n" + "\n".join(result)
                else:
                    tree = await knowledge_title_service.get_title_tree(db, doc_id)
                    if not tree:
                        return f"文档{doc_id}没有标题索引"
                    lines = []
                    for t in tree:
                        indent = "  " * (t.level - 1)
                        lines.append(
                            f"{indent}- [标题ID:{t.id}] {t.title}（{t.paragraph_count}段）"
                        )
                    return f"## 标题树（文档ID:{doc_id}）\n" + "\n".join(lines)

        async def get_paragraphs(title_id: int) -> str:
            """获取标题下的所有段落内容"""
            async with AsyncSessionLocal() as db:
                paragraphs = await knowledge_title_service.get_paragraphs_by_title(
                    db, title_id
                )
                if not paragraphs:
                    return f"标题ID:{title_id}下没有段落"
                lines = []
                for p in paragraphs:
                    lines.append(
                        f"### 段落 [ID:{p.id}]（第{p.segment_index}段，{p.word_count}字）\n{p.content}"
                    )
                return "\n\n".join(lines)

        async def adjacent(segment_id: int, direction: str = "both") -> str:
            """查看相邻段落：direction可选 prev/next/both"""
            async with AsyncSessionLocal() as db:
                result = await knowledge_title_service.get_adjacent_segments(
                    db, segment_id, direction
                )
                parts = []
                if result.prev:
                    parts.append(
                        f"### 上一个段落 [ID:{result.prev.id}]\n{result.prev.content}"
                    )
                if result.current:
                    parts.append(
                        f"### 当前段落 [ID:{result.current.id}]\n{result.current.content}"
                    )
                if result.next:
                    parts.append(
                        f"### 下一个段落 [ID:{result.next.id}]\n{result.next.content}"
                    )
                if not parts:
                    return f"未找到段落ID:{segment_id}"
                return "\n\n".join(parts)

        async def title_lookup(segment_id: int) -> str:
            """查看段落所属的标题及文档标题树"""
            async with AsyncSessionLocal() as db:
                result = await knowledge_title_service.get_title_for_segment(
                    db, segment_id
                )
                parts = []
                if result.current_title:
                    t = result.current_title
                    parts.append(
                        f"### 当前所属标题\n[标题ID:{t.id}] {t.title}（级别{t.level}，{t.paragraph_count}段）"
                    )
                else:
                    parts.append("该段落不属于任何标题")
                if result.title_tree:
                    tree_lines = []
                    for t in result.title_tree:
                        indent = "  " * (t.level - 1)
                        marker = (
                            " ◀ 当前"
                            if result.current_title and t.id == result.current_title.id
                            else ""
                        )
                        tree_lines.append(
                            f"{indent}- [ID:{t.id}] {t.title}（{t.paragraph_count}段）{marker}"
                        )
                    parts.append("### 文档标题树\n" + "\n".join(tree_lines))
                return "\n\n".join(parts)

        # ---- 搜索工具（优先AI沉淀，兜底原始文档） ----

        async def vector_search(query: str, top_k: int = 5) -> str:
            """全局语义搜索段落（优先匹配AI沉淀的知识，未命中时检索原始文档）"""
            async with AsyncSessionLocal() as db:
                # ① 先查 AI 沉淀层
                insights = await knowledge_insight_service.search(
                    db, kb_id, query, top_k
                )

                insight_lines = []
                for i, item in enumerate(insights, 1):
                    seg_refs = ""
                    if item.get("source_segment_ids"):
                        seg_ids = ", ".join(
                            f"[段落ID:{sid}]" for sid in item["source_segment_ids"]
                        )
                        seg_refs = f"\n- 关联段落：{seg_ids}"

                    insight_lines.append(
                        f"### 结果{i}（相似度:{item['score']}）[来源：AI沉淀，沉淀ID:{item['id']}]\n"
                        f"- 问题：{item['question']}\n"
                        f"{item['answer']}{seg_refs}"
                    )

                # ② 沉淀结果足够好时直接返回
                if insights and len(insights) >= 3 and insights[0]["score"] > 0.6:
                    return "## 搜索结果（AI沉淀）\n\n" + "\n\n".join(insight_lines)

                # ③ 补充查原始文档
                doc_results = await knowledge_title_service.vector_search(
                    db, kb_id, query, top_k
                )

                doc_lines = []
                for i, r in enumerate(doc_results, 1):
                    doc_title = r["document_title"] or "未知文档"
                    title_text = r["title_text"] or "无标题"
                    doc_lines.append(
                        f"### 结果{i}（相似度:{r['score']}）[来源：原始文档]\n"
                        f"- 文件：[文档ID:{r['document_id']}] {doc_title}\n"
                        f"- 标题：[标题ID:{r['title_id']}] {title_text}\n"
                        f"- 段落：[段落ID:{r['segment_id']}]\n"
                        f"{r['content']}"
                    )

                # ④ 合并结果
                parts = []
                if insight_lines:
                    parts.append("## AI沉淀\n\n" + "\n\n".join(insight_lines))
                if doc_lines:
                    parts.append("## 原始文档\n\n" + "\n\n".join(doc_lines))

                if not parts:
                    return "未找到相关内容"

                return "\n\n---\n\n".join(parts)

        # ---- 知识沉淀工具 ----

        async def save_insight(
            question: str,
            answer: str,
            keywords: Optional[str] = None,
            source_segment_ids: Optional[str] = None,
        ) -> str:
            """将有价值的知识总结保存到知识库沉淀层，供后续对话复用"""
            seg_ids = None
            if source_segment_ids:
                try:
                    seg_ids = [
                        int(sid.strip())
                        for sid in source_segment_ids.split(",")
                        if sid.strip()
                    ]
                except ValueError:
                    return f"关联段落ID格式错误: {source_segment_ids}"

            async with AsyncSessionLocal() as db:
                insight = await knowledge_insight_service.save_insight(
                    db,
                    knowledge_base_id=kb_id,
                    question=question,
                    answer=answer,
                    keywords=keywords,
                    source_segment_ids=seg_ids,
                )

                seg_info = ""
                if seg_ids:
                    seg_info = f"，关联段落: {source_segment_ids}"

                return f"知识沉淀已保存，沉淀ID: {insight.id}{seg_info}"

        async def delete_insight(ids: str) -> str:
            """删除知识沉淀，ids为逗号分隔的沉淀ID列表"""
            try:
                id_list = [int(i.strip()) for i in ids.split(",") if i.strip()]
            except ValueError:
                return f"沉淀ID格式错误: {ids}"

            if not id_list:
                return "未提供有效的沉淀ID"

            async with AsyncSessionLocal() as db:
                result = await knowledge_insight_service.delete_batch_by_ids(
                    db, id_list
                )
                return (
                    f"删除完成: 请求{result['total']}条，实际删除{result['deleted']}条"
                )

        return [
            StructuredTool(
                name=f"{tool_prefix}_search",
                description=f"全局语义搜索知识库「{node_name}」中的段落内容（优先匹配AI沉淀的知识，未命中时检索原始文档），返回匹配的文件名、标题和段落",
                func=None,
                coroutine=vector_search,
                args_schema=VectorSearchInput,
            ),
            StructuredTool(
                name=f"{tool_prefix}_title_search",
                description=f"浏览知识库「{node_name}」的文档列表或文档标题树。不传doc_id返回文档列表，传入doc_id返回该文档的标题树",
                func=None,
                coroutine=title_search,
                args_schema=TitleSearchInput,
            ),
            StructuredTool(
                name=f"{tool_prefix}_get_paragraphs",
                description=f"获取知识库「{node_name}」中指定标题下的所有段落内容",
                func=None,
                coroutine=get_paragraphs,
                args_schema=GetParagraphsInput,
            ),
            StructuredTool(
                name=f"{tool_prefix}_adjacent",
                description=f"查看知识库「{node_name}」中指定段落的相邻段落，用于上下文翻页",
                func=None,
                coroutine=adjacent,
                args_schema=AdjacentInput,
            ),
            StructuredTool(
                name=f"{tool_prefix}_title_lookup",
                description=f"查看知识库「{node_name}」中段落所属的标题位置及完整标题树，用于定位方向",
                func=None,
                coroutine=title_lookup,
                args_schema=TitleLookupInput,
            ),
            StructuredTool(
                name=f"{tool_prefix}_save_insight",
                description=f"将有价值的知识总结保存到知识库「{node_name}」的沉淀层，供后续对话复用。可传入 source_segment_ids 关联来源段落（逗号分隔）",
                func=None,
                coroutine=save_insight,
                args_schema=SaveInsightInput,
            ),
            StructuredTool(
                name=f"{tool_prefix}_delete_insight",
                description=f"删除知识库「{node_name}」中不再需要的知识沉淀，传入逗号分隔的沉淀ID列表",
                func=None,
                coroutine=delete_insight,
                args_schema=DeleteInsightInput,
            ),
        ]

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
        node_key = node.node_key
        tool_prefix = f"knowledge_{node_key}"
        return [
            {
                "name": f"{tool_prefix}_search",
                "description": "全局语义搜索知识库段落内容",
            },
            {
                "name": f"{tool_prefix}_title_search",
                "description": "浏览知识库文档列表或标题树",
            },
            {
                "name": f"{tool_prefix}_get_paragraphs",
                "description": "获取指定标题下的所有段落",
            },
            {
                "name": f"{tool_prefix}_adjacent",
                "description": "查看指定段落的相邻段落",
            },
            {
                "name": f"{tool_prefix}_title_lookup",
                "description": "查看段落所属的标题位置",
            },
            {"name": f"{tool_prefix}_save_insight", "description": "保存知识沉淀"},
            {"name": f"{tool_prefix}_delete_insight", "description": "删除知识沉淀"},
        ]

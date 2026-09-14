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
   - knowledge_save_document: 将 Markdown 内容保存为知识库文档（同名覆盖更新）
   - knowledge_update_document: 全量覆盖更新文档内容并重建分段
   - knowledge_delete_document: 删除文档及其分段与向量
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
from app.services.knowledge_document_service import knowledge_document_service
from app.utils.knowledge_reference import (
    build_knowledge_result,
    merge_knowledge_references,
)

logger = logging.getLogger(__name__)

# 知识库引用规则（知识工具结果 / system_prompt hint / 消息层 reminder 共用）
KNOWLEDGE_CITATION_PROMPT = (
    "\n\n知识库片段引用规则：知识片段中的 [段落ID:x] 是可验证引用标记。"
    "回答使用某个片段的事实时，必须在对应句子末尾原样保留该标记；"
    "只能使用当前上下文或工具结果中实际出现的标记，不得自行编造；"
    "未使用知识库内容时不要添加引用。"
)

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

    query: str = Field(
        ...,
        description="语义搜索文本，建议用完整的句子或描述（向量检索句子越长越精准）",
    )
    top_k: Optional[int] = Field(
        default=5, ge=1, le=50, description="返回结果数量，默认5"
    )


class SaveInsightInput(BaseModel):
    question: str = Field(..., description="触发问题/查询（用于后续语义检索）")
    answer: str = Field(..., description="AI生成的知识沉淀内容")
    keywords: Optional[list[str]] = Field(
        default=None, description="关键词列表（辅助检索）"
    )
    source_segment_ids: Optional[list[int]] = Field(
        default=None, description="关联的段落ID列表（用于溯源）"
    )


class DeleteInsightInput(BaseModel):
    ids: list[int] = Field(..., description="要删除的沉淀ID列表")


class ListInsightInput(BaseModel):
    """查询知识沉淀列表工具输入参数"""

    page: Optional[int] = Field(default=1, ge=1, description="页码，从1开始，默认1")
    page_size: Optional[int] = Field(
        default=10, ge=1, le=10, description="每页条数，默认10，最大10"
    )


class SaveDocumentInput(BaseModel):
    """保存 Markdown 文档工具输入参数"""

    title: str = Field(
        ..., description="文档标题（简短主题名，知识库内需唯一，重名保存会被拒绝）"
    )
    content: str = Field(
        ...,
        description="完整 Markdown 文档内容，用 # 层级标题组织结构（全量保存，非追加）",
    )


class UpdateDocumentInput(BaseModel):
    """更新文档工具输入参数"""

    document_id: int = Field(
        ..., description="文档ID，通过 knowledge_title_search 获取"
    )
    content: str = Field(
        ..., description="更新后的完整 Markdown 内容（全量覆盖，不是追加）"
    )
    title: Optional[str] = Field(default=None, description="新标题，不传则保持原标题")


class DeleteDocumentInput(BaseModel):
    """删除文档工具输入参数"""

    document_id: int = Field(..., description="要删除的文档ID")


class ListDocumentInput(BaseModel):
    """分页查询文档列表工具输入参数"""

    page: Optional[int] = Field(default=1, ge=1, description="页码，从1开始，默认1")
    page_size: Optional[int] = Field(
        default=10, ge=1, le=50, description="每页条数，默认10，最大50"
    )


# AI 写入文档的字符数上限（content 列为 Text，MySQL 下约 64KB）
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
            output_names = self._get_output_var_names(node, ["result"])
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
        if config is None:
            config = node.base_config or {}
        output = {}

        output_vars = config.get("output_variables", [])
        if output_vars:
            for var in output_vars:
                name = var.get("name", "") if isinstance(var, dict) else var.name
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
                    "保存前可用 knowledge_list_insight 查看已有沉淀，避免重复保存同类结论\n"
                )
                if cfg.enable_document_edit:
                    static_prefix += (
                        "\n文档沉淀（knowledge_save_document / knowledge_update_document"
                        " / knowledge_delete_document）：\n"
                        "当用户要求把知识整理进知识库、或成体系的知识值得长期沉淀时，写成 Markdown 文档：\n"
                        "1. 保存前先用 title_search 查看文档列表，同主题文档已存在则用 update_document"
                        " 全量覆盖更新（不是追加）\n"
                        "2. 内容用 # 层级标题组织，系统会按标题生成标题树并分段向量化\n"
                        "3. 保存后约1分钟完成分段向量化，期间检索不到属正常现象，不要重复保存\n"
                        "零散的单点结论仍优先用 knowledge_save_insight，成体系资料用文档沉淀\n"
                    )
                dynamic_suffix = f"\n知识库名称：{name}"
                if description:
                    dynamic_suffix += f"\n简介：{description}"
                return static_prefix + KNOWLEDGE_CITATION_PROMPT + dynamic_suffix
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
                    documents = await knowledge_title_service.get_document_list(
                        db, kb_id
                    )
                    if not any(item.id == doc_id for item in documents):
                        return f"当前知识库中未找到文档ID:{doc_id}"
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

        async def get_paragraphs(title_id: int) -> str | dict:
            """获取标题下的所有段落内容"""
            async with AsyncSessionLocal() as db:
                paragraphs = await knowledge_title_service.get_paragraphs_by_title(
                    db, title_id
                )
                if not paragraphs:
                    return f"标题ID:{title_id}下没有段落"
                references = await knowledge_title_service.resolve_segment_references(
                    db, kb_id, [paragraph.id for paragraph in paragraphs]
                )
                if not references:
                    return f"当前知识库中未找到标题ID:{title_id}"
                reference_ids = {reference["segment_id"] for reference in references}
                lines = []
                for p in paragraphs:
                    if p.id not in reference_ids:
                        continue
                    lines.append(
                        f"### 段落 [段落ID:{p.id}]（第{p.segment_index}段，{p.word_count}字）\n{p.content}"
                    )
                content = (
                    KNOWLEDGE_CITATION_PROMPT.strip() + "\n\n" + "\n\n".join(lines)
                )
                return build_knowledge_result(content, references)

        async def adjacent(segment_id: int, direction: str = "both") -> str | dict:
            """查看相邻段落：direction可选 prev/next/both"""
            async with AsyncSessionLocal() as db:
                result = await knowledge_title_service.get_adjacent_segments(
                    db, segment_id, direction
                )
                segment_ids = [
                    paragraph.id
                    for paragraph in (result.prev, result.current, result.next)
                    if paragraph is not None
                ]
                references = await knowledge_title_service.resolve_segment_references(
                    db, kb_id, segment_ids
                )
                reference_ids = {reference["segment_id"] for reference in references}
                parts = []
                if result.prev and result.prev.id in reference_ids:
                    parts.append(
                        f"### 上一个段落 [段落ID:{result.prev.id}]\n{result.prev.content}"
                    )
                if result.current and result.current.id in reference_ids:
                    parts.append(
                        f"### 当前段落 [段落ID:{result.current.id}]\n{result.current.content}"
                    )
                if result.next and result.next.id in reference_ids:
                    parts.append(
                        f"### 下一个段落 [段落ID:{result.next.id}]\n{result.next.content}"
                    )
                if not parts:
                    return f"当前知识库中未找到段落ID:{segment_id}"
                content = (
                    KNOWLEDGE_CITATION_PROMPT.strip() + "\n\n" + "\n\n".join(parts)
                )
                return build_knowledge_result(content, references)

        async def title_lookup(segment_id: int) -> str:
            """查看段落所属的标题及文档标题树"""
            async with AsyncSessionLocal() as db:
                references = await knowledge_title_service.resolve_segment_references(
                    db, kb_id, [segment_id]
                )
                if not references:
                    return f"当前知识库中未找到段落ID:{segment_id}"
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

        async def vector_search(query: str, top_k: int = 5) -> str | dict:
            """全局语义搜索段落（优先匹配AI沉淀的知识，未命中时检索原始文档）"""
            async with AsyncSessionLocal() as db:
                # ① 先查 AI 沉淀层
                insights = await knowledge_insight_service.search(
                    db, kb_id, query, top_k
                )

                insight_segment_ids: list[int] = []
                insight_score_map: dict[int, float | None] = {}
                for item in insights:
                    for segment_id in item.get("source_segment_ids") or []:
                        insight_segment_ids.append(segment_id)
                        current_score = insight_score_map.get(segment_id)
                        item_score = item.get("score")
                        if current_score is None or (
                            item_score is not None and item_score > current_score
                        ):
                            insight_score_map[segment_id] = item_score
                insight_method_map = {
                    segment_id: "insight" for segment_id in insight_segment_ids
                }
                insight_references = (
                    await knowledge_title_service.resolve_segment_references(
                        db,
                        kb_id,
                        insight_segment_ids,
                        score_by_segment_id=insight_score_map,
                        retrieval_method_by_segment_id=insight_method_map,
                    )
                )
                valid_insight_segment_ids = {
                    reference["segment_id"] for reference in insight_references
                }

                insight_lines = []
                for i, item in enumerate(insights, 1):
                    seg_refs = ""
                    if item.get("source_segment_ids"):
                        seg_ids = ", ".join(
                            f"[段落ID:{sid}]"
                            for sid in item["source_segment_ids"]
                            if sid in valid_insight_segment_ids
                        )
                        if seg_ids:
                            seg_refs = f"\n- 关联段落：{seg_ids}"

                    insight_lines.append(
                        f"### 结果{i}（相似度:{item['score']}）[来源：AI沉淀，沉淀ID:{item['id']}]\n"
                        f"- 问题：{item['question']}\n"
                        f"{item['answer']}{seg_refs}"
                    )

                # ② 沉淀结果足够好时直接返回
                if insights and len(insights) >= 3 and insights[0]["score"] > 0.6:
                    content = (
                        KNOWLEDGE_CITATION_PROMPT.strip()
                        + "\n\n## 搜索结果（AI沉淀）\n\n"
                        + "\n\n".join(insight_lines)
                    )
                    return build_knowledge_result(content, insight_references)

                # ③ 补充查原始文档
                doc_results = await knowledge_title_service.vector_search(
                    db, kb_id, query, top_k
                )
                doc_score_map = {
                    result["segment_id"]: result.get("score") for result in doc_results
                }
                doc_method_map = {
                    result["segment_id"]: result.get("retrieval_method")
                    for result in doc_results
                }
                doc_references = (
                    await knowledge_title_service.resolve_segment_references(
                        db,
                        kb_id,
                        [result["segment_id"] for result in doc_results],
                        score_by_segment_id=doc_score_map,
                        retrieval_method_by_segment_id=doc_method_map,
                    )
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

                content = (
                    KNOWLEDGE_CITATION_PROMPT.strip()
                    + "\n\n"
                    + "\n\n---\n\n".join(parts)
                )
                references = merge_knowledge_references(
                    insight_references, doc_references
                )
                return build_knowledge_result(content, references)

        # ---- 知识沉淀工具 ----

        async def save_insight(
            question: str,
            answer: str,
            keywords: Optional[list[str]] = None,
            source_segment_ids: Optional[list[int]] = None,
        ) -> str:
            async with AsyncSessionLocal() as db:
                insight = await knowledge_insight_service.save_insight(
                    db,
                    knowledge_base_id=kb_id,
                    question=question,
                    answer=answer,
                    keywords=",".join(keywords) if keywords else None,
                    source_segment_ids=source_segment_ids,
                )

                seg_info = ""
                if source_segment_ids:
                    seg_info = (
                        f"，关联段落: {','.join(str(s) for s in source_segment_ids)}"
                    )

                return f"知识沉淀已保存，沉淀ID: {insight.id}{seg_info}"

        async def list_insights(page: int = 1, page_size: int = 10) -> str:
            async with AsyncSessionLocal() as db:
                result = await knowledge_insight_service.list_insights(
                    db, kb_id, page, page_size
                )
            items = result["items"]
            total = result["total"]
            if not items:
                if total == 0:
                    return "知识库中还没有知识沉淀"
                return (
                    f"第{result['page']}页没有数据，"
                    f"共{total}条沉淀，请检查页码是否超出范围"
                )
            total_pages = max(1, -(-total // result["page_size"]))
            header = (
                f"共{total}条，第{result['page']}/{total_pages}页，"
                f"每页{result['page_size']}条"
            )
            lines = [f"## 知识沉淀列表（{header}）"]
            for item in items:
                extra = ""
                if item.get("keywords"):
                    extra += f"\n- 关键词: {item['keywords']}"
                if item.get("source_segment_ids"):
                    seg_ids = ", ".join(
                        f"[段落ID:{sid}]" for sid in item["source_segment_ids"]
                    )
                    extra += f"\n- 关联段落: {seg_ids}"
                lines.append(
                    f"### [沉淀ID:{item['id']}] {item['question']}\n"
                    f"{item['answer']}{extra}"
                )
            return "\n\n".join(lines)

        async def delete_insight(ids: list[int]) -> str:
            if not ids:
                return "未提供有效的沉淀ID"

            async with AsyncSessionLocal() as db:
                result = await knowledge_insight_service.delete_batch_by_ids(
                    db, ids, knowledge_base_id=kb_id
                )
                return (
                    f"删除完成: 请求{result['total']}条，实际删除{result['deleted']}条"
                )

        # ---- 文档编辑工具（AI 沉淀为 Markdown 文档） ----

        async def save_document(title: str, content: str) -> str:
            clean_title = title.strip()
            if not clean_title:
                return "保存失败：标题不能为空"
            if len(content) > _MAX_DOCUMENT_CHARS:
                return (
                    f"保存失败：内容 {len(content)} 字超过上限 {_MAX_DOCUMENT_CHARS} 字，"
                    "请精简内容或拆分为多个文档"
                )
            async with AsyncSessionLocal() as db:
                existing = await knowledge_document_service.get_by_title(
                    db, kb_id, clean_title
                )
                if existing:
                    return (
                        f"保存失败：知识库中已存在同名文档「{clean_title}」"
                        f"（文档ID: {existing.id}）。"
                        "请换一个标题重新保存；若目的是更新该文档内容，请改用 update_document"
                    )
                document = await knowledge_document_service.create_markdown_document(
                    db, kb_id, clean_title, content
                )
                return (
                    f"Markdown 文档已保存（文档ID: {document.id}），"
                    "系统将在约1分钟内自动分段并向量化，完成后即可通过 search 检索到"
                )

        async def update_document(
            document_id: int, content: str, title: Optional[str] = None
        ) -> str:
            if len(content) > _MAX_DOCUMENT_CHARS:
                return (
                    f"更新失败：内容 {len(content)} 字超过上限 {_MAX_DOCUMENT_CHARS} 字，"
                    "请精简内容或拆分为多个文档"
                )
            async with AsyncSessionLocal() as db:
                document = await knowledge_document_service.get_active_document(
                    db, document_id
                )
                if not document or document.knowledge_base_id != kb_id:
                    return f"当前知识库中未找到文档ID:{document_id}"
                await knowledge_document_service.update_markdown_document(
                    db, document, content, title
                )
                return (
                    f"文档（文档ID: {document_id}）已更新，"
                    "系统将在约1分钟内自动重新分段并向量化"
                )

        async def delete_document(document_id: int) -> str:
            async with AsyncSessionLocal() as db:
                document = await knowledge_document_service.get_active_document(
                    db, document_id
                )
                if not document or document.knowledge_base_id != kb_id:
                    return f"当前知识库中未找到文档ID:{document_id}"
                await knowledge_document_service.delete_document_with_segments(
                    db, document_id
                )
                return f"文档（文档ID: {document_id}）及其分段、向量已删除"

        async def list_documents(page: int = 1, page_size: int = 10) -> str:
            status_names = {
                0: "待处理",
                1: "处理中",
                2: "已完成",
                3: "失败",
                4: "向量化中",
            }
            async with AsyncSessionLocal() as db:
                result = await knowledge_document_service.list_documents(
                    db, kb_id, page, page_size
                )
            items = result["items"]
            total = result["total"]
            if not items:
                if total == 0:
                    return "知识库中还没有文档"
                return (
                    f"第{result['page']}页没有数据，"
                    f"共{total}个文档，请检查页码是否超出范围"
                )
            total_pages = max(1, -(-total // result["page_size"]))
            lines = [
                f"## 文档列表（共{total}个，第{result['page']}/{total_pages}页，"
                f"每页{result['page_size']}个）"
            ]
            for item in items:
                status = status_names.get(
                    item["processing_status"], str(item["processing_status"])
                )
                lines.append(
                    f"- [文档ID:{item['id']}] {item['title']}（{item['file_type']}"
                    f"，{item['word_count']}字，{item['segment_count']}段，{status}）"
                )
            return "\n".join(lines)

        tool_metadata = {
            "knowledge_tool": True,
            "knowledge_base_id": kb_id,
        }
        tools = [
            StructuredTool(
                name=f"{tool_prefix}_search",
                description=f"全局语义搜索知识库「{node_name}」中的段落内容（优先匹配AI沉淀的知识，未命中时检索原始文档），返回匹配的文件名、标题和段落。query越完整（句子/描述）越精准，短关键词精度较低。实现说明：已配置向量模型时向量检索优先、无结果自动SQL兜底；未配置向量模型时直接SQL模糊搜索",
                func=None,
                coroutine=vector_search,
                args_schema=VectorSearchInput,
                metadata=tool_metadata,
            ),
            StructuredTool(
                name=f"{tool_prefix}_title_search",
                description=f"浏览知识库「{node_name}」的文档列表或文档标题树。不传doc_id返回文档列表，传入doc_id返回该文档的标题树",
                func=None,
                coroutine=title_search,
                args_schema=TitleSearchInput,
                metadata=tool_metadata,
            ),
            StructuredTool(
                name=f"{tool_prefix}_get_paragraphs",
                description=f"获取知识库「{node_name}」中指定标题下的所有段落内容",
                func=None,
                coroutine=get_paragraphs,
                args_schema=GetParagraphsInput,
                metadata=tool_metadata,
            ),
            StructuredTool(
                name=f"{tool_prefix}_adjacent",
                description=f"查看知识库「{node_name}」中指定段落的相邻段落，用于上下文翻页",
                func=None,
                coroutine=adjacent,
                args_schema=AdjacentInput,
                metadata=tool_metadata,
            ),
            StructuredTool(
                name=f"{tool_prefix}_title_lookup",
                description=f"查看知识库「{node_name}」中段落所属的标题位置及完整标题树，用于定位方向",
                func=None,
                coroutine=title_lookup,
                args_schema=TitleLookupInput,
                metadata=tool_metadata,
            ),
            StructuredTool(
                name=f"{tool_prefix}_save_insight",
                description=f"将有价值的知识总结保存到知识库「{node_name}」的沉淀层，供后续对话复用。可传入 source_segment_ids 关联来源段落（逗号分隔）",
                func=None,
                coroutine=save_insight,
                args_schema=SaveInsightInput,
                metadata=tool_metadata,
            ),
            StructuredTool(
                name=f"{tool_prefix}_list_insight",
                description=f"分页查看知识库「{node_name}」中已保存的全部知识沉淀列表（按保存时间倒序，每页最多10条，page 翻页查看较早的沉淀），用于保存前检查是否已有同类沉淀、或获取沉淀ID以便删除",
                func=None,
                coroutine=list_insights,
                args_schema=ListInsightInput,
                metadata=tool_metadata,
            ),
            StructuredTool(
                name=f"{tool_prefix}_delete_insight",
                description=f"删除知识库「{node_name}」中不再需要的知识沉淀，传入逗号分隔的沉淀ID列表",
                func=None,
                coroutine=delete_insight,
                args_schema=DeleteInsightInput,
                metadata=tool_metadata,
            ),
        ]

        # ---- 文档编辑工具（按节点配置开关注册） ----
        if cfg.enable_document_edit:
            tools.extend(
                [
                    StructuredTool(
                        name=f"{tool_prefix}_save_document",
                        description=(
                            f"将 Markdown 内容作为文档写入知识库「{node_name}」沉淀长期知识"
                            f"（标题在知识库内需唯一，已存在同名文档时保存失败，需换标题；"
                            "更新已有文档请用 update_document）。"
                            "用 # 层级标题组织内容，系统会自动生成标题树并分段向量化。"
                            "单次保存有字数上限，超长请拆分为多个文档"
                        ),
                        func=None,
                        coroutine=save_document,
                        args_schema=SaveDocumentInput,
                        metadata=tool_metadata,
                    ),
                    StructuredTool(
                        name=f"{tool_prefix}_update_document",
                        description=(
                            f"全量覆盖更新知识库「{node_name}」中指定文档的内容（不是追加），"
                            "可选同时改标题，更新后自动重新分段并向量化"
                        ),
                        func=None,
                        coroutine=update_document,
                        args_schema=UpdateDocumentInput,
                        metadata=tool_metadata,
                    ),
                    StructuredTool(
                        name=f"{tool_prefix}_delete_document",
                        description=(
                            f"删除知识库「{node_name}」中指定的文档及其分段与向量数据，"
                            "不可恢复，请谨慎使用"
                        ),
                        func=None,
                        coroutine=delete_document,
                        args_schema=DeleteDocumentInput,
                        metadata=tool_metadata,
                    ),
                    StructuredTool(
                        name=f"{tool_prefix}_list_document",
                        description=(
                            f"分页查看知识库「{node_name}」中的文档列表"
                            "（按保存时间倒序，每页最多50个，含字数/分段数/处理状态），"
                            "用于保存前检查是否已有同类文档、或获取文档ID以便更新/删除"
                        ),
                        func=None,
                        coroutine=list_documents,
                        args_schema=ListDocumentInput,
                        metadata=tool_metadata,
                    ),
                ]
            )

        return tools

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
        info = [
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
            {
                "name": f"{tool_prefix}_list_insight",
                "description": "分页查询知识沉淀列表（按时间倒序，每页最多10条）",
            },
            {"name": f"{tool_prefix}_delete_insight", "description": "删除知识沉淀"},
        ]
        if bool((node.base_config or {}).get("enable_document_edit", True)):
            info.extend(
                [
                    {
                        "name": f"{tool_prefix}_save_document",
                        "description": "将 Markdown 内容保存为知识库文档（重名拒绝，需换标题）",
                    },
                    {
                        "name": f"{tool_prefix}_update_document",
                        "description": "全量覆盖更新指定文档内容并重建分段",
                    },
                    {
                        "name": f"{tool_prefix}_delete_document",
                        "description": "删除指定文档及其分段与向量",
                    },
                    {
                        "name": f"{tool_prefix}_list_document",
                        "description": "分页查询文档列表（含处理状态，每页最多50个）",
                    },
                ]
            )
        return info

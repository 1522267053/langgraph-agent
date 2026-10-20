"""
知识库工具集（KnowledgeToolKit）

从 knowledge_handler.get_tool 抽出的 12 个工具（三层导航 + 知识沉淀 +
文档编辑）：search / title_search / get_paragraphs / adjacent /
title_lookup / save_insight / list_insight / delete_insight /
save_document / update_document / delete_document / list_document。

依赖注入：kb_id / node_name / tool_prefix / hybrid 检索参数 /
enable_document_edit 开关由 handler 从节点配置解析后注入。

描述单源：KNOWLEDGE_TOOL_DESCRIPTIONS 模块级常量（短名 → 描述模板，
{node_name} 占位），tool_info 与 build_tools 共用——描述只在一处维护。

依赖方向：tools/* → app/services/*、app/utils/*、app/config/*；
本模块不 import node_handlers。
"""

from typing import TYPE_CHECKING, Optional, Sequence

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent_flow.tools.base_abs_tool import BaseToolKit
from app.config.database import AsyncSessionLocal
from app.services.knowledge_title_service import knowledge_title_service
from app.services.knowledge_insight_service import knowledge_insight_service
from app.services.knowledge_document_service import knowledge_document_service
from app.utils.knowledge_reference import (
    build_knowledge_result,
    merge_knowledge_references,
)

if TYPE_CHECKING:
    from app.models.flow_node import FlowNode

# 知识库引用规则（知识工具结果共用；与 handler 的 hint/reminder 同文案）
KNOWLEDGE_CITATION_PROMPT = (
    "\n\n知识库片段引用规则：知识片段中的 [段落ID:x] 是可验证引用标记。"
    "回答使用某个片段的事实时，必须在对应句子末尾原样保留该标记；"
    "只能使用当前上下文或工具结果中实际出现的标记，不得自行编造；"
    "未使用知识库内容时不要添加引用。"
)

# AI 写入文档的字符数上限（content 列为 Text 类型）
_MAX_DOCUMENT_CHARS = 20000

# ---- 描述单源（唯一维护点；tool_info 与 build_tools 共用）----
# 值为描述模板，{node_name} 由 build_tools/tool_info 按节点渲染
KNOWLEDGE_TOOL_DESCRIPTIONS: dict[str, str] = {
    "search": (
        "全局语义搜索知识库「{node_name}」中的段落内容（优先匹配AI沉淀的知识，"
        "未命中时检索原始文档），返回匹配的文件名、标题和段落。"
        "query越完整（句子/描述）越精准，短关键词精度较低。"
        "实现说明：已配置向量模型时向量检索优先、无结果自动SQL兜底；"
        "未配置向量模型时直接SQL模糊搜索"
    ),
    "title_search": (
        "浏览知识库「{node_name}」的文档列表或文档标题树。"
        "不传doc_id返回文档列表，传入doc_id返回该文档的标题树"
    ),
    "get_paragraphs": "获取知识库「{node_name}」中指定标题下的所有段落内容",
    "adjacent": ("查看知识库「{node_name}」中指定段落的相邻段落，用于上下文翻页"),
    "title_lookup": (
        "查看知识库「{node_name}」中段落所属的标题位置及完整标题树，用于定位方向"
    ),
    "save_insight": (
        "将有价值的知识总结保存到知识库「{node_name}」的沉淀层，供后续对话复用。"
        "可传入 source_segment_ids 关联来源段落（逗号分隔）"
    ),
    "list_insight": (
        "分页查看知识库「{node_name}」中已保存的全部知识沉淀列表"
        "（按保存时间倒序，每页最多10条，page 翻页查看较早的沉淀），"
        "用于保存前检查是否已有同类沉淀、或获取沉淀ID以便删除"
    ),
    "delete_insight": (
        "删除知识库「{node_name}」中不再需要的知识沉淀，传入逗号分隔的沉淀ID列表"
    ),
    "save_document": (
        "将 Markdown 内容作为文档写入知识库「{node_name}」沉淀长期知识"
        "（标题在知识库内需唯一，已存在同名文档时保存失败，需换标题；"
        "更新已有文档请用 update_document）。"
        "用 # 层级标题组织内容，系统会自动生成标题树并分段向量化。"
        "单次保存有字数上限，超长请拆分为多个文档"
    ),
    "update_document": (
        "全量覆盖更新知识库「{node_name}」中指定文档的内容（不是追加），"
        "可选同时改标题，更新后自动重新分段并向量化"
    ),
    "delete_document": (
        "删除知识库「{node_name}」中指定的文档及其分段与向量数据，不可恢复，请谨慎使用"
    ),
    "list_document": (
        "分页查看知识库「{node_name}」中的文档列表"
        "（按保存时间倒序，每页最多50个，含字数/分段数/处理状态），"
        "用于保存前检查是否已有同类文档、或获取文档ID以便更新/删除"
    ),
}


def _desc(short_name: str, node_name: str) -> str:
    """渲染描述模板（tool_info 与 build_tools 共用的单源出口）"""
    return KNOWLEDGE_TOOL_DESCRIPTIONS[short_name].format(node_name=node_name)


# ---- 工具参数 Schema ----


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


class KnowledgeToolKit(BaseToolKit):
    """知识库工具集（8~12 个工具，按 enable_document_edit 开关）

    由 knowledge_handler.get_tool 构造，注入节点配置解析结果。
    """

    def __init__(
        self,
        kb_id: int,
        node_name: str,
        tool_prefix: str,
        enable_document_edit: bool = True,
        hybrid_enabled: Optional[bool] = True,
        rrf_k: Optional[int] = 60,
        keyword_top_k: Optional[int] = 20,
    ):
        self._kb_id = kb_id
        self._node_name = node_name
        self._tool_prefix = tool_prefix
        self._enable_document_edit = enable_document_edit
        self._hybrid_enabled = hybrid_enabled
        self._rrf_k = rrf_k
        self._keyword_top_k = keyword_top_k

    async def build_tools(self) -> Sequence[StructuredTool]:
        kb_id = self._kb_id
        node_name = self._node_name
        tool_prefix = self._tool_prefix
        cfg_hybrid_enabled = self._hybrid_enabled
        cfg_rrf_k = self._rrf_k
        cfg_keyword_top_k = self._keyword_top_k
        tool_metadata = {
            "knowledge_tool": True,
            "knowledge_base_id": kb_id,
        }

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
                insight_method_map: dict[int, str | None] = {
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
                doc_results = await knowledge_title_service.search(
                    db,
                    kb_id,
                    query,
                    top_k,
                    hybrid_enabled=cfg_hybrid_enabled,
                    rrf_k=cfg_rrf_k,
                    keyword_top_k=cfg_keyword_top_k,
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

        tools = [
            StructuredTool(
                name=f"{tool_prefix}_search",
                description=_desc("search", node_name),
                func=None,
                coroutine=vector_search,
                args_schema=VectorSearchInput,
                metadata=tool_metadata,
            ),
            StructuredTool(
                name=f"{tool_prefix}_title_search",
                description=_desc("title_search", node_name),
                func=None,
                coroutine=title_search,
                args_schema=TitleSearchInput,
                metadata=tool_metadata,
            ),
            StructuredTool(
                name=f"{tool_prefix}_get_paragraphs",
                description=_desc("get_paragraphs", node_name),
                func=None,
                coroutine=get_paragraphs,
                args_schema=GetParagraphsInput,
                metadata=tool_metadata,
            ),
            StructuredTool(
                name=f"{tool_prefix}_adjacent",
                description=_desc("adjacent", node_name),
                func=None,
                coroutine=adjacent,
                args_schema=AdjacentInput,
                metadata=tool_metadata,
            ),
            StructuredTool(
                name=f"{tool_prefix}_title_lookup",
                description=_desc("title_lookup", node_name),
                func=None,
                coroutine=title_lookup,
                args_schema=TitleLookupInput,
                metadata=tool_metadata,
            ),
            StructuredTool(
                name=f"{tool_prefix}_save_insight",
                description=_desc("save_insight", node_name),
                func=None,
                coroutine=save_insight,
                args_schema=SaveInsightInput,
                metadata=tool_metadata,
            ),
            StructuredTool(
                name=f"{tool_prefix}_list_insight",
                description=_desc("list_insight", node_name),
                func=None,
                coroutine=list_insights,
                args_schema=ListInsightInput,
                metadata=tool_metadata,
            ),
            StructuredTool(
                name=f"{tool_prefix}_delete_insight",
                description=_desc("delete_insight", node_name),
                func=None,
                coroutine=delete_insight,
                args_schema=DeleteInsightInput,
                metadata=tool_metadata,
            ),
        ]

        # ---- 文档编辑工具（按节点配置开关注册） ----
        if self._enable_document_edit:
            tools.extend(
                [
                    StructuredTool(
                        name=f"{tool_prefix}_save_document",
                        description=_desc("save_document", node_name),
                        func=None,
                        coroutine=save_document,
                        args_schema=SaveDocumentInput,
                        metadata=tool_metadata,
                    ),
                    StructuredTool(
                        name=f"{tool_prefix}_update_document",
                        description=_desc("update_document", node_name),
                        func=None,
                        coroutine=update_document,
                        args_schema=UpdateDocumentInput,
                        metadata=tool_metadata,
                    ),
                    StructuredTool(
                        name=f"{tool_prefix}_delete_document",
                        description=_desc("delete_document", node_name),
                        func=None,
                        coroutine=delete_document,
                        args_schema=DeleteDocumentInput,
                        metadata=tool_metadata,
                    ),
                    StructuredTool(
                        name=f"{tool_prefix}_list_document",
                        description=_desc("list_document", node_name),
                        func=None,
                        coroutine=list_documents,
                        args_schema=ListDocumentInput,
                        metadata=tool_metadata,
                    ),
                ]
            )

        return tools

    @classmethod
    def tool_info(cls, node: "FlowNode") -> list[dict]:
        """从描述常量派生（单源：与 build_tools 产出逐字节一致）

        幽灵索引守卫：未配置知识库时无工具（与 get_tool 返回 None 对齐）。
        """
        if not (node.base_config or {}).get("knowledge_base_id"):
            return []
        node_key = node.node_key
        node_name = node.node_name or "知识库"
        tool_prefix = f"knowledge_{node_key}"
        enable_doc = bool((node.base_config or {}).get("enable_document_edit", True))
        short_names = [
            "search",
            "title_search",
            "get_paragraphs",
            "adjacent",
            "title_lookup",
            "save_insight",
            "list_insight",
            "delete_insight",
        ]
        if enable_doc:
            short_names += [
                "save_document",
                "update_document",
                "delete_document",
                "list_document",
            ]
        return [
            {"name": f"{tool_prefix}_{s}", "description": _desc(s, node_name)}
            for s in short_names
        ]

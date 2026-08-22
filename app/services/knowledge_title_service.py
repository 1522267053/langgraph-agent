"""
知识库标题索引服务

三层知识库导航的核心服务：
- 第一层：标题索引（文档列表 + 标题树）
- 第二层：段落内容（通过标题定位段落）
- 第三层：相邻段落（通过段落序号翻页）
- 全局向量搜索：语义搜索段落，返回文件名+标题+段落
"""

from typing import Any, Dict, List

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_document import KnowledgeDocument
from app.models.knowledge_document_segment import KnowledgeDocumentSegment
from app.models.knowledge_document_title import KnowledgeDocumentTitle
from app.schemas.knowledge_schema import (
    TitleTreeItem,
    DocumentListItem,
    ParagraphItem,
    AdjacentParagraphsResult,
    TitleLookupResult,
)


class KnowledgeTitleService:
    """
    知识库标题索引服务

    提供文档列表、标题树、段落导航、相邻段落等查询能力
    """

    async def get_document_list(
        self, db: AsyncSession, knowledge_base_id: int
    ) -> List[DocumentListItem]:
        """
        获取知识库下的文档列表

        Args:
            db: 数据库会话
            knowledge_base_id: 知识库ID

        Returns:
            文档列表 [{id, title, file_type, title_count}, ...]
        """
        from sqlalchemy import func as sa_func

        stmt = (
            select(
                KnowledgeDocument.id,
                KnowledgeDocument.title,
                KnowledgeDocument.file_type,
            )
            .join(
                KnowledgeBase,
                KnowledgeDocument.knowledge_base_id == KnowledgeBase.id,
            )
            .where(
                KnowledgeDocument.knowledge_base_id == knowledge_base_id,
                KnowledgeDocument.is_delete == 0,
                KnowledgeBase.is_delete == 0,
            )
            .order_by(KnowledgeDocument.id)
        )
        result = await db.execute(stmt)
        docs = result.all()

        if not docs:
            return []

        doc_ids = [d.id for d in docs]

        title_count_stmt = (
            select(
                KnowledgeDocumentTitle.document_id,
                sa_func.count(KnowledgeDocumentTitle.id).label("title_count"),
            )
            .where(
                KnowledgeDocumentTitle.document_id.in_(doc_ids),
                KnowledgeDocumentTitle.is_delete == 0,
            )
            .group_by(KnowledgeDocumentTitle.document_id)
        )
        title_count_result = await db.execute(title_count_stmt)
        title_count_map = {
            row.document_id: row.title_count for row in title_count_result.all()
        }

        return [
            DocumentListItem(
                id=d.id,
                title=d.title,
                file_type=d.file_type,
                title_count=title_count_map.get(d.id, 0),
            )
            for d in docs
        ]

    async def get_title_tree(
        self, db: AsyncSession, document_id: int
    ) -> List[TitleTreeItem]:
        """
        获取文档的标题树

        Args:
            db: 数据库会话
            document_id: 文档ID

        Returns:
            标题树列表，按 title_index 排序
        """
        titles = await self._get_titles_by_document(db, document_id)

        if not titles:
            return []

        doc = await db.get(KnowledgeDocument, document_id)
        if not doc:
            return []

        title_items = []
        for t in titles:
            paragraph_count = t.end_segment_index - t.start_segment_index + 1
            title_items.append(
                TitleTreeItem(
                    id=t.id,
                    level=t.level,
                    title=t.title,
                    title_index=t.title_index,
                    paragraph_count=paragraph_count,
                )
            )

        return title_items

    async def get_paragraphs_by_title(
        self, db: AsyncSession, title_id: int
    ) -> List[ParagraphItem]:
        """
        获取标题下的所有段落

        Args:
            db: 数据库会话
            title_id: 标题ID

        Returns:
            段落列表
        """
        title = await db.get(KnowledgeDocumentTitle, title_id)
        if not title or title.is_delete == 1:
            return []

        stmt = (
            select(KnowledgeDocumentSegment)
            .where(
                KnowledgeDocumentSegment.document_id == title.document_id,
                KnowledgeDocumentSegment.segment_index >= title.start_segment_index,
                KnowledgeDocumentSegment.segment_index <= title.end_segment_index,
                KnowledgeDocumentSegment.is_delete == 0,
            )
            .order_by(KnowledgeDocumentSegment.segment_index)
        )
        result = await db.execute(stmt)
        segments = result.scalars().all()

        return [
            ParagraphItem(
                id=s.id,
                segment_index=s.segment_index,
                content=s.content,
                word_count=s.word_count,
            )
            for s in segments
        ]

    async def get_adjacent_segments(
        self, db: AsyncSession, segment_id: int, direction: str = "both"
    ) -> AdjacentParagraphsResult:
        """
        获取相邻段落

        Args:
            db: 数据库会话
            segment_id: 段落ID
            direction: 方向 "prev" / "next" / "both"

        Returns:
            相邻段落结果
        """
        segment = await db.get(KnowledgeDocumentSegment, segment_id)
        if not segment or segment.is_delete == 1:
            return AdjacentParagraphsResult()

        current = ParagraphItem(
            id=segment.id,
            segment_index=segment.segment_index,
            content=segment.content,
            word_count=segment.word_count,
        )

        prev_item = None
        next_item = None

        if direction in ("prev", "both"):
            stmt = (
                select(KnowledgeDocumentSegment)
                .where(
                    KnowledgeDocumentSegment.document_id == segment.document_id,
                    KnowledgeDocumentSegment.segment_index == segment.segment_index - 1,
                    KnowledgeDocumentSegment.is_delete == 0,
                )
                .limit(1)
            )
            result = await db.execute(stmt)
            prev_seg = result.scalar_one_or_none()
            if prev_seg:
                prev_item = ParagraphItem(
                    id=prev_seg.id,
                    segment_index=prev_seg.segment_index,
                    content=prev_seg.content,
                    word_count=prev_seg.word_count,
                )

        if direction in ("next", "both"):
            stmt = (
                select(KnowledgeDocumentSegment)
                .where(
                    KnowledgeDocumentSegment.document_id == segment.document_id,
                    KnowledgeDocumentSegment.segment_index == segment.segment_index + 1,
                    KnowledgeDocumentSegment.is_delete == 0,
                )
                .limit(1)
            )
            result = await db.execute(stmt)
            next_seg = result.scalar_one_or_none()
            if next_seg:
                next_item = ParagraphItem(
                    id=next_seg.id,
                    segment_index=next_seg.segment_index,
                    content=next_seg.content,
                    word_count=next_seg.word_count,
                )

        return AdjacentParagraphsResult(
            prev=prev_item,
            current=current,
            next=next_item,
        )

    async def get_title_for_segment(
        self, db: AsyncSession, segment_id: int
    ) -> TitleLookupResult:
        """
        段落反向查找标题及上下文

        返回段落所属的标题信息，以及该文档的完整标题树

        Args:
            db: 数据库会话
            segment_id: 段落ID

        Returns:
            标题查找结果
        """
        segment = await db.get(KnowledgeDocumentSegment, segment_id)
        if not segment or segment.is_delete == 1:
            return TitleLookupResult()

        title_tree = await self.get_title_tree(db, segment.document_id)

        if not segment.title_id:
            return TitleLookupResult(title_tree=title_tree)

        title = await db.get(KnowledgeDocumentTitle, segment.title_id)
        if not title or title.is_delete == 1:
            return TitleLookupResult(title_tree=title_tree)

        current_title = TitleTreeItem(
            id=title.id,
            level=title.level,
            title=title.title,
            title_index=title.title_index,
            paragraph_count=title.end_segment_index - title.start_segment_index + 1,
        )

        return TitleLookupResult(
            current_title=current_title,
            title_tree=title_tree,
        )

    async def build_title_tree_text(self, db: AsyncSession, document_id: int) -> str:
        """
        格式化标题树为缩进文本

        Args:
            db: 数据库会话
            document_id: 文档ID

        Returns:
            格式化的标题树文本
        """
        titles = await self.get_title_tree(db, document_id)
        if not titles:
            return "该文档无标题索引"

        lines = []
        for t in titles:
            indent = "  " * (t.level - 1)
            line = f"{indent}- [{t.id}] {t.title}（{t.paragraph_count}段）"
            lines.append(line)

        return "\n".join(lines)

    async def delete_titles_by_document_id(
        self, db: AsyncSession, document_id: int
    ) -> None:
        """
        软删除文档的所有标题索引

        Args:
            db: 数据库会话
            document_id: 文档ID
        """
        await db.execute(
            update(KnowledgeDocumentTitle)
            .where(KnowledgeDocumentTitle.document_id == document_id)
            .values(is_delete=1)
        )

    async def vector_search(
        self, db: AsyncSession, knowledge_base_id: int, query: str, top_k: int = 5
    ) -> List[Dict]:
        """
        全局搜索知识库分段，优先向量搜索，失败时回退到 LIKE 模糊搜索

        Args:
            db: 数据库会话
            knowledge_base_id: 知识库ID
            query: 搜索文本
            top_k: 返回结果数量

        Returns:
            包含知识库、文档、标题、分片、分数和检索方式的结果列表
        """
        from app.config.settings import settings

        min_score = settings.knowledge_search_min_score

        # ---- 向量搜索 ----
        from app.services.embedding_service import get_embedding_service_async
        from app.services.vector_store_service import get_vector_store_service

        try:
            embedding_service = await get_embedding_service_async()
            vector_store = get_vector_store_service()

            query_embedding = await embedding_service.embed_query(query)

            if query_embedding:
                vector_results = await vector_store.similarity_search(
                    query_embedding=query_embedding,
                    k=top_k,
                    filter={"knowledge_base_id": knowledge_base_id},
                )
            else:
                vector_results = []

            # 相关度阈值过滤，过滤后为空回退 LIKE 模糊搜索
            vector_results = [
                result
                for result in vector_results
                if (score := self._score_from_distance(result.get("distance")))
                is not None
                and score >= min_score
            ]

            if vector_results:
                enriched_results = await self._enrich_vector_results(
                    db, knowledge_base_id, vector_results
                )
                if enriched_results:
                    return enriched_results
        except Exception:
            pass

        # ---- 回退到 LIKE 模糊搜索 ----
        return await self._like_search(db, knowledge_base_id, query, top_k)

    async def _enrich_vector_results(
        self,
        db: AsyncSession,
        knowledge_base_id: int,
        vector_results: List[Dict],
    ) -> List[Dict]:
        """使用活动数据库记录补全向量结果。"""
        segment_ids = []
        for item in vector_results:
            metadata = item.get("metadata") or {}
            try:
                segment_id = int(metadata.get("segment_id"))
            except (TypeError, ValueError):
                continue
            segment_ids.append(segment_id)

        segment_data = await self._batch_active_segment_data(
            db, knowledge_base_id, segment_ids
        )

        results = []
        for item in vector_results:
            metadata = item.get("metadata") or {}
            try:
                segment_id = int(metadata.get("segment_id"))
            except (TypeError, ValueError):
                continue

            data = segment_data.get(segment_id)
            if data is None:
                continue

            metadata_document_id = metadata.get("document_id")
            if metadata_document_id is not None:
                try:
                    if int(metadata_document_id) != data["document_id"]:
                        continue
                except (TypeError, ValueError):
                    continue

            metadata_knowledge_base_id = metadata.get("knowledge_base_id")
            if metadata_knowledge_base_id is not None:
                try:
                    if int(metadata_knowledge_base_id) != knowledge_base_id:
                        continue
                except (TypeError, ValueError):
                    continue

            score = self._score_from_distance(item.get("distance"))
            enriched = dict(data)
            enriched["score"] = round(score, 4) if score is not None else None
            enriched["retrieval_method"] = "vector"
            results.append(enriched)

        return results

    async def _like_search(
        self, db: AsyncSession, knowledge_base_id: int, query: str, top_k: int = 5
    ) -> List[Dict]:
        """LIKE 模糊搜索知识库分段（向量搜索不可用时的回退方案）"""
        stmt = (
            select(KnowledgeDocumentSegment.id)
            .join(
                KnowledgeDocument,
                KnowledgeDocumentSegment.document_id == KnowledgeDocument.id,
            )
            .join(
                KnowledgeBase,
                KnowledgeDocument.knowledge_base_id == KnowledgeBase.id,
            )
            .where(
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.is_delete == 0,
                KnowledgeDocument.is_delete == 0,
                KnowledgeDocumentSegment.is_delete == 0,
                KnowledgeDocumentSegment.content.like(f"%{query}%"),
            )
            .limit(top_k)
        )
        result = await db.execute(stmt)
        segment_ids = list(result.scalars().all())
        if not segment_ids:
            return []

        segment_data = await self._batch_active_segment_data(
            db, knowledge_base_id, segment_ids
        )
        results = []
        for segment_id in segment_ids:
            data = segment_data.get(segment_id)
            if data is None:
                continue
            enriched = dict(data)
            enriched["score"] = None
            enriched["retrieval_method"] = "like"
            results.append(enriched)
        return results

    async def resolve_segment_references(
        self,
        db: AsyncSession,
        knowledge_base_id: int,
        segment_ids: List[int],
        *,
        score_by_segment_id: Dict[int, float | None] | None = None,
        retrieval_method_by_segment_id: Dict[int, str | None] | None = None,
    ) -> List[Dict]:
        """批量解析当前知识库中的活动分片引用。"""
        ordered_ids = list(dict.fromkeys(segment_ids))
        segment_data = await self._batch_active_segment_data(
            db, knowledge_base_id, ordered_ids
        )

        references = []
        for segment_id in ordered_ids:
            data = segment_data.get(segment_id)
            if data is None:
                continue

            excerpt = data["content"].strip()
            if len(excerpt) > 300:
                excerpt = f"{excerpt[:300].rstrip()}..."

            references.append(
                {
                    "reference_id": f"segment:{segment_id}",
                    "citation_marker": f"[段落ID:{segment_id}]",
                    "knowledge_base_id": data["knowledge_base_id"],
                    "document_id": data["document_id"],
                    "document_title": data["document_title"],
                    "file_type": data["file_type"],
                    "title_id": data["title_id"],
                    "title_text": data["title_text"],
                    "segment_id": data["segment_id"],
                    "segment_index": data["segment_index"],
                    "excerpt": excerpt,
                    "score": (
                        score_by_segment_id.get(segment_id)
                        if score_by_segment_id is not None
                        else None
                    ),
                    "retrieval_method": (
                        retrieval_method_by_segment_id.get(segment_id)
                        if retrieval_method_by_segment_id is not None
                        else None
                    ),
                }
            )
        return references

    async def _batch_active_segment_data(
        self,
        db: AsyncSession,
        knowledge_base_id: int,
        segment_ids: List[int],
    ) -> Dict[int, Dict[str, Any]]:
        """批量查询当前知识库中的活动文档和分片。"""
        if not segment_ids:
            return {}

        stmt = (
            select(
                KnowledgeDocumentSegment.id.label("segment_id"),
                KnowledgeDocumentSegment.document_id,
                KnowledgeDocumentSegment.segment_index,
                KnowledgeDocumentSegment.title_id,
                KnowledgeDocumentSegment.content,
                KnowledgeDocument.knowledge_base_id,
                KnowledgeDocument.title.label("document_title"),
                KnowledgeDocument.file_type,
            )
            .select_from(KnowledgeDocumentSegment)
            .join(
                KnowledgeDocument,
                KnowledgeDocumentSegment.document_id == KnowledgeDocument.id,
            )
            .join(
                KnowledgeBase,
                KnowledgeDocument.knowledge_base_id == KnowledgeBase.id,
            )
            .where(
                KnowledgeDocumentSegment.id.in_(segment_ids),
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.is_delete == 0,
                KnowledgeDocument.is_delete == 0,
                KnowledgeDocumentSegment.is_delete == 0,
            )
        )
        result = await db.execute(stmt)
        rows = result.all()
        if not rows:
            return {}

        title_ids = {row.title_id for row in rows if row.title_id is not None}
        title_map = {}
        if title_ids:
            title_stmt = select(
                KnowledgeDocumentTitle.id,
                KnowledgeDocumentTitle.document_id,
                KnowledgeDocumentTitle.title,
            ).where(
                KnowledgeDocumentTitle.id.in_(title_ids),
                KnowledgeDocumentTitle.is_delete == 0,
            )
            title_result = await db.execute(title_stmt)
            title_map = {
                (row.id, row.document_id): row.title for row in title_result.all()
            }

        segment_data = {}
        for row in rows:
            title_text = title_map.get((row.title_id, row.document_id))
            segment_data[row.segment_id] = {
                "knowledge_base_id": row.knowledge_base_id,
                "document_id": row.document_id,
                "document_title": row.document_title,
                "file_type": row.file_type,
                "title_id": row.title_id if title_text is not None else None,
                "title_text": title_text or "",
                "segment_id": row.segment_id,
                "segment_index": row.segment_index,
                "content": row.content,
            }
        return segment_data

    @staticmethod
    def _score_from_distance(distance: Any) -> float | None:
        """将向量距离转换为相关度分数。"""
        if distance is None:
            return None
        try:
            return 1 - float(distance)
        except (TypeError, ValueError):
            return None

    async def _get_titles_by_document(
        self, db: AsyncSession, document_id: int
    ) -> List[KnowledgeDocumentTitle]:
        """
        获取文档的所有标题索引记录

        Args:
            db: 数据库会话
            document_id: 文档ID

        Returns:
            标题索引列表
        """
        stmt = (
            select(KnowledgeDocumentTitle)
            .where(
                KnowledgeDocumentTitle.document_id == document_id,
                KnowledgeDocumentTitle.is_delete == 0,
            )
            .order_by(KnowledgeDocumentTitle.title_index)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())


knowledge_title_service = KnowledgeTitleService()

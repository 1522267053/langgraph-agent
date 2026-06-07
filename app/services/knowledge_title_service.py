"""
知识库标题索引服务

三层知识库导航的核心服务：
- 第一层：标题索引（文档列表 + 标题树）
- 第二层：段落内容（通过标题定位段落）
- 第三层：相邻段落（通过段落序号翻页）
- 全局向量搜索：语义搜索段落，返回文件名+标题+段落
"""

from typing import List, Dict

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

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
            .where(
                KnowledgeDocument.knowledge_base_id == knowledge_base_id,
                KnowledgeDocument.is_delete == 0,
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
            搜索结果列表 [{document_id, document_title, title_id, title_text, segment_id, content, score}, ...]
        """
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

            if vector_results:
                return await self._enrich_vector_results(db, vector_results)
        except Exception:
            pass

        # ---- 回退到 LIKE 模糊搜索 ----
        return await self._like_search(db, knowledge_base_id, query, top_k)

    async def _enrich_vector_results(
        self, db: AsyncSession, vector_results: List[Dict]
    ) -> List[Dict]:
        """将向量搜索结果与数据库信息合并"""
        doc_ids = set()
        title_ids = set()
        segment_ids = set()

        for item in vector_results:
            metadata = item.get("metadata", {})
            doc_id = metadata.get("document_id")
            title_id = metadata.get("title_id")
            segment_id = metadata.get("segment_id")
            if doc_id:
                doc_ids.add(doc_id)
            if title_id:
                title_ids.add(title_id)
            if segment_id:
                segment_ids.add(segment_id)

        doc_name_map = await self._batch_doc_names(db, doc_ids)
        title_name_map = await self._batch_title_names(db, title_ids)
        segment_content_map = await self._batch_segment_contents(db, segment_ids)

        results = []
        for item in vector_results:
            metadata = item.get("metadata", {})
            doc_id = metadata.get("document_id")
            title_id = metadata.get("title_id")
            segment_id = metadata.get("segment_id")
            distance = item.get("distance", 0)

            content = segment_content_map.get(segment_id) or item.get("text", "")

            results.append(
                {
                    "document_id": doc_id,
                    "document_title": doc_name_map.get(doc_id, ""),
                    "title_id": title_id,
                    "title_text": title_name_map.get(title_id, ""),
                    "segment_id": segment_id,
                    "content": content,
                    "score": round(1 - distance, 4) if distance else 0,
                }
            )

        return results

    async def _like_search(
        self, db: AsyncSession, knowledge_base_id: int, query: str, top_k: int = 5
    ) -> List[Dict]:
        """LIKE 模糊搜索知识库分段（向量搜索不可用时的回退方案）"""
        stmt = (
            select(
                KnowledgeDocumentSegment.id,
                KnowledgeDocumentSegment.document_id,
                KnowledgeDocumentSegment.title_id,
                KnowledgeDocumentSegment.content,
            )
            .join(
                KnowledgeDocument,
                KnowledgeDocumentSegment.document_id == KnowledgeDocument.id,
            )
            .where(
                KnowledgeDocument.knowledge_base_id == knowledge_base_id,
                KnowledgeDocument.is_delete == 0,
                KnowledgeDocumentSegment.is_delete == 0,
                KnowledgeDocumentSegment.content.like(f"%{query}%"),
            )
            .limit(top_k)
        )
        result = await db.execute(stmt)
        rows = result.all()

        if not rows:
            return []

        doc_ids = {r.document_id for r in rows}
        title_ids = {r.title_id for r in rows if r.title_id}

        doc_name_map = await self._batch_doc_names(db, doc_ids)
        title_name_map = await self._batch_title_names(db, title_ids)

        return [
            {
                "document_id": r.document_id,
                "document_title": doc_name_map.get(r.document_id, ""),
                "title_id": r.title_id,
                "title_text": title_name_map.get(r.title_id, "") if r.title_id else "",
                "segment_id": r.id,
                "content": r.content,
                "score": 0,
            }
            for r in rows
        ]

    async def _batch_doc_names(self, db: AsyncSession, doc_ids: set) -> Dict[int, str]:
        """批量查询文档名称"""
        if not doc_ids:
            return {}
        stmt = select(KnowledgeDocument.id, KnowledgeDocument.title).where(
            KnowledgeDocument.id.in_(doc_ids),
            KnowledgeDocument.is_delete == 0,
        )
        result = await db.execute(stmt)
        return {row.id: row.title for row in result.all()}

    async def _batch_title_names(
        self, db: AsyncSession, title_ids: set
    ) -> Dict[int, str]:
        """批量查询标题名称"""
        if not title_ids:
            return {}
        stmt = select(KnowledgeDocumentTitle.id, KnowledgeDocumentTitle.title).where(
            KnowledgeDocumentTitle.id.in_(title_ids),
            KnowledgeDocumentTitle.is_delete == 0,
        )
        result = await db.execute(stmt)
        return {row.id: row.title for row in result.all()}

    async def _batch_segment_contents(
        self, db: AsyncSession, segment_ids: set
    ) -> Dict[int, str]:
        """批量查询段落内容"""
        if not segment_ids:
            return {}
        stmt = select(
            KnowledgeDocumentSegment.id, KnowledgeDocumentSegment.content
        ).where(
            KnowledgeDocumentSegment.id.in_(segment_ids),
            KnowledgeDocumentSegment.is_delete == 0,
        )
        result = await db.execute(stmt)
        return {row.id: row.content for row in result.all()}

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

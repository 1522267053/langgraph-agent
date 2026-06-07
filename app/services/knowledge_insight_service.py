"""
知识沉淀服务

知识库的 AI 知识沉淀层，LLM 在对话中主动保存有价值的知识总结。
搜索时优先查询沉淀层，未命中再检索原始文档。
"""

import logging
import threading
from typing import Optional, List
from sqlalchemy import select, and_, or_, Select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_insight import KnowledgeInsight, KnowledgeInsightSegment
from app.models.base_model import DbBaseModel
from app.schemas.knowledge_insight_schema import (
    KnowledgeInsightCreate,
    KnowledgeInsightUpdate,
)
from app.services.base_service import BaseService
from app.services.embedding_service import get_embedding_service_async
from app.services.vector_store_service import ChromaVectorStoreService

logger = logging.getLogger(__name__)

INSIGHT_COLLECTION_NAME = "knowledge_insights"
_vector_store: Optional[ChromaVectorStoreService] = None
_vector_store_lock = threading.Lock()


def _get_vector_store() -> ChromaVectorStoreService:
    """获取知识沉淀向量存储单例"""
    global _vector_store
    if _vector_store is None:
        with _vector_store_lock:
            if _vector_store is None:
                _vector_store = ChromaVectorStoreService(
                    collection_name=INSIGHT_COLLECTION_NAME
                )
    return _vector_store


class KnowledgeInsightService(
    BaseService[KnowledgeInsight, KnowledgeInsightCreate, KnowledgeInsightUpdate]
):
    def __init__(self):
        super().__init__(KnowledgeInsight)

    def _apply_filters(
        self,
        query: Optional[Select],
        count_query: Optional[Select],
        condition: Optional[DbBaseModel],
    ) -> tuple[Optional[Select], Optional[Select]]:
        query, count_query = super()._apply_filters(query, count_query, condition)

        if not condition:
            return query, count_query

        if hasattr(condition, "knowledge_base_id") and condition.knowledge_base_id:
            query = query.where(
                KnowledgeInsight.knowledge_base_id == condition.knowledge_base_id
            )
            count_query = count_query.where(
                KnowledgeInsight.knowledge_base_id == condition.knowledge_base_id
            )

        if hasattr(condition, "question") and condition.question:
            query, count_query = self._apply_like_filter(
                query, count_query, "question", condition.question
            )

        if hasattr(condition, "keywords") and condition.keywords:
            query, count_query = self._apply_like_filter(
                query, count_query, "keywords", condition.keywords
            )

        return query, count_query

    # ---- 沉淀保存 ----

    async def save_insight(
        self,
        db: AsyncSession,
        knowledge_base_id: int,
        question: str,
        answer: str,
        keywords: Optional[str] = None,
        source_segment_ids: Optional[List[int]] = None,
    ) -> KnowledgeInsight:
        """保存知识沉淀并自动向量化"""
        insight = KnowledgeInsight(
            knowledge_base_id=knowledge_base_id,
            question=question,
            answer=answer,
            keywords=keywords,
        )

        self._set_creator_fields(insight)
        insight.is_delete = 0

        db.add(insight)
        await db.commit()
        await db.refresh(insight)

        # 保存关联段落
        if source_segment_ids:
            await self._save_segment_relations(db, insight.id, source_segment_ids)

        # 向量化
        await self._vectorize(insight)

        logger.info(
            f"知识沉淀已保存: insight_id={insight.id}, "
            f"kb_id={knowledge_base_id}, question={question[:50]}"
        )

        return insight

    async def _save_segment_relations(
        self,
        db: AsyncSession,
        insight_id: int,
        segment_ids: List[int],
    ) -> None:
        """批量保存沉淀与段落的关联关系"""
        for segment_id in segment_ids:
            relation = KnowledgeInsightSegment(
                insight_id=insight_id,
                segment_id=segment_id,
            )
            self._set_creator_fields(relation)
            relation.is_delete = 0
            db.add(relation)

        await db.commit()

    # ---- 向量化 ----

    async def _vectorize(self, insight: KnowledgeInsight) -> None:
        """将沉淀内容向量化并存入 ChromaDB"""
        try:
            embedding_service = await get_embedding_service_async()
            vector_store = _get_vector_store()

            text = f"{insight.question}。{insight.answer}"
            embedding = await embedding_service.embed_query(text)

            vector_id = f"insight_{insight.id}"
            insight.vector_id = vector_id

            from app.config.database import AsyncSessionLocal

            async with AsyncSessionLocal() as vdb:
                vdb.add(insight)
                await vdb.commit()

            await vector_store.add_texts(
                texts=[text],
                embeddings=[embedding],
                metadatas=[
                    {
                        "knowledge_base_id": insight.knowledge_base_id,
                        "insight_id": insight.id,
                    }
                ],
                ids=[vector_id],
            )
        except Exception as e:
            logger.warning(f"知识沉淀向量化失败: insight_id={insight.id}, error={e}")

    # ---- 向量搜索 ----

    async def search(
        self,
        db: AsyncSession,
        knowledge_base_id: int,
        query: str,
        top_k: int = 5,
    ) -> List[dict]:
        """搜索知识沉淀，优先向量搜索，失败时回退到 LIKE 模糊搜索

        Returns:
            [{id, question, answer, score, source_segment_ids}, ...]
        """
        # ---- 向量搜索 ----
        try:
            embedding_service = await get_embedding_service_async()
            vector_store = _get_vector_store()

            query_embedding = await embedding_service.embed_query(query)

            if query_embedding:
                vector_results = await vector_store.similarity_search(
                    query_embedding=query_embedding,
                    k=top_k,
                    filter={"knowledge_base_id": knowledge_base_id},
                )

                if vector_results:
                    return await self._enrich_vector_results(db, vector_results, top_k)
        except Exception as e:
            logger.warning(f"知识沉淀向量搜索失败: {e}")

        # ---- 回退到 LIKE 模糊搜索 ----
        return await self._like_search(db, knowledge_base_id, query, top_k)

    async def _enrich_vector_results(
        self, db: AsyncSession, vector_results: List[dict], top_k: int
    ) -> List[dict]:
        """将向量搜索结果与沉淀记录合并"""
        insight_ids = [
            r.get("metadata", {}).get("insight_id")
            for r in vector_results
            if r.get("metadata", {}).get("insight_id")
        ]

        if not insight_ids:
            return []

        stmt = select(KnowledgeInsight).where(KnowledgeInsight.id.in_(insight_ids))
        db_result = await db.execute(stmt, execution_options={"include_deleted": False})
        id_to_insight = {i.id: i for i in db_result.scalars().all()}

        insight_segments = await self._batch_segment_ids(db, insight_ids)

        scored = []
        for r in vector_results[:top_k]:
            distance = r.get("distance", 1.0)
            score = round(1 - distance, 4)
            insight_id = r.get("metadata", {}).get("insight_id")
            insight = id_to_insight.get(insight_id)
            if not insight:
                continue

            scored.append(
                {
                    "id": insight.id,
                    "question": insight.question,
                    "answer": insight.answer,
                    "score": score,
                    "source_segment_ids": insight_segments.get(insight.id, []),
                }
            )

        return scored

    async def _like_search(
        self,
        db: AsyncSession,
        knowledge_base_id: int,
        query: str,
        top_k: int = 5,
    ) -> List[dict]:
        """LIKE 模糊搜索知识沉淀（向量搜索不可用时的回退方案）"""
        stmt = (
            select(KnowledgeInsight)
            .where(
                KnowledgeInsight.knowledge_base_id == knowledge_base_id,
                KnowledgeInsight.is_delete == 0,
                or_(
                    KnowledgeInsight.question.like(f"%{query}%"),
                    KnowledgeInsight.answer.like(f"%{query}%"),
                ),
            )
            .limit(top_k)
        )
        result = await db.execute(stmt)
        insights = result.scalars().all()

        if not insights:
            return []

        insight_ids = [i.id for i in insights]
        insight_segments = await self._batch_segment_ids(db, insight_ids)

        return [
            {
                "id": insight.id,
                "question": insight.question,
                "answer": insight.answer,
                "score": 0,
                "source_segment_ids": insight_segments.get(insight.id, []),
            }
            for insight in insights
        ]

    async def _batch_segment_ids(
        self, db: AsyncSession, insight_ids: list[int]
    ) -> dict[int, list[int]]:
        """批量查询沉淀关联的段落ID"""
        if not insight_ids:
            return {}
        seg_stmt = select(
            KnowledgeInsightSegment.insight_id,
            KnowledgeInsightSegment.segment_id,
        ).where(
            and_(
                KnowledgeInsightSegment.insight_id.in_(insight_ids),
                KnowledgeInsightSegment.is_delete == 0,
            )
        )
        seg_result = await db.execute(seg_stmt)
        result: dict[int, list[int]] = {}
        for row in seg_result.all():
            result.setdefault(row.insight_id, []).append(row.segment_id)
        return result

    # ---- 关联段落查询 ----

    async def get_source_segment_ids(
        self, db: AsyncSession, insight_id: int
    ) -> List[int]:
        """查询沉淀关联的段落ID列表"""
        stmt = select(KnowledgeInsightSegment.segment_id).where(
            and_(
                KnowledgeInsightSegment.insight_id == insight_id,
                KnowledgeInsightSegment.is_delete == 0,
            )
        )
        result = await db.execute(stmt)
        return [row[0] for row in result.all()]

    # ---- 删除（重写：级联处理） ----

    async def delete(self, db: AsyncSession, id: int) -> None:
        """删除知识沉淀，同时处理关联段落和向量"""
        db_obj = await self.get_by_id(db, id)
        if not db_obj:
            return

        # 软删除关联段落
        stmt = (
            update(KnowledgeInsightSegment)
            .where(
                and_(
                    KnowledgeInsightSegment.insight_id == id,
                    KnowledgeInsightSegment.is_delete == 0,
                )
            )
            .values(is_delete=1)
        )
        await db.execute(stmt)

        # 删除 ChromaDB 向量
        if db_obj.vector_id:
            try:
                vector_store = _get_vector_store()
                await vector_store.delete(ids=[db_obj.vector_id])
            except Exception as e:
                logger.warning(f"删除知识沉淀向量失败: insight_id={id}, error={e}")

        # 软删除沉淀记录
        db_obj.is_delete = 1
        self._set_modifier_fields(db_obj)
        await db.commit()

        logger.info(f"知识沉淀已删除: insight_id={id}")

    async def delete_batch_by_ids(self, db: AsyncSession, ids: List[int]) -> dict:
        """批量删除知识沉淀

        Returns:
            {"total": 请求删除数, "deleted": 实际删除数}
        """
        deleted = 0
        for insight_id in ids:
            try:
                await self.delete(db, insight_id)
                deleted += 1
            except Exception as e:
                logger.warning(
                    f"批量删除知识沉淀失败: insight_id={insight_id}, error={e}"
                )

        return {"total": len(ids), "deleted": deleted}


knowledge_insight_service = KnowledgeInsightService()

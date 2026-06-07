"""
知识库文档服务
"""

import logging
from typing import List, Dict, Any
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.models.knowledge_document import (
    KnowledgeDocument,
    ProcessingStatus,
)
from app.models.knowledge_document_segment import KnowledgeDocumentSegment
from app.models.knowledge_document_title import KnowledgeDocumentTitle
from app.schemas.knowledge_schema import (
    KnowledgeDocumentCreate,
    KnowledgeDocumentUpdate,
)
from app.services.base_service import BaseService
from app.utils.document_processor import document_processor
from app.services.knowledge_title_service import knowledge_title_service

logger = logging.getLogger(__name__)


class KnowledgeDocumentService(
    BaseService[KnowledgeDocument, KnowledgeDocumentCreate, KnowledgeDocumentUpdate]
):
    """
    知识库文档服务类
    """

    def __init__(self):
        super().__init__(KnowledgeDocument)

    def _apply_filters(self, query, count_query, condition):
        """
        应用查询条件
        支持标题模糊搜索
        """
        query, count_query = super()._apply_filters(query, count_query, condition)

        if condition and hasattr(condition, "title") and condition.title:
            keyword = f"%{condition.title}%"
            if query is not None:
                query = query.where(KnowledgeDocument.title.like(keyword))
            if count_query is not None:
                count_query = count_query.where(KnowledgeDocument.title.like(keyword))

        return query, count_query

    async def create(self, db, obj_in):
        """
        创建文档，自动计算字数
        """
        if obj_in.content:
            obj_in.word_count = len(obj_in.content)
        return await super().create(db, obj_in)

    async def update(self, db, obj_in):
        """
        更新文档，自动计算字数
        """
        if hasattr(obj_in, "content") and obj_in.content is not None:
            if hasattr(obj_in, "model_dump"):
                data = obj_in.model_dump()
                if "content" in data:
                    obj_in.word_count = len(data["content"])
        return await super().update(db, obj_in)

    # ---- 上传（异步） ----

    async def upload_file(
        self, db: AsyncSession, file: UploadFile, knowledge_base_id: int
    ) -> KnowledgeDocument:
        """
        上传文档（仅保存文件，不解析，立即返回）

        定时任务会异步完成解析、分段、向量化。

        Args:
            db: 数据库会话
            file: 上传的文件
            knowledge_base_id: 知识库ID

        Returns:
            创建的文档记录
        """
        file_path = await document_processor.save_file(file, knowledge_base_id)

        filename = file.filename or "未命名文档"
        ext = document_processor._get_file_extension(filename)

        document = KnowledgeDocument(
            knowledge_base_id=knowledge_base_id,
            title=filename,
            file_type=ext,
            file_path=file_path,
            processing_status=ProcessingStatus.PENDING.value,
        )
        db.add(document)
        await db.commit()
        await db.refresh(document)

        return document

    # ---- 文档处理（定时任务调用） ----

    async def _parse_and_segment(
        self, db: AsyncSession, document: KnowledgeDocument
    ) -> None:
        """解析文件内容并分段存储"""
        text_content = document_processor.extract_text_from_path(
            document.file_path, document.file_type
        )

        if document.file_type == "xlsx":
            result = document_processor.segment_xlsx_by_row(text_content)
        else:
            result = document_processor.smart_segment(text_content)
        titles_data = result["titles"]
        segments_data = result["segments"]

        document.content = text_content
        document.word_count = len(text_content)
        document.segment_count = len(segments_data)

        title_id_map: Dict[int, int] = {}
        for title_data in titles_data:
            title_record = KnowledgeDocumentTitle(
                document_id=document.id,
                title_index=title_data["title_index"],
                level=title_data["level"],
                title=title_data["title"],
                start_segment_index=title_data["start_segment_index"],
                end_segment_index=title_data["end_segment_index"],
            )
            db.add(title_record)
            await db.flush()
            title_id_map[title_data["title_index"]] = title_record.id

        for seg_data in segments_data:
            title_index = seg_data.get("title_index", -1)
            title_id = title_id_map.get(title_index) if title_index >= 0 else None
            segment = KnowledgeDocumentSegment(
                document_id=document.id,
                segment_index=seg_data["segment_index"],
                title=seg_data.get("title", ""),
                title_id=title_id,
                content=seg_data["content"],
                word_count=seg_data["word_count"],
            )
            db.add(segment)

        await db.commit()

    async def get_pending_documents(
        self, db: AsyncSession, limit: int = 5
    ) -> List[KnowledgeDocument]:
        """
        获取待处理的文档列表（含待处理 0 和待向量化 4）
        """
        stmt = (
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.processing_status.in_(
                    [ProcessingStatus.PENDING.value, ProcessingStatus.VECTORIZING.value]
                ),
                KnowledgeDocument.is_delete == 0,
            )
            .order_by(KnowledgeDocument.id)
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def process_document(
        self, db: AsyncSession, document_id: int
    ) -> Dict[str, Any]:
        """
        处理单个文档：
        - status=0(待处理) → 解析文件 → 分段 → 存 DB → 向量化
        - status=4(待向量化) → 仅向量化（文档已解析分段）

        由定时任务调用，内部管理 processing_status 状态流转。
        """
        from app.services.embedding_service import get_embedding_service_async

        document = await self.get_by_id(db, document_id, raise_not_found=False)
        if not document:
            logger.warning(f"文档不存在: {document_id}")
            return {"document_id": document_id, "status": "not_found"}

        is_retry = document.processing_status == ProcessingStatus.VECTORIZING.value
        emb_svc = await get_embedding_service_async()

        # ---- 向量不可用且是首次处理 → 解析分段后设为待向量化 ----
        if not is_retry and not emb_svc.is_available():
            document.processing_status = ProcessingStatus.PROCESSING.value
            await db.commit()
            try:
                await self._parse_and_segment(db, document)
                document.processing_status = ProcessingStatus.VECTORIZING.value
                await db.commit()
                logger.info(
                    f"文档 {document_id} 已解析分段（待向量化，向量模型未配置）"
                )
                return {
                    "document_id": document_id,
                    "status": "pending_vectorization",
                    "segment_count": document.segment_count,
                }
            except Exception as e:
                document.processing_status = ProcessingStatus.FAILED.value
                document.error_message = str(e)
                await db.commit()
                raise

        # ---- 标准处理流程 ----
        document.processing_status = ProcessingStatus.PROCESSING.value
        document.error_message = None
        await db.commit()

        try:
            if not is_retry:
                if not document.file_path:
                    raise ValueError("文件路径为空，无法处理")
                await self._parse_and_segment(db, document)

            # 向量化
            try:
                document.processing_status = ProcessingStatus.VECTORIZING.value
                await db.commit()
                vectorize_result = await self.vectorize_document(
                    db, document.id, force=False
                )
            except Exception as e:
                logger.error(f"文档 {document_id} 向量化失败: {e}")
                vectorize_result = {
                    "vectorized_segments": 0,
                    "failed_segments": 0,
                }
                if document.segment_count and document.segment_count > 0:
                    document.processing_status = ProcessingStatus.VECTORIZING.value
                    document.error_message = f"向量化失败: {e}"
                    await db.commit()
                    return {
                        "document_id": document_id,
                        "status": "vectorization_failed",
                        "error": str(e),
                    }

            # 标记为已完成
            document.processing_status = ProcessingStatus.COMPLETED.value
            await db.commit()

            return {
                "document_id": document_id,
                "status": "success",
                "word_count": document.word_count or 0,
                "segment_count": document.segment_count or 0,
                "vectorized_segments": vectorize_result.get("vectorized_segments", 0),
            }

        except Exception as e:
            logger.error(f"文档 {document_id} 处理失败: {e}", exc_info=True)
            await db.rollback()
            # rollback 后重新查询，再更新状态
            document = await self.get_by_id(db, document_id, raise_not_found=False)
            if document:
                document.processing_status = ProcessingStatus.FAILED.value
                document.error_message = str(e)[:1000]
                await db.commit()

            return {
                "document_id": document_id,
                "status": "failed",
                "error": str(e)[:1000],
            }

    async def reset_document_status(self, db: AsyncSession, document_id: int) -> None:
        """
        重置文档处理状态为待处理（用于重试失败文档）

        Args:
            db: 数据库会话
            document_id: 文档ID
        """
        document = await self.get_by_id(db, document_id, raise_not_found=False)
        if document:
            document.processing_status = ProcessingStatus.PENDING.value
            document.error_message = None
            await db.commit()

    # ---- 查询 ----

    async def get_segments_by_document_id(
        self, db: AsyncSession, document_id: int
    ) -> List[KnowledgeDocumentSegment]:
        """
        获取文档的所有分段

        Args:
            db: 数据库会话
            document_id: 文档ID

        Returns:
            分段列表
        """
        result = await db.execute(
            select(KnowledgeDocumentSegment)
            .where(KnowledgeDocumentSegment.document_id == document_id)
            .where(KnowledgeDocumentSegment.is_delete == 0)
            .order_by(KnowledgeDocumentSegment.segment_index)
        )
        return list(result.scalars().all())

    # ---- 删除 ----

    async def delete_document_with_segments(
        self, db: AsyncSession, document_id: int
    ) -> None:
        """
        删除文档及其所有分段、标题索引（包括向量数据）

        Args:
            db: 数据库会话
            document_id: 文档ID
        """
        document = await self.get_by_id(db, document_id, raise_not_found=False)
        if not document:
            return

        if document.file_path:
            import os

            if os.path.exists(document.file_path):
                os.remove(document.file_path)

        # 从 ChromaDB 按 document_id 删除所有关联向量（不依赖 DB 中的 vector_id）
        try:
            from app.services.vector_store_service import get_vector_store_service

            vector_store = get_vector_store_service()
            await vector_store.delete_by_document_id(document_id)
        except Exception as e:
            logger.warning(f"删除文档向量失败: document_id={document_id}, error={e}")

        await db.execute(
            update(KnowledgeDocumentSegment)
            .where(KnowledgeDocumentSegment.document_id == document_id)
            .values(is_delete=1)
        )

        await knowledge_title_service.delete_titles_by_document_id(db, document_id)

        await self.delete(db, document_id)

    # ---- 向量化 ----

    async def vectorize_document(
        self, db: AsyncSession, document_id: int, force: bool = False
    ) -> Dict[str, Any]:
        """
        向量化单个文档的所有分段

        Args:
            db: 数据库会话
            document_id: 文档ID
            force: 是否强制重新向量化

        Returns:
            向量化结果
        """
        document = await self.get_by_id(db, document_id, raise_not_found=False)
        if not document:
            return {
                "document_id": document_id,
                "document_title": "",
                "total_segments": 0,
                "vectorized_segments": 0,
                "failed_segments": 0,
            }

        segments = await self.get_segments_by_document_id(db, document_id)
        if not segments:
            return {
                "document_id": document_id,
                "document_title": document.title or "",
                "total_segments": 0,
                "vectorized_segments": 0,
                "failed_segments": 0,
            }

        if not force:
            unvectorized = [s for s in segments if not s.vector_id]
        else:
            unvectorized = segments
            vector_ids = [s.vector_id for s in segments if s.vector_id]
            if vector_ids:
                try:
                    from app.services.vector_store_service import (
                        get_vector_store_service,
                    )

                    vector_store = get_vector_store_service()
                    await vector_store.delete(vector_ids)
                except Exception:
                    pass

        if not unvectorized:
            return {
                "document_id": document_id,
                "document_title": document.title or "",
                "total_segments": len(segments),
                "vectorized_segments": len(segments),
                "failed_segments": 0,
            }

        from app.services.embedding_service import get_embedding_service_async
        from app.services.vector_store_service import get_vector_store_service

        embedding_service = await get_embedding_service_async()
        vector_store = get_vector_store_service()

        texts = [s.content for s in unvectorized]

        def on_progress(completed: int, total: int) -> None:
            logger.info(f"文档 {document_id} 向量化进度: {completed}/{total}")

        embeddings = await embedding_service.embed_texts(
            texts, progress_callback=on_progress
        )

        metadatas = []
        ids = []
        for s in unvectorized:
            metadatas.append(
                {
                    "document_id": s.document_id,
                    "knowledge_base_id": document.knowledge_base_id,
                    "segment_id": s.id,
                    "segment_index": s.segment_index,
                    "title": s.title or "",
                    "title_id": s.title_id if s.title_id is not None else 0,
                    "word_count": s.word_count,
                }
            )
            ids.append(f"seg_{s.id}")

        await vector_store.add_texts(
            texts=texts, embeddings=embeddings, metadatas=metadatas, ids=ids
        )

        vectorized_count = 0
        failed_count = 0
        for i, segment in enumerate(unvectorized):
            if i < len(ids):
                segment.vector_id = ids[i]
                vectorized_count += 1
            else:
                failed_count += 1

        await db.commit()

        return {
            "document_id": document_id,
            "document_title": document.title or "",
            "total_segments": len(segments),
            "vectorized_segments": len(segments) - len(unvectorized) + vectorized_count,
            "failed_segments": failed_count,
        }

    async def vectorize_knowledge_base(
        self, db: AsyncSession, knowledge_base_id: int, force: bool = False
    ) -> Dict[str, Any]:
        """
        批量向量化知识库下的所有文档

        Args:
            db: 数据库会话
            knowledge_base_id: 知识库ID
            force: 是否强制重新向量化

        Returns:
            批量向量化结果
        """
        stmt = select(KnowledgeDocument).where(
            KnowledgeDocument.knowledge_base_id == knowledge_base_id,
            KnowledgeDocument.is_delete == 0,
        )
        result = await db.execute(stmt)
        documents = result.scalars().all()

        total_segments = 0
        vectorized_segments = 0
        failed_segments = 0
        details = []

        for doc in documents:
            doc_result = await self.vectorize_document(db, doc.id, force)
            total_segments += doc_result["total_segments"]
            vectorized_segments += doc_result["vectorized_segments"]
            failed_segments += doc_result["failed_segments"]
            details.append(doc_result)

        return {
            "knowledge_base_id": knowledge_base_id,
            "total_documents": len(documents),
            "total_segments": total_segments,
            "vectorized_segments": vectorized_segments,
            "failed_segments": failed_segments,
            "details": details,
        }

    async def get_segments_by_knowledge_base_id(
        self, db: AsyncSession, knowledge_base_id: int
    ) -> List[KnowledgeDocumentSegment]:
        """
        获取知识库下的所有分段

        Args:
            db: 数据库会话
            knowledge_base_id: 知识库ID

        Returns:
            分段列表
        """
        stmt = (
            select(KnowledgeDocumentSegment)
            .join(
                KnowledgeDocument,
                KnowledgeDocumentSegment.document_id == KnowledgeDocument.id,
            )
            .where(
                KnowledgeDocument.knowledge_base_id == knowledge_base_id,
                KnowledgeDocument.is_delete == 0,
                KnowledgeDocumentSegment.is_delete == 0,
            )
            .order_by(
                KnowledgeDocumentSegment.document_id,
                KnowledgeDocumentSegment.segment_index,
            )
        )

        result = await db.execute(stmt)
        return list(result.scalars().all())


knowledge_document_service = KnowledgeDocumentService()

from pathlib import Path
import asyncio
from fastapi import APIRouter, Depends, File, Form, UploadFile, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db, AsyncSessionLocal
from app.api.base_api import BaseApi, RouteConfig
from app.models.knowledge_document import KnowledgeDocument, ProcessingStatus
from app.schemas.knowledge_schema import (
    KnowledgeDocumentBase,
    KnowledgeDocumentCreate,
    KnowledgeDocumentUpdate,
    KnowledgeDocumentUploadResult,
    KnowledgeDocumentSegmentBase,
    KnowledgeBaseVectorizeResult,
    SegmentSearchCondition,
)
from app.schemas.base_schema import ApiResponse
from app.services.knowledge_document_service import knowledge_document_service
from app.services.knowledge_title_service import knowledge_title_service


class KnowledgeDocumentApi(
    BaseApi[
        KnowledgeDocument,
        KnowledgeDocumentBase,
        KnowledgeDocumentCreate,
        KnowledgeDocumentCreate,
        KnowledgeDocumentUpdate,
    ]
):
    """文档 API"""

    def __init__(self):
        super().__init__(
            service=knowledge_document_service,
            router_prefix="/api/knowledge/document",
            router_tags=["知识库管理"],
            route_config=RouteConfig(enable_create=False),
        )
        self._register_custom_routes()

    async def delete(self, db: AsyncSession, id: int) -> None:
        """删除文档（同时删除分段和文件）"""
        await knowledge_document_service.delete_document_with_segments(db, id)

    def _register_custom_routes(self):
        """注册自定义路由"""

        @self.router.post(
            "/upload",
            response_model=ApiResponse[KnowledgeDocumentUploadResult],
            summary="上传文档",
        )
        async def upload_document(
            file: UploadFile = File(
                ..., description="文档文件（支持 txt、md、docx、pdf、xlsx）"
            ),
            knowledge_base_id: int = Form(..., description="知识库ID"),
            db: AsyncSession = Depends(get_db),
        ):
            """
            上传文档到知识库

            - 支持格式：txt、md、docx、pdf、xlsx
            - 文件保存后立即返回，后台定时任务异步完成解析、分段、向量化
            - 通过 processing_status 查询处理进度
            """
            document = await knowledge_document_service.upload_file(
                db=db, file=file, knowledge_base_id=knowledge_base_id
            )
            return ApiResponse.success(
                data=KnowledgeDocumentUploadResult.model_validate(
                    {
                        "id": document.id,
                        "title": document.title,
                        "file_type": document.file_type,
                        "processing_status": document.processing_status,
                    }
                ),
                msg="文档已上传，后台处理中",
            )

        @self.router.get(
            "/segments/{document_id}",
            response_model=ApiResponse[list[KnowledgeDocumentSegmentBase]],
            summary="获取文档分段列表",
        )
        async def get_document_segments(
            document_id: int, db: AsyncSession = Depends(get_db)
        ):
            """获取指定文档的所有分段"""
            segments = await knowledge_document_service.get_segments_by_document_id(
                db, document_id
            )
            views = [
                KnowledgeDocumentSegmentBase.model_validate(
                    {
                        "id": s.id,
                        "document_id": s.document_id,
                        "segment_index": s.segment_index,
                        "title": s.title,
                        "content": s.content,
                        "word_count": s.word_count,
                        "create_time": s.create_time,
                        "update_time": s.modify_time,
                    }
                )
                for s in segments
            ]
            return ApiResponse.success(data=views, msg="查询成功")

        @self.router.get("/download/{document_id}", summary="下载文档源文件")
        async def download_document(
            document_id: int, db: AsyncSession = Depends(get_db)
        ):
            """下载文档源文件"""
            document = await knowledge_document_service.get_by_id(
                db, document_id, raise_not_found=False
            )
            if not document:
                return ApiResponse.error(msg="文档不存在")

            if not document.file_path:
                return ApiResponse.error(msg="文件路径不存在")

            file_path = Path(document.file_path)
            if not file_path.exists():
                return ApiResponse.error(msg="文件不存在")

            return FileResponse(
                path=file_path,
                filename=document.title,
                media_type="application/octet-stream",
            )

        @self.router.get(
            "/content/{document_id}",
            response_model=ApiResponse[dict],
            summary="获取文档原文内容",
        )
        async def get_document_content(
            document_id: int, db: AsyncSession = Depends(get_db)
        ):
            """获取文档原文内容"""
            document = await knowledge_document_service.get_by_id(
                db, document_id, raise_not_found=False
            )
            if not document:
                return ApiResponse.error(msg="文档不存在")

            return ApiResponse.success(
                data={
                    "id": document.id,
                    "title": document.title,
                    "content": document.content,
                    "word_count": document.word_count,
                    "file_type": document.file_type,
                },
                msg="查询成功",
            )

        @self.router.post(
            "/vectorize/{knowledge_base_id}",
            response_model=ApiResponse[KnowledgeBaseVectorizeResult],
            summary="批量向量化知识库文档",
        )
        async def vectorize_knowledge_base(
            knowledge_base_id: int,
            force: bool = Query(
                False, description="是否强制重新向量化（已向量化的也会重新处理）"
            ),
            db: AsyncSession = Depends(get_db),
        ):
            """
            批量向量化知识库下的所有文档

            - 将文档分段转换为向量存储到 ChromaDB
            - 使用阿里云通义 Embedding 模型
            - 支持增量向量化（只处理未向量化的分段）
            - force=true 时强制重新向量化所有分段
            """
            result = await knowledge_document_service.vectorize_knowledge_base(
                db=db, knowledge_base_id=knowledge_base_id, force=force
            )
            return ApiResponse.success(
                data=KnowledgeBaseVectorizeResult.model_validate(result),
                msg=f"向量化完成，共处理 {result['vectorized_segments']} 个分段",
            )

        @self.router.post(
            "/vectorize/document/{document_id}",
            response_model=ApiResponse[dict],
            summary="向量化单个文档",
        )
        async def vectorize_document(
            document_id: int,
            force: bool = Query(False, description="是否强制重新向量化"),
            db: AsyncSession = Depends(get_db),
        ):
            """
            向量化单个文档的所有分段

            - 将文档分段转换为向量存储到 ChromaDB
            - 后台异步执行，接口立即返回
            """
            document = await knowledge_document_service.get_by_id(
                db, document_id, raise_not_found=False
            )
            if not document:
                return ApiResponse.error(msg="文档不存在")

            if document.processing_status != ProcessingStatus.COMPLETED.value:
                return ApiResponse.error(msg="文档未完成处理，不可重新向量化")

            document.processing_status = ProcessingStatus.VECTORIZING.value
            document.error_message = None
            await db.commit()

            asyncio.create_task(_vectorize_in_background(document_id, force))
            return ApiResponse.success(msg="重新向量化任务已提交")

        @self.router.post(
            "/reprocess/{document_id}",
            response_model=ApiResponse,
            summary="重新处理文档",
        )
        async def reprocess_document(
            document_id: int, db: AsyncSession = Depends(get_db)
        ):
            """
            重置文档状态为待处理，由定时任务重新解析、分段、向量化

            - 适用于处理失败的文档重试
            - 也可用于已处理文档的重新处理
            """
            document = await knowledge_document_service.get_by_id(
                db, document_id, raise_not_found=False
            )
            if not document:
                return ApiResponse.error(msg="文档不存在")

            await knowledge_document_service.reset_document_status(db, document_id)
            return ApiResponse.success(msg="已重置，等待后台处理")

        @self.router.post(
            "/search-segments",
            response_model=ApiResponse[list],
            summary="向量搜索知识库分段",
        )
        async def search_segments(
            condition: SegmentSearchCondition,
            db: AsyncSession = Depends(get_db),
        ):
            """
            按自然语言搜索知识库中的分段

            - 通过向量语义相似度匹配最相关的分段
            - 返回匹配的分段内容、所属文档和标题信息
            """
            results = await knowledge_title_service.vector_search(
                db=db,
                knowledge_base_id=condition.knowledge_base_id,
                query=condition.query,
                top_k=condition.top_k,
            )
            return ApiResponse.success(data=results, msg=f"找到 {len(results)} 条结果")


async def _vectorize_in_background(document_id: int, force: bool) -> None:
    """后台执行向量化任务"""
    import logging

    logger = logging.getLogger(__name__)
    try:
        async with AsyncSessionLocal() as bg_db:
            result = await knowledge_document_service.vectorize_document(
                bg_db, document_id, force=force
            )
            logger.info(
                f"文档 {document_id} 向量化完成，"
                f"成功 {result.get('vectorized_segments', 0)} 段"
            )
            document = await knowledge_document_service.get_by_id(
                bg_db, document_id, raise_not_found=False
            )
            if document:
                document.processing_status = ProcessingStatus.COMPLETED.value
                document.error_message = None
                await bg_db.commit()
    except Exception as e:
        logger.error(f"文档 {document_id} 后台向量化失败: {e}", exc_info=True)
        try:
            async with AsyncSessionLocal() as err_db:
                document = await knowledge_document_service.get_by_id(
                    err_db, document_id, raise_not_found=False
                )
                if document:
                    document.processing_status = ProcessingStatus.FAILED.value
                    document.error_message = str(e)[:1000]
                    await err_db.commit()
        except Exception:
            pass


router = APIRouter()
knowledge_document_api = KnowledgeDocumentApi()
router.include_router(knowledge_document_api.router)

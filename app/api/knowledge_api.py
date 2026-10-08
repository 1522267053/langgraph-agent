"""
知识库 API 路由
处理知识库和文档相关的路由定义
"""

import io
from datetime import datetime

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.base_api import BaseApi
from app.config.database import get_db
from app.models.knowledge_base import KnowledgeBase
from app.services.flow_transfer_service import flow_transfer_service
from app.services.knowledge_base_service import knowledge_base_service
from app.schemas.base_schema import ApiResponse
from app.schemas.knowledge_schema import (
    KnowledgeBaseBase,
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
)


class KnowledgeExportRequest(BaseModel):
    ids: list[int] = Field(..., description="要导出的知识库ID列表")


class KnowledgeBaseApi(
    BaseApi[
        KnowledgeBase,
        KnowledgeBaseBase,
        KnowledgeBaseBase,
        KnowledgeBaseCreate,
        KnowledgeBaseUpdate,
    ]
):
    """知识库 API"""

    def __init__(self):
        super().__init__(
            service=knowledge_base_service,
            router_prefix="/api/knowledge/base",
            router_tags=["知识库管理"],
        )
        self._register_custom_routes()

    def _register_custom_routes(self):
        """注册知识库单独导出/导入路由"""

        @self.router.post("/export", summary="导出知识库（.lga 打包）")
        async def export_knowledge_bases(
            body: KnowledgeExportRequest, db: AsyncSession = Depends(get_db)
        ):
            """导出知识库及其原始文档，打包为 .lga（zip）"""
            try:
                zip_bytes = await flow_transfer_service.export_knowledge_package(
                    db, body.ids
                )
            except ValueError as e:
                return ApiResponse.error(msg=str(e))
            except Exception as e:
                return ApiResponse.error(msg=f"导出失败: {e}")
            filename = f"knowledge_export_{datetime.now().strftime('%Y%m%d%H%M%S')}.lga"
            return StreamingResponse(
                io.BytesIO(zip_bytes),
                media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        @self.router.post(
            "/import",
            response_model=ApiResponse,
            summary="导入知识库（.lga 打包）",
        )
        async def import_knowledge_bases(
            file: UploadFile, db: AsyncSession = Depends(get_db)
        ):
            """从知识库 .lga 打包文件导入，同名知识库自动创建副本"""
            content = await file.read()
            try:
                (
                    created,
                    warnings,
                ) = await flow_transfer_service.import_knowledge_package(db, content)
                return ApiResponse.success(
                    data={"created": created, "warnings": warnings},
                    msg=f"导入 {len(created)} 个知识库"
                    + ("，存在部分警告" if warnings else ""),
                )
            except ValueError as e:
                return ApiResponse.error(msg=str(e))
            except Exception as e:
                return ApiResponse.error(msg=f"导入失败: {e}")


knowledge_base_api = KnowledgeBaseApi()
router = APIRouter()
router.include_router(knowledge_base_api.router)

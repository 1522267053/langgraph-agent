from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.api.base_api import BaseApi, RouteConfig
from app.schemas.file_schema import FileBase, FileCondition, FileView
from app.schemas.base_schema import ApiResponse, PaginatedResponse, PaginationParams
from app.models.file import File as FileModel
from app.services.file_service import file_service


class FileApi(BaseApi[FileModel, FileBase, FileCondition, FileView, FileView]):
    """文件 API"""

    def __init__(self):
        super().__init__(
            service=file_service,
            router_prefix="/api/file",
            router_tags=["文件管理"],
            route_config=RouteConfig(
                enable_page=True,
                enable_get=False,
                enable_create=False,
                enable_update=False,
                enable_delete=False,
                enable_batch_delete=False,
            ),
        )
        self._register_custom_routes()

    def _register_custom_routes(self):

        @self.router.post("/upload", summary="上传文件")
        async def upload_file(
            file: UploadFile = File(..., description="文件"),
            source_type: Optional[str] = Form(None, description="来源类型：flow/agent"),
            db: AsyncSession = Depends(get_db),
        ):
            file_obj = await file_service.upload_file(db, file, source_type)
            view = FileView.model_to_view(file_obj)
            view.download_url = f"/api/file/download/{file_obj.id}"
            view.preview_url = file_obj.preview_url or f"/{file_obj.file_path}"
            return ApiResponse.success(data=view, msg="上传成功")

        @self.router.get("/download/{file_id}", summary="下载/预览文件")
        async def download_file(file_id: int, db: AsyncSession = Depends(get_db)):
            try:
                (
                    file_path,
                    original_name,
                    mime_type,
                ) = await file_service.get_download_path(db, file_id)
            except FileNotFoundError:
                return ApiResponse.error(msg="文件不存在")
            return FileResponse(
                path=file_path,
                filename=original_name,
                media_type=mime_type,
            )

        @self.router.get("/delete/{file_id}", summary="删除文件")
        async def delete_file(file_id: int, db: AsyncSession = Depends(get_db)):
            await file_service.delete_with_physical(db, file_id)
            return ApiResponse.success(msg="删除成功")

        @self.router.post("/deleteBatch", summary="批量删除文件")
        async def batch_delete_files(
            ids: list[int], db: AsyncSession = Depends(get_db)
        ):
            for fid in ids:
                await file_service.delete_with_physical(db, fid)
            return ApiResponse.success(msg="删除成功")

    def _register_page_route(self):
        """注册分页查询路由，为每条记录追加 download_url"""

        @self.router.post(
            "/page", response_model=ApiResponse[PaginatedResponse[FileView]]
        )
        async def page_query(
            query_params: PaginationParams[FileCondition],
            db: AsyncSession = Depends(get_db),
        ):
            items, total = await self.service.page_query(db, query_params)
            views = []
            for item in items:
                v = FileView.model_to_view(item)
                v.download_url = f"/api/file/download/{item.id}"
                v.preview_url = item.preview_url or f"/{item.file_path}"
                views.append(v)
            paginated_data = PaginatedResponse.create(
                items=views,
                total=total,
                page=query_params.page,
                page_size=query_params.page_size,
            )
            return ApiResponse.success(data=paginated_data, msg="查询成功")


router = APIRouter()
file_api = FileApi()
router.include_router(file_api.router)

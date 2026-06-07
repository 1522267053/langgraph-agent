"""
市场 API 路由
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.schemas.base_schema import ApiResponse
from app.schemas.marketplace_schema import (
    MarketplaceConfigRequest,
    MarketplaceResourceListRequest,
)
from app.services.marketplace_service import marketplace_service

logger = logging.getLogger(__name__)


class MarketplaceApi:
    """市场 API"""

    def __init__(self):
        self.router = APIRouter(prefix="/api/marketplace", tags=["市场"])
        self._register_routes()

    def _register_routes(self):
        @self.router.get(
            "/status", response_model=ApiResponse, summary="获取市场连接状态"
        )
        async def get_status(db: AsyncSession = Depends(get_db)):
            """返回市场服务器地址和连接状态"""
            server_url = await marketplace_service.get_server_url(db)
            connected = await marketplace_service.is_connected(db)
            return ApiResponse.success(
                data={
                    "server_url": server_url or "",
                    "connected": connected,
                },
                msg="查询成功",
            )

        @self.router.post(
            "/config", response_model=ApiResponse, summary="保存市场服务器地址"
        )
        async def save_config(
            body: MarketplaceConfigRequest, db: AsyncSession = Depends(get_db)
        ):
            """保存市场服务器地址，自动尝试连接"""
            server_url = body.server_url.strip()
            if not server_url:
                await marketplace_service.save_server_url(db, "")
                await marketplace_service.disconnect(db)
                return ApiResponse.success(msg="已清除市场配置")
            await marketplace_service.save_server_url(db, server_url)
            token = await marketplace_service.connect(db)
            if token:
                return ApiResponse.success(data={"connected": True}, msg="连接成功")
            error_msg = (
                marketplace_service.get_connect_error_msg()
                or "连接失败，请检查服务器地址和网络"
            )
            return ApiResponse.error(msg=error_msg)

        @self.router.get(
            "/config", response_model=ApiResponse, summary="获取市场服务器配置"
        )
        async def get_config(db: AsyncSession = Depends(get_db)):
            """获取市场服务器配置"""
            server_url = await marketplace_service.get_server_url(db)
            connected = await marketplace_service.is_connected(db)
            return ApiResponse.success(
                data={
                    "server_url": server_url or "",
                    "connected": connected,
                },
                msg="查询成功",
            )

        @self.router.post(
            "/connect", response_model=ApiResponse, summary="连接市场服务器"
        )
        async def connect(db: AsyncSession = Depends(get_db)):
            """用本地账号登录市场服务器"""
            server_url = await marketplace_service.get_server_url(db)
            if not server_url:
                return ApiResponse.error(msg="请先配置市场服务器地址")
            available = await marketplace_service.check_server_available(db)
            if not available:
                return ApiResponse.error(msg="服务器不可达")
            token = await marketplace_service.connect(db)
            if token:
                return ApiResponse.success(data={"connected": True}, msg="连接成功")
            error_msg = (
                marketplace_service.get_connect_error_msg()
                or "连接失败，请检查服务器地址和网络"
            )
            return ApiResponse.error(msg=error_msg)

        @self.router.post(
            "/disconnect", response_model=ApiResponse, summary="断开市场连接"
        )
        async def disconnect(db: AsyncSession = Depends(get_db)):
            """断开市场连接"""
            await marketplace_service.disconnect(db)
            return ApiResponse.success(msg="已断开连接")

        @self.router.post(
            "/resources", response_model=ApiResponse, summary="获取市场资源列表"
        )
        async def list_resources(
            body: MarketplaceResourceListRequest,
            db: AsyncSession = Depends(get_db),
        ):
            """代理获取市场资源列表"""
            data = await marketplace_service.list_resources(
                db,
                resource_type=body.resource_type or "",
                category=body.category or "",
                keyword=body.keyword or "",
                page=body.page or 1,
                page_size=body.page_size or 10,
            )
            if data is None:
                return ApiResponse.error(msg="市场未连接或请求失败")
            return ApiResponse.success(data=data, msg="查询成功")

        @self.router.get(
            "/resources/{resource_id}",
            response_model=ApiResponse,
            summary="获取市场资源详情",
        )
        async def get_resource_detail(
            resource_id: int, db: AsyncSession = Depends(get_db)
        ):
            """代理获取市场资源详情"""
            data = await marketplace_service.get_resource_detail(db, resource_id)
            if data is None:
                return ApiResponse.error(msg="市场未连接或请求失败")
            return ApiResponse.success(data=data, msg="查询成功")

        @self.router.post(
            "/import/{resource_id}",
            response_model=ApiResponse,
            summary="导入市场资源到本地",
        )
        async def import_resource(resource_id: int, db: AsyncSession = Depends(get_db)):
            """从市场下载资源并导入到本地"""
            from app.services.marketplace_import_service import (
                marketplace_import_service,
            )

            resource_info = await marketplace_service.get_resource_detail(
                db, resource_id
            )
            if resource_info is None:
                return ApiResponse.error(msg="获取资源信息失败")
            file_bytes = await marketplace_service.download_resource(db, resource_id)
            if file_bytes is None:
                return ApiResponse.error(msg="下载资源文件失败")
            result = await marketplace_import_service.import_resource(
                db, resource_info, file_bytes
            )
            return ApiResponse.success(data=result, msg="导入完成")

        @self.router.get(
            "/categories", response_model=ApiResponse, summary="获取市场分类列表"
        )
        async def list_categories(
            resource_type: str = "", db: AsyncSession = Depends(get_db)
        ):
            """代理获取市场分类列表"""
            data = await marketplace_service.list_categories(db, resource_type)
            if data is None:
                return ApiResponse.error(msg="市场未连接或请求失败")
            return ApiResponse.success(data=data, msg="查询成功")


marketplace_api = MarketplaceApi()
router = marketplace_api.router

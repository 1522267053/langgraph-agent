"""
WebSocket 网关 API 路由

提供 网关配置的 CRUD 管理。
外部触发通过 WebSocket ``ws://host/ws/trigger/{token}`` 实现（见 ws_trigger_api.py）。
"""

import logging

from fastapi import Depends, File, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.schemas.base_schema import ApiResponse
from app.services.file_service import file_service
from app.services.flow_service import flow_service
from app.services.ws_gateway_service import ws_gateway_service
from app.schemas.ws_gateway_schema import (
    WsGatewayConfigBase,
    WsGatewayConfigCreate,
    WsGatewayConfigUpdate,
)
from app.api.base_api import BaseApi, RouteConfig
from app.models.ws_gateway import WsGatewayConfig

logger = logging.getLogger(__name__)


class WsGatewayApi(
    BaseApi[
        WsGatewayConfig,
        WsGatewayConfigBase,
        WsGatewayConfigBase,
        WsGatewayConfigCreate,
        WsGatewayConfigUpdate,
    ]
):
    """WebSocket 网关 API"""

    def __init__(self):
        super().__init__(
            service=ws_gateway_service,
            router_prefix="/api/ws-gateway",
            router_tags=["WebSocket 网关"],
            route_config=RouteConfig(enable_get=False),
        )
        self._register_custom_routes()

    async def create(
        self, db: AsyncSession, data: WsGatewayConfigCreate
    ) -> WsGatewayConfig:
        """创建网关（自动生成 token）"""
        return await ws_gateway_service.create(db, data)

    def _register_custom_routes(self):
        """注册自定义路由"""

        @self.router.get(
            "/get/{id}/url",
            response_model=ApiResponse,
            summary="获取 WebSocket 连接地址",
        )
        async def get_ws_gateway_url(id: int, db: AsyncSession = Depends(get_db)):
            """获取 网关的 WebSocket 触发地址"""
            gateway = await ws_gateway_service.get_by_id(db, id)
            if not gateway:
                return ApiResponse.error(msg="网关不存在")

            url = f"/ws/trigger/{gateway.token}"
            return ApiResponse.success(
                data={"url": url, "token": gateway.token}, msg="查询成功"
            )

        @self.router.post("/upload", summary="WS 网关文件上传（token 鉴权）")
        async def ws_upload(
            token: str,
            file: UploadFile = File(..., description="文件"),
            db: AsyncSession = Depends(get_db),
        ):
            """外部客户端通过 gateway token 上传文件，返回 file_id 供 execute 使用

            文件归属绑定到网关关联的 flow，下载时严格校验同 flow 归属。
            """
            gateway = await ws_gateway_service.get_by_token(db, token)
            if not gateway or not gateway.is_enabled:
                return ApiResponse.error(msg="token 无效或网关已禁用")

            flow = await flow_service.get_by_id(
                db, gateway.flow_id, raise_not_found=False
            )
            source_type = flow.flow_type if flow else "flow"

            try:
                file_obj = await file_service.upload_file(db, file, source_type)
            except ValueError as e:
                return ApiResponse.error(msg=str(e))

            # 绑定归属，严格模式下只允许同网关下载
            file_obj.flow_id = gateway.flow_id
            await db.commit()

            return ApiResponse.success(
                data={
                    "file_id": file_obj.id,
                    "download_url": (
                        f"/api/ws-gateway/download/{file_obj.id}?token={token}"
                    ),
                    "mime_type": file_obj.mime_type,
                    "file_size": file_obj.file_size,
                },
                msg="上传成功",
            )

        @self.router.get("/download/{file_id}", summary="WS 网关文件下载（token 鉴权）")
        async def ws_download(
            file_id: int,
            token: str,
            db: AsyncSession = Depends(get_db),
        ):
            """通过 gateway token 下载文件，严格校验文件归属该网关关联的 flow"""
            gateway = await ws_gateway_service.get_by_token(db, token)
            if not gateway or not gateway.is_enabled:
                return ApiResponse.error(msg="token 无效或网关已禁用")

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


ws_gateway_api = WsGatewayApi()
router = ws_gateway_api.router

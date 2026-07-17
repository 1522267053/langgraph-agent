"""
WebSocket 网关 API 路由

提供 网关配置的 CRUD 管理。
外部触发通过 WebSocket ``ws://host/ws/trigger/{token}`` 实现（见 ws_trigger_api.py）。
"""

import logging

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.schemas.base_schema import ApiResponse
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


ws_gateway_api = WsGatewayApi()
router = ws_gateway_api.router

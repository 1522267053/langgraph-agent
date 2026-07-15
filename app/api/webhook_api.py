"""
Webhook API 路由

提供 Webhook 配置的 CRUD 管理。
外部触发通过 WebSocket ``ws://host/ws/trigger/{token}`` 实现（见 ws_trigger_api.py）。
"""

import logging

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.schemas.base_schema import ApiResponse
from app.services.webhook_service import webhook_service
from app.schemas.webhook_schema import (
    WebhookConfigBase,
    WebhookConfigCreate,
    WebhookConfigUpdate,
)
from app.api.base_api import BaseApi, RouteConfig
from app.models.webhook import WebhookConfig

logger = logging.getLogger(__name__)


class WebhookApi(
    BaseApi[
        WebhookConfig,
        WebhookConfigBase,
        WebhookConfigBase,
        WebhookConfigCreate,
        WebhookConfigUpdate,
    ]
):
    """Webhook API"""

    def __init__(self):
        super().__init__(
            service=webhook_service,
            router_prefix="/api/webhook",
            router_tags=["Webhook"],
            route_config=RouteConfig(enable_get=False),
        )
        self._register_custom_routes()

    async def create(
        self, db: AsyncSession, data: WebhookConfigCreate
    ) -> WebhookConfig:
        """创建 Webhook（自动生成 token）"""
        return await webhook_service.create(db, data)

    def _register_custom_routes(self):
        """注册自定义路由"""

        @self.router.get(
            "/get/{id}/url",
            response_model=ApiResponse,
            summary="获取 WebSocket 连接地址",
        )
        async def get_webhook_url(id: int, db: AsyncSession = Depends(get_db)):
            """获取 Webhook 的 WebSocket 触发地址"""
            webhook = await webhook_service.get_by_id(db, id)
            if not webhook:
                return ApiResponse.error(msg="Webhook 不存在")

            url = f"/ws/trigger/{webhook.token}"
            return ApiResponse.success(
                data={"url": url, "token": webhook.token}, msg="查询成功"
            )


webhook_api = WebhookApi()
router = webhook_api.router

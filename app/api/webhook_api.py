"""
Webhook API 路由

提供 Webhook 配置的 CRUD 管理和外部触发端点。
触发端点 /api/webhook/trigger/{token} 免认证（通过 token 认证）。
"""

import logging

from fastapi import Depends, Body
from pydantic import BaseModel
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


class WebhookTriggerResponse(BaseModel):
    """Webhook 触发响应"""

    status: str
    webhook_id: int


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

        @self.router.post(
            "/trigger/{token}",
            response_model=ApiResponse,
            summary="触发 Webhook（免认证）",
        )
        async def trigger_webhook(
            token: str,
            body: dict = Body(default={}),
            db: AsyncSession = Depends(get_db),
        ):
            """外部系统通过 token 触发流程执行

            输入参数合并优先级：请求体 > Webhook 默认配置。
            执行异步进行，立即返回状态。
            """
            webhook = await webhook_service.get_by_token(db, token)
            if not webhook:
                return ApiResponse.error(msg="Webhook 不存在", code=0)
            if not webhook.is_enabled:
                return ApiResponse.error(msg="Webhook 已禁用")

            # 合并输入：默认模板 < 请求体
            input_data = {**(webhook.input_config or {}), **body}

            # 记录调用
            await webhook_service.record_call(db, webhook.id)

            # 异步触发执行
            result = await webhook_service.trigger_flow(db, webhook, input_data)

            return ApiResponse.success(
                data=result,
                msg="触发成功",
            )

        @self.router.get(
            "/get/{id}/url",
            response_model=ApiResponse,
            summary="获取 Webhook 完整 URL",
        )
        async def get_webhook_url(id: int, db: AsyncSession = Depends(get_db)):
            """获取 Webhook 的完整触发 URL"""
            webhook = await webhook_service.get_by_id(db, id)
            if not webhook:
                return ApiResponse.error(msg="Webhook 不存在")

            # 构建 URL（相对路径，前端拼接 host）
            url = f"/api/webhook/trigger/{webhook.token}"
            return ApiResponse.success(
                data={"url": url, "token": webhook.token}, msg="查询成功"
            )


webhook_api = WebhookApi()
router = webhook_api.router

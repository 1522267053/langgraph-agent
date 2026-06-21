"""
Webhook API 路由

提供 Webhook 配置的 CRUD 管理和外部触发端点。
触发端点 /api/webhook/trigger/{token} 免认证（通过 token 认证）。
查询端点 /api/webhook/query/{token}/... 免认证（通过 token 认证）。
"""

import logging

from fastapi import Depends, Body, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.schemas.base_schema import ApiResponse
from app.services.webhook_service import webhook_service
from app.schemas.webhook_schema import (
    WebhookConfigBase,
    WebhookConfigCreate,
    WebhookConfigUpdate,
    WebhookCallRecordResponse,
    WebhookCallRecordListResponse,
    WebhookMessageResponse,
    WebhookMessageListResponse,
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

        # ---- 免认证查询接口（通过 token 鉴权） ----

        @self.router.get(
            "/query/{token}/calls",
            response_model=ApiResponse,
            summary="查询调用记录列表（免认证）",
        )
        async def query_call_records(
            token: str,
            page: int = Query(1, ge=1, description="页码"),
            page_size: int = Query(20, ge=1, le=100, description="每页条数"),
            db: AsyncSession = Depends(get_db),
        ):
            """通过 token 查询该 Webhook 的所有调用记录列表"""
            records, total = await webhook_service.get_call_records_by_token(
                db, token, page, page_size
            )
            items = []
            for r in records:
                items.append(
                    WebhookCallRecordResponse(
                        id=r.id,
                        webhook_id=r.webhook_id,
                        flow_id=r.flow_id,
                        ref_type=r.ref_type,
                        ref_id=r.ref_id,
                        input_data=r.input_data,
                        status=r.status,
                        output_data=r.output_data,
                        error_message=r.error_message,
                        callback_status=r.callback_status,
                        started_at=r.started_at,
                        finished_at=r.finished_at,
                    )
                )
            return ApiResponse.success(
                data=WebhookCallRecordListResponse(total=total, items=items),
                msg="查询成功",
            )

        @self.router.get(
            "/query/{token}/calls/{call_id}",
            response_model=ApiResponse,
            summary="查询单条调用记录详情（免认证）",
        )
        async def query_call_record_detail(
            token: str,
            call_id: int,
            db: AsyncSession = Depends(get_db),
        ):
            """通过 token + call_id 查询单条调用记录详情"""
            record = await webhook_service.get_call_record_by_token(db, token, call_id)
            if not record:
                return ApiResponse.error(msg="调用记录不存在")

            return ApiResponse.success(
                data=WebhookCallRecordResponse(
                    id=record.id,
                    webhook_id=record.webhook_id,
                    flow_id=record.flow_id,
                    ref_type=record.ref_type,
                    ref_id=record.ref_id,
                    input_data=record.input_data,
                    status=record.status,
                    output_data=record.output_data,
                    error_message=record.error_message,
                    callback_status=record.callback_status,
                    started_at=record.started_at,
                    finished_at=record.finished_at,
                ),
                msg="查询成功",
            )

        @self.router.get(
            "/query/{token}/calls/{call_id}/messages",
            response_model=ApiResponse,
            summary="查询调用记录的消息列表（免认证）",
        )
        async def query_call_record_messages(
            token: str,
            call_id: int,
            before_id: int = Query(None, description="游标ID（返回此ID之前的消息）"),
            limit: int = Query(20, ge=1, le=100, description="每页条数"),
            db: AsyncSession = Depends(get_db),
        ):
            """通过 token + call_id 查询该次调用产生的消息列表

            自动按 ref_type 分流：
            - session（Agent）→ 查 agent_message 表
            - execution（Flow）→ 查 conversation_message 表
            """
            record = await webhook_service.get_call_record_by_token(db, token, call_id)
            if not record:
                return ApiResponse.error(msg="调用记录不存在")

            messages, total = await webhook_service.get_call_record_messages(
                db, record, before_id, limit
            )

            items = []
            for m in messages:
                items.append(
                    WebhookMessageResponse(
                        id=m.id,
                        role=m.role,
                        content=m.content,
                        thinking=getattr(m, "thinking", None),
                        tool_calls=getattr(m, "tool_calls", None),
                        tool_call_id=getattr(m, "tool_call_id", None),
                        status=getattr(m, "status", None),
                        sequence=getattr(m, "sequence", None),
                        created_at=m.create_time,
                    )
                )
            return ApiResponse.success(
                data=WebhookMessageListResponse(total=total, items=items),
                msg="查询成功",
            )


webhook_api = WebhookApi()
router = webhook_api.router

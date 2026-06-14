"""
Webhook 配置服务

提供 Webhook 的 CRUD 和触发执行功能。
触发执行采用异步模式：立即返回 execution_id，后台执行完成后通过 WebSocket 通知 + callback_url 回调。
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook import WebhookConfig
from app.schemas.webhook_schema import (
    WebhookConfigCreate,
    WebhookConfigUpdate,
    WebhookConfigCondition,
)
from app.services.base_service import BaseService

logger = logging.getLogger(__name__)


class WebhookService(
    BaseService[WebhookConfig, WebhookConfigCreate, WebhookConfigUpdate]
):
    """Webhook 配置服务"""

    def __init__(self):
        super().__init__(WebhookConfig)

    def _apply_filters(self, query, count_query, condition: WebhookConfigCondition):
        """应用查询过滤条件"""
        query, count_query = super()._apply_filters(query, count_query, condition)
        if condition:
            if hasattr(condition, "name") and condition.name:
                query, count_query = self._apply_like_filter(
                    query, count_query, "name", condition.name
                )
            if hasattr(condition, "flow_id") and condition.flow_id:
                query = query.where(WebhookConfig.flow_id == condition.flow_id)
                count_query = count_query.where(
                    WebhookConfig.flow_id == condition.flow_id
                )
            if hasattr(condition, "is_enabled") and condition.is_enabled is not None:
                query = query.where(WebhookConfig.is_enabled == condition.is_enabled)
                count_query = count_query.where(
                    WebhookConfig.is_enabled == condition.is_enabled
                )
        return query, count_query

    async def create(
        self, db: AsyncSession, obj_in: WebhookConfigCreate
    ) -> WebhookConfig:
        """创建 Webhook（自动生成 token）"""
        import uuid

        model = obj_in.to_model(WebhookConfig)
        model.token = uuid.uuid4().hex
        model.call_count = 0
        db.add(model)
        await db.commit()
        await db.refresh(model)
        return model

    async def get_by_token(
        self, db: AsyncSession, token: str
    ) -> Optional[WebhookConfig]:
        """通过 token 查找 Webhook 配置"""
        stmt = select(WebhookConfig).where(
            WebhookConfig.token == token,
            WebhookConfig.is_delete == 0,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def record_call(self, db: AsyncSession, webhook_id: int) -> None:
        """记录一次调用（更新调用次数和最后调用时间）"""
        webhook = await self.get_by_id(db, webhook_id)
        if webhook:
            webhook.call_count = (webhook.call_count or 0) + 1
            webhook.last_call_time = datetime.now()
            await db.commit()

    async def trigger_flow(
        self, db: AsyncSession, webhook: WebhookConfig, input_data: dict
    ) -> dict:
        """异步触发流程执行

        后台执行流程，完成后通过 WebSocket 通知应用用户。
        如果配置了 callback_url，还会 POST 回调通知外部系统。

        Args:
            db: 数据库会话
            webhook: Webhook 配置
            input_data: 合并后的输入数据

        Returns:
            {"status": "started", "webhook_id": webhook.id}
        """
        flow_id = webhook.flow_id

        # 后台异步执行（fire-and-forget）
        asyncio.create_task(
            self._execute_webhook_flow(
                flow_id=flow_id,
                input_data=input_data,
                flow_name="",
                callback_url=webhook.callback_url,
                webhook_name=webhook.name,
            )
        )

        return {"status": "started", "webhook_id": webhook.id}

    async def _execute_webhook_flow(
        self,
        flow_id: int,
        input_data: dict,
        flow_name: str,
        callback_url: Optional[str],
        webhook_name: str,
    ) -> None:
        """后台执行 Webhook 触发的流程

        消费 execute_stream 生成器直到完成，完成后发送 callback 回调。
        WebSocket 通知由 flow_executor_service 的完成点自动处理。
        """
        from app.services.flow_executor_service import flow_executor_service
        from app.config.database import AsyncSessionLocal

        # 先获取流程名称（用于通知）
        if not flow_name:
            try:
                async with AsyncSessionLocal() as db:
                    from app.services.flow_service import flow_service

                    flow = await flow_service.get_by_id(
                        db, flow_id, raise_not_found=False
                    )
                    if flow:
                        flow_name = flow.name or ""
            except Exception:
                pass

        output_data = None
        error_message = None
        status = "unknown"

        try:
            async for event in flow_executor_service.execute_stream(
                flow_id, input_data=input_data
            ):
                event_type = event.get("type")
                if event_type == "flow_done":
                    status = event.get("data", {}).get("status", "success")
                    output_data = event.get("data", {}).get("output_data")
                elif event_type == "error":
                    status = "failed"
                    error_message = event.get("data", {}).get("message")
        except Exception as e:
            logger.exception(f"Webhook 流程执行异常: {e}")
            status = "failed"
            error_message = str(e)

        # 发送 callback 回调
        if callback_url:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    await client.post(
                        callback_url,
                        json={
                            "webhook_name": webhook_name,
                            "flow_id": flow_id,
                            "status": status,
                            "output_data": output_data,
                            "error": error_message,
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
            except Exception as e:
                logger.warning(f"Webhook callback 失败: {callback_url}, {e}")


webhook_service = WebhookService()

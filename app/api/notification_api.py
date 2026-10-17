"""
统一通知中心 API 路由

提供通知分页查询、未读数、已读标记、清空等操作。
"""

from typing import Any

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.base_api import BaseApi, RouteConfig
from app.config.database import get_db
from app.models.notification import Notification
from app.schemas.base_schema import ApiResponse
from app.schemas.notification_schema import (
    NotificationBase,
    NotificationCondition,
    NotificationCreate,
    NotificationUpdate,
)
from app.services.notification_service import notification_service


class NotificationApi(
    BaseApi[
        Notification,
        NotificationBase,
        NotificationCondition,
        NotificationCreate,
        NotificationUpdate,
    ]
):
    """统一通知中心 API"""

    def __init__(self):
        super().__init__(
            service=notification_service,
            router_prefix="/api/notification",
            router_tags=["通知中心"],
            route_config=RouteConfig(
                enable_page=True,
                enable_get=True,
                enable_create=False,  # 通知由服务层内部创建，前端不直接创建
                enable_update=False,
                enable_delete=True,
                enable_batch_delete=True,
            ),
        )
        self._register_custom_routes()

    def _register_custom_routes(self):
        """注册自定义路由"""

        @self.router.get("/unread-count", response_model=ApiResponse[dict[str, Any]])
        async def get_unread_count(db: AsyncSession = Depends(get_db)):
            """未读通知数量（铃铛角标）"""
            count = await notification_service.get_unread_count(db)
            return ApiResponse.success(data={"count": count}, msg="查询成功")

        @self.router.post("/mark-read/{id}", response_model=ApiResponse)
        async def mark_read(id: int, db: AsyncSession = Depends(get_db)):
            """标记单条通知已读"""
            exists = await notification_service.mark_read(db, id)
            if not exists:
                return ApiResponse.error(msg="通知不存在")
            return ApiResponse.success(msg="已标记已读")

        @self.router.post("/mark-all-read", response_model=ApiResponse[dict[str, Any]])
        async def mark_all_read(db: AsyncSession = Depends(get_db)):
            """全部标记已读"""
            count = await notification_service.mark_all_read(db)
            return ApiResponse.success(data={"count": count}, msg="已全部标记已读")

        @self.router.post("/clear-read", response_model=ApiResponse[dict[str, Any]])
        async def clear_read(db: AsyncSession = Depends(get_db)):
            """清空所有已读通知"""
            count = await notification_service.clear_read(db)
            return ApiResponse.success(data={"count": count}, msg="已清空已读通知")


notification_api = NotificationApi()
router = notification_api.router

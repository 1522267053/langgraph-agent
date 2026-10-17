"""
统一通知中心服务

所有通知的唯一入口：先落库（notification 表），再经 WebSocket 推送前端。
落库失败不阻断推送（通知属辅助提示，丢一条无实质影响）。

现有通知源收敛口径：
- ws_manager.notify_execution_done / notify_agenda_reminder 内部已追加落库，
  既有调用点（flow/agent 执行器、调度器、定时任务）无需逐个改造；
- 新增通知源直接调用 notification_service.notify()。
"""

import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.schemas.notification_schema import (
    NotificationCondition,
    NotificationCreate,
    NotificationUpdate,
)
from app.services.base_service import BaseService

logger = logging.getLogger(__name__)


class NotificationService(
    BaseService[Notification, NotificationCreate, NotificationUpdate]
):
    """统一通知服务：落库 + WebSocket 推送二合一"""

    def __init__(self):
        super().__init__(Notification)

    # ---- 统一入口 ----

    async def notify(
        self,
        db: AsyncSession,
        *,
        type: str,
        title: str,
        content: Optional[str] = None,
        ref_type: Optional[str] = None,
        ref_id: Optional[int] = None,
        username: Optional[str] = None,
        browser_notify: bool = True,
        push: bool = True,
    ) -> Optional[Notification]:
        """创建通知并推送（统一入口）

        Args:
            db: 数据库会话
            type: 通知类型（NotificationType）
            title: 标题
            content: 正文
            ref_type: 关联类型（NotificationRefType），前端跳转用
            ref_id: 关联 ID
            username: 定向推送用户名（None 时全局广播）
            browser_notify: 是否触发浏览器桌面通知
            push: 是否 WebSocket 推送（False 仅落库）

        Returns:
            通知记录（落库失败返回 None，不抛异常）
        """
        notification: Optional[Notification] = None
        try:
            create = NotificationCreate(
                type=type,
                title=title[:255] if title else "通知",
                content=content,
                ref_type=ref_type,
                ref_id=ref_id,
            )
            notification = await self.create(db, create)
        except Exception as e:
            # 落库失败不阻断推送（通知属辅助提示）
            logger.warning(f"通知落库失败 type={type} title={title}: {e}")

        if push:
            try:
                unread = await self.get_unread_count(db)
            except Exception:
                unread = 0
            payload = {
                "notification_id": notification.id if notification else None,
                "unread_count": unread,
                "type": type,
                "title": title,
                "content": content,
                "ref_type": ref_type,
                "ref_id": ref_id,
                "browser_notify": browser_notify,
            }
            try:
                from app.services.ws_manager import ws_manager

                if username:
                    await ws_manager.broadcast_to_user(
                        username, {"type": "notification", "data": payload}
                    )
                else:
                    await ws_manager.broadcast(
                        {"type": "notification", "data": payload}
                    )
            except Exception as e:
                logger.warning(f"通知 WebSocket 推送失败 type={type}: {e}")

        return notification

    # ---- 查询 ----

    async def get_unread_count(self, db: AsyncSession) -> int:
        """未读通知数量"""
        result = await db.execute(
            select(func.count(Notification.id)).where(
                Notification.is_read == 0, Notification.is_delete == 0
            )
        )
        return int(result.scalar() or 0)

    def _apply_filters(
        self,
        query: Optional[select],
        count_query: Optional[select],
        condition: Optional[NotificationCondition],
    ):
        """重写过滤：类型/已读精确、标题模糊"""
        if not condition:
            return query, count_query

        if condition.type:
            query = query.where(Notification.type == condition.type)
            count_query = count_query.where(Notification.type == condition.type)
        if condition.is_read is not None:
            query = query.where(Notification.is_read == condition.is_read)
            count_query = count_query.where(Notification.is_read == condition.is_read)
        if condition.title:
            query, count_query = self._apply_like_filter(
                query, count_query, "title", condition.title
            )
        return query, count_query

    # ---- 已读操作 ----

    async def mark_read(self, db: AsyncSession, notification_id: int) -> bool:
        """标记单条已读。返回通知是否存在"""
        from datetime import datetime

        notification = await self.get_by_id(db, notification_id, raise_not_found=False)
        if notification is None:
            return False
        if not notification.is_read:
            notification.is_read = 1
            notification.read_time = datetime.now()
            await db.commit()
        return True

    async def mark_all_read(self, db: AsyncSession) -> int:
        """全部标记已读。返回影响条数"""
        from datetime import datetime

        result = await db.execute(
            select(Notification).where(
                Notification.is_read == 0, Notification.is_delete == 0
            )
        )
        items = result.scalars().all()
        now = datetime.now()
        for item in items:
            item.is_read = 1
            item.read_time = now
        if items:
            await db.commit()
        return len(items)

    async def clear_read(self, db: AsyncSession) -> int:
        """清空所有已读通知（软删除）。返回清理条数"""
        result = await db.execute(
            select(Notification).where(
                Notification.is_read == 1, Notification.is_delete == 0
            )
        )
        items = result.scalars().all()
        for item in items:
            item.is_delete = 1
        if items:
            await db.commit()
        return len(items)


notification_service = NotificationService()

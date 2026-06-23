"""
日程管理 API 路由

提供日程 CRUD 和完成操作，创建时自动填充创建者用户名，提醒调度同步。
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.base_api import BaseApi, RouteConfig
from app.config.database import get_db
from app.models.agenda import Agenda, AgendaRecurrence, AgendaStatus
from app.schemas.agenda_schema import (
    AgendaBase,
    AgendaCondition,
    AgendaCreate,
    AgendaUpdate,
    CalendarEventsRequest,
)
from app.schemas.base_schema import ApiResponse
from app.services.agenda_service import agenda_service
from app.services.global_config_service import global_config_service


class AgendaApi(
    BaseApi[
        Agenda,
        AgendaBase,
        AgendaCondition,
        AgendaCreate,
        AgendaUpdate,
    ]
):
    """日程管理 API"""

    def __init__(self):
        super().__init__(
            service=agenda_service,
            router_prefix="/api/agenda",
            router_tags=["日程管理"],
            route_config=RouteConfig(
                enable_page=True,
                enable_get=True,
                enable_create=True,
                enable_update=True,
                enable_delete=True,
                enable_batch_delete=True,
            ),
        )
        self._register_custom_routes()

    async def create(self, db: AsyncSession, data: AgendaCreate) -> Agenda:
        """创建日程 - 填充创建者用户名，同步提醒调度"""
        username = await global_config_service.get_username(db)
        if username:
            data.creator_name = username
        agenda = await self.service.create(db, data)
        self._sync_reminder(agenda)
        if agenda.remind_at:
            await db.commit()
            await db.refresh(agenda)
        return agenda

    async def update(self, db: AsyncSession, data: AgendaUpdate) -> Agenda | None:
        """更新日程 - 同步提醒调度"""
        agenda = await self.service.update(db, data)
        if agenda:
            self._sync_reminder(agenda)
            await db.commit()
            await db.refresh(agenda)
        return agenda

    async def delete(self, db: AsyncSession, id: int) -> None:
        """删除日程 - 移除提醒调度"""
        from app.services.scheduler_service import scheduler_service

        scheduler_service.remove_agenda_reminder(id)
        await self.service.delete(db, id)

    async def batch_delete(self, db: AsyncSession, ids: list[int]) -> None:
        """批量删除 - 移除所有提醒调度"""
        from app.services.scheduler_service import scheduler_service

        for aid in ids:
            scheduler_service.remove_agenda_reminder(aid)
        await self.service.bulk_delete(db, ids)

    @staticmethod
    def _sync_reminder(agenda: Agenda) -> None:
        """同步日程提醒到调度器"""
        from app.services.scheduler_service import scheduler_service

        scheduler_service.sync_agenda_reminder(agenda)

    def _register_custom_routes(self):
        """注册自定义路由"""

        @self.router.post(
            "/calendar-events", response_model=ApiResponse[list[AgendaBase]]
        )
        async def get_calendar_events(
            body: CalendarEventsRequest,
            db: AsyncSession = Depends(get_db),
        ):
            """日历模式：按日期范围查询日程（不分页）"""
            items = await agenda_service.get_by_date_range(
                db, body.start_date, body.end_date, body.status
            )
            views = AgendaBase.model_to_view_batch(items)
            return ApiResponse.success(data=views, msg="查询成功")

        @self.router.post("/complete/{agenda_id}", response_model=ApiResponse)
        async def complete_agenda(agenda_id: int, db: AsyncSession = Depends(get_db)):
            """标记日程为已完成，重复日程生成下一实例"""
            agenda: Optional[Agenda] = await agenda_service.get_by_id(db, agenda_id)
            if agenda is None:
                return ApiResponse.error(msg=f"日程记录[{agenda_id}]不存在")
            agenda.status = AgendaStatus.COMPLETED.value
            agenda.completed_at = datetime.now()
            # 完成后移除提醒调度
            from app.services.scheduler_service import scheduler_service

            scheduler_service.remove_agenda_reminder(agenda_id)

            # 重复日程：原子锁获取后生成下一实例
            if agenda.recurrence != AgendaRecurrence.NONE.value:
                locked = await agenda_service.mark_recurrence_generated(db, agenda_id)
                if locked:
                    next_agenda = await agenda_service.create_next_recurrence(
                        db, agenda
                    )
                    if next_agenda:
                        scheduler_service.sync_agenda_reminder(next_agenda)

            await db.commit()
            await db.refresh(agenda)
            return ApiResponse.success(
                data=AgendaBase.model_to_view(agenda), msg="已完成"
            )

        @self.router.post("/postpone/{agenda_id}", response_model=ApiResponse)
        async def postpone_agenda(agenda_id: int, db: AsyncSession = Depends(get_db)):
            """延后提醒 15 分钟"""
            agenda = await agenda_service.get_by_id(db, agenda_id)
            if agenda is None:
                return ApiResponse.error(msg=f"日程记录[{agenda_id}]不存在")
            new_remind = datetime.now() + timedelta(minutes=15)
            agenda.remind_at = new_remind
            agenda.is_reminded = 0
            # 重新注册提醒调度
            from app.services.scheduler_service import scheduler_service

            scheduler_service.sync_agenda_reminder(agenda)
            await db.commit()
            await db.refresh(agenda)
            return ApiResponse.success(
                data=AgendaBase.model_to_view(agenda), msg="已延后15分钟"
            )


agenda_api = AgendaApi()
router = agenda_api.router

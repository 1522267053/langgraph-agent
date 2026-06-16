"""
日程管理服务

管理日程的 CRUD、条件过滤和提醒查询。
"""

import calendar
import logging
from datetime import timedelta
from typing import Optional, Tuple

from sqlalchemy import Select, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agenda import Agenda, AgendaRecurrence, AgendaStatus
from app.schemas.agenda_schema import AgendaCondition, AgendaCreate, AgendaUpdate
from app.services.base_service import BaseService

logger = logging.getLogger(__name__)


class AgendaService(BaseService[Agenda, AgendaCreate, AgendaUpdate]):
    """日程管理服务"""

    def __init__(self):
        super().__init__(Agenda)

    def _apply_filters(
        self,
        query: Optional[Select],
        count_query: Optional[Select],
        condition: Optional[AgendaCondition],
    ) -> Tuple[Optional[Select], Optional[Select]]:
        """重写过滤：标题模糊、分类/状态/创建人精确、日期范围"""
        if not condition:
            return query, count_query

        # 标题模糊查询
        if getattr(condition, "title", None):
            query, count_query = self._apply_like_filter(
                query, count_query, "title", condition.title
            )

        # 分类精确匹配
        if getattr(condition, "category", None):
            query = query.where(Agenda.category == condition.category)
            count_query = count_query.where(Agenda.category == condition.category)

        # 状态精确匹配
        if getattr(condition, "status", None) is not None:
            query = query.where(Agenda.status == condition.status)
            count_query = count_query.where(Agenda.status == condition.status)

        # 创建人精确匹配
        if getattr(condition, "creator_name", None):
            query = query.where(Agenda.creator_name == condition.creator_name)
            count_query = count_query.where(
                Agenda.creator_name == condition.creator_name
            )

        # 开始时间范围（起）
        if getattr(condition, "start_date", None):
            query = query.where(Agenda.start_time >= condition.start_date)
            count_query = count_query.where(Agenda.start_time >= condition.start_date)

        # 开始时间范围（止，包含当天）
        if getattr(condition, "end_date", None):
            end_dt = f"{condition.end_date} 23:59:59"
            query = query.where(Agenda.start_time <= end_dt)
            count_query = count_query.where(Agenda.start_time <= end_dt)

        return query, count_query

    async def get_unreminded_agendas(self, db: AsyncSession) -> list[Agenda]:
        """获取所有设置了提醒但未推送的日程（不论是否过期）"""
        stmt = select(Agenda).where(
            and_(
                Agenda.remind_at.isnot(None),
                Agenda.is_reminded == 0,
            )
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_date_range(
        self, db: AsyncSession, start_date: str, end_date: str
    ) -> list[Agenda]:
        """按日期范围查询日程（日历模式用，不分页）"""
        end_dt = f"{end_date} 23:59:59"
        stmt = (
            select(Agenda)
            .where(Agenda.start_time >= start_date)
            .where(Agenda.start_time <= end_dt)
            .order_by(Agenda.start_time)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def mark_reminded(self, db: AsyncSession, agenda_id: int) -> None:
        """标记日程为已推送提醒"""
        stmt = select(Agenda).where(Agenda.id == agenda_id)
        result = await db.execute(stmt)
        agenda = result.scalar_one_or_none()
        if agenda:
            agenda.is_reminded = 1
            await db.commit()

    async def create_next_recurrence(
        self, db: AsyncSession, agenda: Agenda
    ) -> Optional[Agenda]:
        """根据重复规则生成下一条日程实例"""
        if not agenda.start_time or agenda.recurrence == AgendaRecurrence.NONE.value:
            return None

        # 计算 remind_at 相对 start_time 的偏移量
        offset = 0
        if agenda.remind_at and agenda.start_time:
            offset = int((agenda.remind_at - agenda.start_time).total_seconds())

        # 计算下一组时间
        if agenda.recurrence == AgendaRecurrence.DAILY.value:
            next_start = agenda.start_time + timedelta(days=1)
        elif agenda.recurrence == AgendaRecurrence.WEEKLY.value:
            next_start = agenda.start_time + timedelta(days=7)
        elif agenda.recurrence == AgendaRecurrence.MONTHLY.value:
            month = agenda.start_time.month + 1
            year = agenda.start_time.year + (month - 1) // 12
            month = (month - 1) % 12 + 1
            max_day = calendar.monthrange(year, month)[1]
            day = min(agenda.start_time.day, max_day)
            next_start = agenda.start_time.replace(year=year, month=month, day=day)
        else:
            return None

        # 计算结束时间
        next_end = None
        if agenda.end_time and agenda.start_time:
            duration = (agenda.end_time - agenda.start_time).total_seconds()
            next_end = next_start + timedelta(seconds=duration)

        # 计算提醒时间（保留偏移量）
        next_remind = None
        if agenda.remind_at:
            next_remind = next_start + timedelta(seconds=offset)

        new_agenda = Agenda(
            title=agenda.title,
            description=agenda.description,
            start_time=next_start,
            end_time=next_end,
            category=agenda.category,
            priority=agenda.priority,
            location=agenda.location,
            recurrence=agenda.recurrence,
            status=AgendaStatus.PENDING.value,
            color=agenda.color,
            remind_at=next_remind,
            is_reminded=0,
            creator_name=agenda.creator_name,
        )
        db.add(new_agenda)
        await db.flush()
        return new_agenda


agenda_service = AgendaService()

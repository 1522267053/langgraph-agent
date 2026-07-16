"""
定时任务 API 路由
"""

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.base_api import BaseApi, RouteConfig
from app.config.database import get_db
from app.models.scheduled_task import ScheduledTask
from app.schemas.base_schema import (
    ApiResponse,
    PaginatedResponse,
    PaginationParams,
)
from app.schemas.scheduled_task_schema import (
    ScheduledTaskBase,
    ScheduledTaskCreate,
    ScheduledTaskUpdate,
    ScheduledTaskCondition,
    ScheduledTaskLogBase,
    ScheduledTaskLogCondition,
)
from app.services.scheduled_task_service import scheduled_task_service


class ScheduledTaskApi(
    BaseApi[
        ScheduledTask,
        ScheduledTaskBase,
        ScheduledTaskCondition,
        ScheduledTaskCreate,
        ScheduledTaskUpdate,
    ]
):
    """定时任务 API"""

    def __init__(self):
        super().__init__(
            service=scheduled_task_service,
            router_prefix="/api/scheduled-task",
            router_tags=["定时任务"],
            route_config=RouteConfig(
                enable_page=True,
                enable_get=True,
                enable_create=True,
                enable_update=True,
                enable_delete=True,
                enable_batch_delete=False,
            ),
        )
        self._register_custom_routes()

    async def _check_name_unique(
        self, db: AsyncSession, name: str, exclude_id: int | None = None
    ) -> None:
        """检查任务名称是否唯一"""
        stmt = select(ScheduledTask).where(
            ScheduledTask.name == name, ScheduledTask.is_delete == 0
        )
        if exclude_id is not None:
            stmt = stmt.where(ScheduledTask.id != exclude_id)
        result = await db.execute(stmt)
        if result.scalar_one_or_none() is not None:
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail=f"任务名称「{name}」已存在")

    @staticmethod
    async def _check_flow_target(
        db: AsyncSession, target_type: str, target_id: int
    ) -> None:
        """校验 Flow 目标流程不含人类帮助节点"""
        if target_type != "flow":
            return
        from app.utils.flow_utils import flow_contains_nodes

        if await flow_contains_nodes(db, target_id, {"human"}):
            from fastapi import HTTPException

            raise HTTPException(
                status_code=400,
                detail="目标流程包含人类帮助节点，不支持作为定时任务目标",
            )

    @staticmethod
    def _validate_schedule(data) -> None:
        """校验调度配置：cron 模式需 cron_expression，once 模式需 run_at"""
        from datetime import datetime

        from fastapi import HTTPException

        schedule_type = (data.schedule_type or "cron") if data else "cron"
        if schedule_type == "once":
            if not getattr(data, "run_at", None):
                raise HTTPException(
                    status_code=400,
                    detail="单次执行任务必须设置运行时间（run_at）",
                )
            if data.run_at.replace(tzinfo=None) < datetime.now():
                raise HTTPException(
                    status_code=400,
                    detail="运行时间不能早于当前时间",
                )
        else:
            if not (getattr(data, "cron_expression", None) or "").strip():
                raise HTTPException(
                    status_code=400,
                    detail="循环执行任务必须设置 Cron 表达式",
                )

    async def create(
        self, db: AsyncSession, data: ScheduledTaskCreate
    ) -> ScheduledTask:
        """创建 - 校验名称唯一性 + 调度配置 + 流程目标合法性，启用时注册调度"""
        await self._check_name_unique(db, data.name)
        self._validate_schedule(data)
        await self._check_flow_target(db, data.target_type, data.target_id)
        task = await self.service.create(db, data)
        if task.is_enabled == 1:
            self._sync_scheduler(task)
            await db.commit()
            await db.refresh(task)
        return task

    async def update(
        self, db: AsyncSession, data: ScheduledTaskUpdate
    ) -> ScheduledTask | None:
        """更新 - 校验名称唯一性 + 调度配置 + 流程目标合法性，同步调度"""
        if data.name is not None:
            await self._check_name_unique(db, data.name, exclude_id=data.id)
        if data.schedule_type is not None:
            self._validate_schedule(data)
        if data.target_type is not None and data.target_id is not None:
            await self._check_flow_target(db, data.target_type, data.target_id)
        task = await self.service.update(db, data)
        if task:
            self._sync_scheduler(task)
            await db.commit()
            await db.refresh(task)
        return task

    @staticmethod
    def _sync_scheduler(task: ScheduledTask) -> None:
        """同步任务到调度器并更新下次执行时间"""
        from app.services.scheduler_service import scheduler_service

        if task.is_enabled == 1:
            scheduler_service.add_job(task)
            next_run = scheduler_service.get_next_run_time(task.id)
            task.next_run_time = next_run
        else:
            scheduler_service.remove_job(task.id)
            task.next_run_time = None

    async def delete(self, db: AsyncSession, id: int) -> None:
        """删除 - 从调度器移除"""
        from app.services.scheduler_service import scheduler_service

        scheduler_service.remove_job(id)
        await self.service.delete(db, id)

    def _register_custom_routes(self):
        """注册自定义路由"""

        @self.router.post("/toggle/{task_id}", response_model=ApiResponse)
        async def toggle_enabled(task_id: int, db: AsyncSession = Depends(get_db)):
            """切换启用/禁用（启用时校验目标流程）"""
            task = await scheduled_task_service.get_by_id(db, task_id)
            if not task:
                return ApiResponse.error(msg="任务不存在")
            is_enabling = task.is_enabled != 1
            if is_enabling:
                await self._check_flow_target(db, task.target_type, task.target_id)
            task = await scheduled_task_service.toggle_enabled(db, task_id)
            return ApiResponse.success(
                data=ScheduledTaskBase.model_to_view(task), msg="操作成功"
            )

        @self.router.post("/trigger/{task_id}", response_model=ApiResponse)
        async def manual_trigger(task_id: int, db: AsyncSession = Depends(get_db)):
            """手动触发执行（校验目标流程）"""
            task = await scheduled_task_service.get_by_id(db, task_id)
            if not task:
                return ApiResponse.error(msg="任务不存在")
            await self._check_flow_target(db, task.target_type, task.target_id)
            log = await scheduled_task_service.manual_trigger(db, task_id)
            return ApiResponse.success(
                data=ScheduledTaskLogBase.model_to_view(log), msg="已触发执行"
            )

        @self.router.post(
            "/logs/page",
            response_model=ApiResponse[PaginatedResponse[ScheduledTaskLogBase]],
        )
        async def get_task_logs(
            params: PaginationParams[ScheduledTaskLogCondition],
            db: AsyncSession = Depends(get_db),
        ):
            """分页查询执行日志"""
            task_id = params.condition.task_id if params.condition else None
            if not task_id:
                return ApiResponse.success(
                    data=PaginatedResponse.create(
                        items=[], total=0, page=params.page, page_size=params.page_size
                    )
                )

            logs, total = await scheduled_task_service.get_task_logs(
                db, task_id, page=params.page, page_size=params.page_size
            )
            views = ScheduledTaskLogBase.model_to_view_batch(logs)
            return ApiResponse.success(
                data=PaginatedResponse.create(
                    items=views,
                    total=total,
                    page=params.page,
                    page_size=params.page_size,
                ),
                msg="查询成功",
            )


scheduled_task_api = ScheduledTaskApi()
router = scheduled_task_api.router

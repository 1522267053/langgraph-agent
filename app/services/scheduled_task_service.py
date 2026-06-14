"""
定时任务服务

管理定时任务的 CRUD、调度同步、手动触发和执行日志。
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scheduled_task import (
    ScheduledTask,
    ScheduledTaskLog,
    ScheduledTaskTargetType,
    TriggerType,
    LogStatus,
)
from app.services.base_service import BaseService

logger = logging.getLogger(__name__)


class ScheduledTaskService(BaseService[ScheduledTask, ScheduledTask, ScheduledTask]):
    """定时任务服务"""

    def __init__(self):
        super().__init__(ScheduledTask)
        self._running_tasks: set[int] = set()

    async def toggle_enabled(self, db: AsyncSession, task_id: int) -> ScheduledTask:
        """切换定时任务的启用/禁用状态"""
        task = await self.get_by_id(db, task_id)
        task.is_enabled = 0 if task.is_enabled == 1 else 1
        self._set_modifier_fields(task)

        from app.services.scheduler_service import scheduler_service

        if task.is_enabled == 1:
            scheduler_service.add_job(task)
            next_run = scheduler_service.get_next_run_time(task.id)
            task.next_run_time = next_run
        else:
            scheduler_service.remove_job(task.id)
            task.next_run_time = None

        await db.commit()
        await db.refresh(task)

        return task

    async def manual_trigger(self, db: AsyncSession, task_id: int) -> ScheduledTaskLog:
        """手动触发定时任务执行（立即返回，后台执行）"""
        import asyncio

        task = await self.get_by_id(db, task_id)

        log = ScheduledTaskLog(
            task_id=task.id,
            trigger_type=TriggerType.MANUAL.value,
            status=LogStatus.RUNNING.value,
            start_time=datetime.now(),
            input_snapshot=task.input_data,
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)

        asyncio.create_task(
            self._execute_task_by_log_id(
                task_id, trigger_type=TriggerType.MANUAL, log_id=log.id
            )
        )

        return log

    async def _execute_task_by_log_id(
        self, task_id: int, trigger_type: TriggerType, log_id: int
    ) -> None:
        """通过 log_id 在独立会话中加载 task 和 log 并执行"""
        from app.config.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            task = await db.get(ScheduledTask, task_id)
            if not task:
                logger.error(f"定时任务不存在: task_id={task_id}")
                return
            log = await db.get(ScheduledTaskLog, log_id)
            if not log:
                logger.error(f"定时任务日志不存在: log_id={log_id}")
                return
            await self._execute_task(task, trigger_type, db, log=log)

    async def _execute_task(
        self,
        task: ScheduledTask,
        trigger_type: TriggerType = TriggerType.CRON,
        db: AsyncSession | None = None,
        log: ScheduledTaskLog | None = None,
    ) -> ScheduledTaskLog:
        """
        执行定时任务

        根据目标类型调用 Flow 或 Agent 执行服务，并记录执行日志。
        db 为 None 时自动创建独立会话。
        """
        from app.config.database import AsyncSessionLocal

        self._running_tasks.add(task.id)

        own_session = db is None
        if own_session:
            db = await AsyncSessionLocal().__aenter__()

        try:
            if log is None:
                log = ScheduledTaskLog(
                    task_id=task.id,
                    trigger_type=trigger_type.value,
                    status=LogStatus.RUNNING.value,
                    start_time=datetime.now(),
                    input_snapshot=task.input_data,
                )
                db.add(log)
                await db.commit()
                await db.refresh(log)

            try:
                target_type = task.target_type
                target_id = task.target_id
                input_data = task.input_data or {}

                if target_type == ScheduledTaskTargetType.AGENT.value:
                    await self._execute_agent_task(
                        db, log, target_id, input_data, task.name
                    )
                else:
                    await self._execute_flow_task(db, log, target_id, input_data)

                log.status = LogStatus.SUCCESS.value
                task.last_run_status = LogStatus.SUCCESS.value
            except Exception as e:
                log.status = LogStatus.FAILED.value
                log.error_message = str(e)
                task.last_run_status = LogStatus.FAILED.value
                logger.error(f"定时任务[{task.name}]执行失败: {e}", exc_info=True)
            finally:
                log.end_time = datetime.now()
                if log.start_time and log.end_time:
                    log.duration_ms = int(
                        (log.end_time - log.start_time).total_seconds() * 1000
                    )
                task.last_run_time = log.end_time
                from app.services.scheduler_service import scheduler_service

                next_run = scheduler_service.get_next_run_time(task.id)
                task.next_run_time = next_run
                await db.commit()

            return log
        finally:
            self._running_tasks.discard(task.id)
            if own_session:
                await db.__aexit__(None, None, None)

    def is_task_running(self, task_id: int) -> bool:
        """检查任务是否正在执行中"""
        return task_id in self._running_tasks

    async def _execute_flow_task(
        self,
        db: AsyncSession,
        log: ScheduledTaskLog,
        flow_id: int,
        input_data: dict,
    ) -> None:
        """执行 Flow 目标任务"""
        from app.services.flow_executor_service import flow_executor_service

        try:
            async for event in flow_executor_service.execute_stream(
                flow_id, input_data=input_data
            ):
                if event.get("type") == "flow_start":
                    event_data = event.get("data", {})
                    if event_data and event_data.get("execution_id"):
                        log.execution_id = event_data["execution_id"]
                        await db.commit()
                if event.get("type") == "waiting_human":
                    execution_id = log.execution_id
                    if execution_id:
                        await self._cancel_flow_execution(execution_id)
                    await self._disable_task_on_human_node(db, log)
                    raise RuntimeError(
                        f"流程[{flow_id}]包含人类帮助节点，已自动取消执行并禁用定时任务"
                    )
        except RuntimeError:
            raise
        except Exception:
            pass

    @staticmethod
    async def _cancel_flow_execution(execution_id: int) -> None:
        """将流程执行记录状态更新为 CANCELLED"""
        from app.config.database import AsyncSessionLocal
        from app.models.flow_execution import ExecutionStatus, FlowExecution

        try:
            async with AsyncSessionLocal() as cancel_db:
                stmt = select(FlowExecution).where(FlowExecution.id == execution_id)
                result = await cancel_db.execute(stmt)
                execution = result.scalar_one_or_none()
                if (
                    execution
                    and execution.status == ExecutionStatus.WAITING_HUMAN.value
                ):
                    execution.status = ExecutionStatus.CANCELLED.value
                    await cancel_db.commit()
                    logger.info(f"已取消流程执行记录: execution_id={execution_id}")
        except Exception as e:
            logger.error(
                f"取消流程执行记录失败: execution_id={execution_id}, error={e}"
            )

    @staticmethod
    async def _disable_task_on_human_node(
        db: AsyncSession, log: ScheduledTaskLog
    ) -> None:
        """遇到人类帮助节点时禁用定时任务"""
        from app.services.scheduler_service import scheduler_service

        task_id = log.task_id
        task = await db.get(ScheduledTask, task_id)
        if task and task.is_enabled == 1:
            task.is_enabled = 0
            task.next_run_time = None
            scheduler_service.remove_job(task.id)
            logger.warning(
                f"定时任务「{task.name}」(id={task_id})因流程含人类帮助节点被自动禁用"
            )

    async def _execute_agent_task(
        self,
        db: AsyncSession,
        log: ScheduledTaskLog,
        agent_flow_id: int,
        input_data: dict,
        task_name: str = "",
    ) -> None:
        """执行 Agent 目标任务（创建新会话并发送消息）"""
        from app.services.agent_executor_service import agent_executor_service

        session = await agent_executor_service.create_session(db, agent_flow_id)
        session.title = f"[定时任务] {task_name}" if task_name else "[定时任务]"
        await db.commit()
        log.session_id = session.id
        log.agent_id = agent_flow_id

        message = ""
        if isinstance(input_data, dict):
            message = input_data.get("message", "")
        else:
            message = str(input_data)
        now = datetime.now(timezone.utc)
        msg_prefix = f"[定时任务，触发时间: {now.strftime('%Y-%m-%d %H:%M:%S')} (UTC)]"
        if not message:
            message = msg_prefix
        else:
            message = msg_prefix + "\n" + message

        params = None
        if isinstance(input_data, dict):
            params = {k: v for k, v in input_data.items() if k != "message"}
            if not params:
                params = None

        try:
            async for event in agent_executor_service.chat_stream(
                session.id, message, params
            ):
                pass
        except Exception:
            pass

    async def get_task_logs(
        self,
        db: AsyncSession,
        task_id: int,
        page: int = 1,
        page_size: int = 10,
    ) -> tuple[list[ScheduledTaskLog], int]:
        """
        获取定时任务的执行日志（分页）

        Args:
            db: 数据库会话
            task_id: 定时任务ID
            page: 页码
            page_size: 每页数量

        Returns:
            (日志列表, 总数)
        """
        count_query = (
            select(func.count())
            .select_from(ScheduledTaskLog)
            .where(ScheduledTaskLog.task_id == task_id, ScheduledTaskLog.is_delete == 0)
        )
        total = (await db.execute(count_query)).scalar() or 0

        offset = (page - 1) * page_size
        query = (
            select(ScheduledTaskLog)
            .where(ScheduledTaskLog.task_id == task_id, ScheduledTaskLog.is_delete == 0)
            .order_by(ScheduledTaskLog.id.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await db.execute(query)
        logs = list(result.scalars().all())

        return logs, total

    async def get_enabled_tasks(self, db: AsyncSession) -> list[ScheduledTask]:
        """获取所有已启用的定时任务"""
        query = select(ScheduledTask).where(
            ScheduledTask.is_enabled == 1, ScheduledTask.is_delete == 0
        )
        result = await db.execute(query)
        return list(result.scalars().all())


scheduled_task_service = ScheduledTaskService()

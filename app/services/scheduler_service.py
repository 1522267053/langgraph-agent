"""
APScheduler 定时任务管理

管理后台定时任务，包括：
- 文档异步处理（解析 → 分段 → 向量化）
- 流程/Agent 定时触发执行
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.config.settings import settings

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler as SchedulerType

logger = logging.getLogger(__name__)


class SchedulerService:
    """
    定时任务管理服务

    使用 APScheduler AsyncIOScheduler 在 FastAPI 进程内运行定时任务。
    """

    def __init__(self):
        self._scheduler: SchedulerType | None = None

    async def start(self) -> None:
        """启动定时任务调度器"""
        if self._scheduler is not None:
            logger.warning("调度器已启动，跳过重复启动")
            return

        self._scheduler = AsyncIOScheduler()
        logging.getLogger("apscheduler").setLevel(logging.WARNING)
        self._scheduler.add_job(
            self._process_pending_documents,
            "interval",
            seconds=settings.doc_process_interval,
            id="process_pending_documents",
            name="文档异步处理",
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=settings.doc_process_interval,
        )
        self._scheduler.add_job(
            self._cleanup_temp_files,
            CronTrigger(minute=0, hour="*"),
            id="cleanup_temp_files",
            name="清理7天前的临时文件",
            replace_existing=True,
            max_instances=1,
        )
        self._scheduler.add_job(
            self._scan_expired_recurring_agendas,
            CronTrigger(minute="*/30"),
            id="scan_expired_recurring_agendas",
            name="扫描过期重复日程",
            replace_existing=True,
            max_instances=1,
        )
        self._scheduler.add_job(
            self._check_pending_reminders,
            "interval",
            seconds=settings.reminder_check_interval,
            id="check_pending_reminders",
            name="日程提醒轮询",
            replace_existing=True,
            max_instances=1,
        )
        self._scheduler.add_job(
            self._sync_ai_models,
            CronTrigger(day_of_week="6", hour=3, minute=0),
            id="sync_ai_models",
            name="同步AI模型与供应商数据",
            replace_existing=True,
            max_instances=1,
        )
        self._scheduler.start()
        logger.info(f"调度器已启动，文档处理任务间隔: {settings.doc_process_interval}s")

        await self._load_scheduled_tasks()
        await self._load_pending_agenda_reminders()

    async def shutdown(self) -> None:
        """关闭调度器"""
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("调度器已关闭")

    async def _load_scheduled_tasks(self) -> None:
        """启动时加载所有已启用的定时任务"""
        from app.config.database import AsyncSessionLocal
        from app.services.scheduled_task_service import scheduled_task_service

        try:
            async with AsyncSessionLocal() as db:
                tasks = await scheduled_task_service.get_enabled_tasks(db)
                for task in tasks:
                    self.add_job(task)
                    next_run = self.get_next_run_time(task.id)
                    if next_run:
                        task.next_run_time = next_run
                await db.commit()
                logger.info(f"加载了 {len(tasks)} 个定时任务")
        except Exception as e:
            logger.error(f"加载定时任务失败: {e}", exc_info=True)

    def add_job(self, task) -> None:
        """
        向 APScheduler 注册定时任务

        Args:
            task: ScheduledTask 模型实例
        """
        if self._scheduler is None:
            return

        job_id = f"scheduled_task_{task.id}"
        schedule_type = getattr(task, "schedule_type", "cron") or "cron"
        job_name = f"定时任务: {getattr(task, 'name', task.id)}"

        try:
            if schedule_type == "once":
                run_at = getattr(task, "run_at", None)
                if not run_at:
                    logger.warning(f"定时任务[{task.id}] once 模式未设置 run_at，跳过")
                    return
                self._scheduler.add_job(
                    self._run_scheduled_task,
                    DateTrigger(run_date=run_at),
                    id=job_id,
                    name=job_name,
                    replace_existing=True,
                    max_instances=1,
                    misfire_grace_time=24 * 3600,
                    args=[task.id],
                )
                logger.info(f"注册单次任务[{job_id}]: 运行时间 {run_at}")
                return

            # cron 模式
            cron_expr = getattr(task, "cron_expression", "") or ""
            if not cron_expr:
                return
            parts = cron_expr.strip().split()
            # Quartz 的 ? 表示"不指定"，等价于标准 cron 的 *
            parts = ["*" if p == "?" else p for p in parts]
            if len(parts) != 5:
                logger.warning(f"定时任务[{task.id}] cron 表达式格式错误: {cron_expr}")
                return

            self._scheduler.add_job(
                self._run_scheduled_task,
                CronTrigger(
                    minute=parts[0],
                    hour=parts[1],
                    day=parts[2],
                    month=parts[3],
                    day_of_week=parts[4],
                ),
                id=job_id,
                name=job_name,
                replace_existing=True,
                max_instances=1,
                args=[task.id],
            )
            logger.info(f"注册定时任务[{job_id}]: {cron_expr}")
        except Exception as e:
            logger.error(f"注册定时任务[{task.id}]失败: {e}", exc_info=True)

    def remove_job(self, task_id: int) -> None:
        """
        从 APScheduler 移除定时任务

        Args:
            task_id: 定时任务ID
        """
        if self._scheduler is None:
            return

        job_id = f"scheduled_task_{task_id}"
        try:
            if self._scheduler.get_job(job_id):
                self._scheduler.remove_job(job_id)
                logger.info(f"移除定时任务[{job_id}]")
        except Exception as e:
            logger.error(f"移除定时任务[{job_id}]失败: {e}")

    def get_next_run_time(self, task_id: int) -> datetime | None:
        """获取定时任务的下次执行时间"""
        if self._scheduler is None:
            return None

        job_id = f"scheduled_task_{task_id}"
        job = self._scheduler.get_job(job_id)
        if job and job.next_run_time:
            return job.next_run_time
        return None

    async def _run_scheduled_task(self, task_id: int) -> None:
        """
        定时任务执行回调

        由 APScheduler 调用，执行对应的定时任务。
        上一次执行未完成时跳过本次。
        """
        from app.config.database import AsyncSessionLocal
        from app.services.scheduled_task_service import scheduled_task_service

        try:
            async with AsyncSessionLocal() as db:
                task = await scheduled_task_service.get_by_id(db, task_id)
                if not task or task.is_enabled != 1:
                    return

                if scheduled_task_service.is_task_running(task_id):
                    logger.info(f"定时任务[{task.name}]上一次执行未完成，跳过本次")
                    return

                await scheduled_task_service._execute_task(task)
                logger.info(f"定时任务[{task.name}]执行完成")

                # 单次任务执行完成后自动禁用（DateTrigger 已自动移除 job）
                if getattr(task, "schedule_type", "cron") == "once":
                    task.is_enabled = 0
                    task.next_run_time = None
                    await db.commit()
                    logger.info(f"单次任务[{task.name}]执行完成，已自动禁用")
        except Exception as e:
            logger.error(f"定时任务[{task_id}]执行异常: {e}", exc_info=True)

    async def _cleanup_temp_files(self) -> None:
        """清理 temp 目录中超过 7 天的文件"""
        import time

        from app.config.build_utils import get_temp_dir

        temp_dir = get_temp_dir()
        if not temp_dir.exists():
            return

        now = time.time()
        max_age = 7 * 24 * 3600  # 7天
        deleted = 0

        for entry in temp_dir.iterdir():
            if not entry.is_file():
                continue
            try:
                if now - entry.stat().st_mtime > max_age:
                    entry.unlink()
                    deleted += 1
            except Exception as e:
                logger.warning(f"清理临时文件失败: {entry.name}: {e}")

        if deleted > 0:
            logger.info(f"已清理 {deleted} 个过期临时文件")

    # ---- 日程提醒轮询 ----

    async def _check_pending_reminders(self) -> None:
        """轮询检查并推送已到提醒时间的日程"""
        from app.config.database import AsyncSessionLocal
        from app.services.agenda_service import agenda_service

        try:
            async with AsyncSessionLocal() as db:
                agendas = await agenda_service.get_unreminded_agendas(db)

            now = datetime.now()
            due = [
                a
                for a in agendas
                if a.remind_at <= now
                and not (a.recurrence == "weekday" and now.weekday() >= 5)
            ]
            for agenda in due:
                await self._run_agenda_reminder(agenda.id)

            if due:
                logger.info(f"日程提醒轮询: {len(due)} 个已到提醒时间")
        except Exception as e:
            logger.error(f"日程提醒轮询异常: {e}", exc_info=True)

    # ---- 日程提醒 ----

    def sync_agenda_reminder(self, agenda) -> None:
        """
        同步日程提醒到调度器（由轮询任务统一处理，此处仅做日志记录）

        Args:
            agenda: Agenda 模型实例
        """
        if (
            agenda.remind_at
            and getattr(agenda, "status", 0) != 2
            and getattr(agenda, "is_delete", 0) != 1
        ):
            logger.debug(
                f"日程提醒已注册[{agenda.id}]: {agenda.remind_at}（由轮询任务处理）"
            )

    def remove_agenda_reminder(self, agenda_id: int) -> None:
        """移除日程提醒（轮询模式下无需操作，保留方法避免调用方报错）"""
        pass

    async def _run_agenda_reminder(self, agenda_id: int) -> None:
        """日程提醒回调：定向推送给创建者，重复日程自动生成下一实例"""
        from app.config.database import AsyncSessionLocal
        from app.models.agenda import AgendaRecurrence, AgendaStatus
        from app.services.agenda_service import agenda_service
        from app.services.ws_manager import ws_manager

        try:
            async with AsyncSessionLocal() as db:
                agenda = await agenda_service.get_by_id(db, agenda_id)
                if not agenda or agenda.status == AgendaStatus.COMPLETED.value:
                    return

                # 原子标记已推送，防止重复触发
                marked = await agenda_service.mark_reminded(db, agenda_id)
                if not marked:
                    return

                # 格式化开始时间
                start_str = (
                    agenda.start_time.strftime("%Y-%m-%d %H:%M")
                    if agenda.start_time
                    else None
                )

                # 定向推送给创建者
                username = agenda.creator_name or "default"
                await ws_manager.notify_agenda_reminder(
                    username=username,
                    agenda_id=agenda.id,
                    title=agenda.title,
                    description=agenda.description,
                    start_time=start_str,
                    location=agenda.location,
                )

                # 重复日程：原子锁获取后生成下一实例并注册提醒
                if agenda.recurrence != AgendaRecurrence.NONE.value:
                    locked = await agenda_service.mark_recurrence_generated(
                        db, agenda_id
                    )
                    if locked:
                        next_agenda = await agenda_service.create_next_recurrence(
                            db, agenda
                        )
                        if next_agenda:
                            self.sync_agenda_reminder(next_agenda)
                            await db.commit()
                            logger.info(
                                f"日程[{agenda_id}]重复生成下一实例[{next_agenda.id}]: "
                                f"{next_agenda.start_time}"
                            )
                    else:
                        await db.commit()

                logger.info(f"日程提醒[{agenda_id}]已推送给 {username}")
        except Exception as e:
            logger.error(f"日程提醒[{agenda_id}]推送失败: {e}", exc_info=True)

    async def _scan_expired_recurring_agendas(self) -> None:
        """定时扫描今天过期的重复日程，生成下一实例（不链式生成，明天由明天扫描处理）"""
        from app.config.database import AsyncSessionLocal
        from app.services.agenda_service import agenda_service

        try:
            async with AsyncSessionLocal() as db:
                agendas = await agenda_service.get_expired_recurring_agendas(db)
                if not agendas:
                    return

                generated = 0
                for agenda in agendas:
                    locked = await agenda_service.mark_recurrence_generated(
                        db, agenda.id
                    )
                    if not locked:
                        continue

                    # 只生成一个下一实例，不做链式生成
                    next_agenda = await agenda_service.create_next_recurrence(
                        db, agenda
                    )
                    if next_agenda:
                        self.sync_agenda_reminder(next_agenda)
                        generated += 1

                if generated > 0:
                    await db.commit()
                    logger.info(
                        f"扫描过期重复日程完成，共处理 {len(agendas)} 条，"
                        f"生成 {generated} 个新实例"
                    )
        except Exception as e:
            logger.error(f"扫描过期重复日程失败: {e}", exc_info=True)

    async def _load_pending_agenda_reminders(self) -> None:
        """启动时加载所有未推送的日程提醒，过期的立即触发推送（未来的由轮询处理）"""
        from app.config.database import AsyncSessionLocal
        from app.services.agenda_service import agenda_service

        try:
            async with AsyncSessionLocal() as db:
                agendas = await agenda_service.get_unreminded_agendas(db)

            now = datetime.now()
            expired = [
                a
                for a in agendas
                if a.remind_at <= now
                and not (a.recurrence == "weekday" and now.weekday() >= 5)
            ]

            # 过期的立即触发推送
            for agenda in expired:
                await self._run_agenda_reminder(agenda.id)

            if agendas:
                logger.info(
                    f"加载日程提醒: {len(expired)} 个立即补推, "
                    f"{len(agendas) - len(expired)} 个待轮询"
                )
        except Exception as e:
            logger.error(f"加载日程提醒失败: {e}", exc_info=True)

    async def _process_pending_documents(self) -> None:
        """
        处理所有待处理的文档

        查询 processing_status=0(待处理) 或 processing_status=4(待向量化) 的文档：
        - status=0 → 解析 → 分段 → 向量化
        - status=4 → 仅向量化（文档已解析分段）
        每个文档使用独立数据库会话，避免单个失败影响后续处理。
        """
        from app.config.database import AsyncSessionLocal
        from app.services.knowledge_document_service import knowledge_document_service

        try:
            async with AsyncSessionLocal() as db:
                documents = await knowledge_document_service.get_pending_documents(
                    db, limit=settings.doc_process_batch_size
                )

                if not documents:
                    return

                doc_ids = [doc.id for doc in documents]
                logger.info(f"发现 {len(doc_ids)} 个待处理文档")

            for doc_id in doc_ids:
                try:
                    async with AsyncSessionLocal() as doc_db:
                        result = await knowledge_document_service.process_document(
                            doc_db, doc_id
                        )
                        status = result.get("status", "unknown")
                        if status == "success":
                            logger.info(
                                f"文档 {doc_id} 处理完成，"
                                f"分段 {result.get('segment_count', 0)}，"
                                f"向量化 {result.get('vectorized_segments', 0)}"
                            )
                        else:
                            error = result.get("error", "未知错误")
                            logger.warning(f"文档 {doc_id} 处理失败: {error}")
                except Exception as e:
                    logger.error(f"文档 {doc_id} 处理异常: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"定时任务执行异常: {e}", exc_info=True)

    async def _sync_ai_models(self) -> None:
        from app.services.ai_provider_service import ai_provider_service

        try:
            logger.info("开始定时同步 AI 模型与供应商数据")
            await ai_provider_service.sync_from_url()
        except Exception as e:
            logger.error(f"同步 AI 模型与供应商数据失败: {e}", exc_info=True)


scheduler_service = SchedulerService()

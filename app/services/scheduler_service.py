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
        self._scheduler.start()
        logger.info(f"调度器已启动，文档处理任务间隔: {settings.doc_process_interval}s")

        await self._load_scheduled_tasks()

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
        cron_expr = getattr(task, "cron_expression", "")
        if not cron_expr:
            return

        try:
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
                name=f"定时任务: {getattr(task, 'name', task.id)}",
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
        except Exception as e:
            logger.error(f"定时任务[{task_id}]执行异常: {e}", exc_info=True)

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


scheduler_service = SchedulerService()

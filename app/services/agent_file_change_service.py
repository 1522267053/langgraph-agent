"""
Agent 文件变更记录服务

记录 Agent 对话中 AI 工具对文件的修改（create/modify），支持：
- 写前备份原文件（modify）
- 回退消息时按时间边界逆序恢复文件
- 按保留期清理过期备份

注意：shell_executor 执行的任意命令产生的文件变更无法追踪。
"""

import asyncio
import logging
import os
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.build_utils import get_file_snapshots_dir
from app.config.database import AsyncSessionLocal
from app.models.agent_file_change import AgentFileChange
from app.services.base_service import BaseService

logger = logging.getLogger(__name__)

# 备份保留天数（未随回退消费的记录超期后由定时任务清理）
RETENTION_DAYS = 7


class AgentFileChangeService(BaseService[AgentFileChange, None, None]):
    """Agent 文件变更记录服务类"""

    def __init__(self):
        super().__init__(AgentFileChange)

    # ---- 记录（工具埋点调用，使用独立会话）----

    async def backup_file(self, session_id: int, file_path: Path) -> Optional[str]:
        """备份即将被覆盖的原文件，返回备份绝对路径；失败返回 None（不阻断写入）"""
        try:
            backup_dir = get_file_snapshots_dir(session_id)
            backup_name = f"{uuid.uuid4().hex}_{file_path.name}"
            backup_path = backup_dir / backup_name
            await asyncio.to_thread(shutil.copy2, file_path, backup_path)
            return str(backup_path)
        except Exception as e:
            logger.warning(
                "文件变更备份失败（继续执行写入）: %s, error=%s", file_path, e
            )
            return None

    async def discard_backup(self, backup_path: Optional[str]) -> None:
        """删除备份文件（写入失败时调用，避免产生无记录的孤儿备份）"""
        if not backup_path:
            return
        try:
            await asyncio.to_thread(os.remove, backup_path)
        except OSError:
            pass

    async def record_change(
        self,
        session_id: int,
        run_id: str,
        tool_name: str,
        file_path: str,
        change_type: str,
        backup_path: Optional[str] = None,
        file_id: Optional[int] = None,
    ) -> None:
        """记录一次文件变更（独立数据库会话，失败仅告警不阻断工具执行）"""
        try:
            async with AsyncSessionLocal() as db:
                db.add(
                    AgentFileChange(
                        session_id=session_id,
                        run_id=run_id,
                        tool_name=tool_name,
                        file_path=file_path,
                        file_id=file_id,
                        change_type=change_type,
                        backup_path=backup_path,
                        is_reverted=0,
                    )
                )
                await db.commit()
        except Exception as e:
            logger.warning(
                "文件变更记录失败: session_id=%s, file=%s, error=%s",
                session_id,
                file_path,
                e,
            )

    # ---- 查询（预览/回退共用，复用调用方会话）----

    async def get_changes_since(
        self,
        db: AsyncSession,
        session_id: int,
        since_time: Optional[datetime] = None,
    ) -> List[AgentFileChange]:
        """查询指定会话自 since_time（不含）之后的未回退变更记录，按时间正序

        since_time 为 None 时返回全部未回退变更（回退到会话开头）。
        """
        query = (
            select(AgentFileChange)
            .where(
                AgentFileChange.session_id == session_id,
                AgentFileChange.is_delete == 0,
                AgentFileChange.is_reverted == 0,
            )
            .order_by(AgentFileChange.id.asc())
        )
        if since_time is not None:
            query = query.where(AgentFileChange.create_time > since_time)
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_changes_boundary(
        self, db: AsyncSession, session_id: int, message_id: int
    ) -> Optional[datetime]:
        """计算回退锚点的时间边界：锚点消息之前最后一条保留消息的创建时间

        工具执行时间必然晚于上一轮消息落库时间（消息执行结束后统一落库），
        因此以该时间作为恢复范围下界是安全的。
        """
        from app.models.agent_message import AgentMessage

        query = (
            select(AgentMessage.create_time)
            .where(
                AgentMessage.session_id == session_id,
                AgentMessage.is_delete == 0,
                AgentMessage.id < message_id,
            )
            .order_by(AgentMessage.id.desc())
            .limit(1)
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    def summarize_changes(
        changes: List[AgentFileChange],
    ) -> List[dict]:
        """将变更记录压缩为按文件去重的回退预览清单

        同一文件多次变更时，回退效果取决于最早一条记录的类型：
        最早为 create（该轮新建）→ 回退后文件被删除；
        最早为 modify → 还原最早备份；最早为 delete → 还原被删文件。
        """
        oldest_by_path: dict[str, AgentFileChange] = {}
        for change in changes:
            if change.file_path not in oldest_by_path:
                oldest_by_path[change.file_path] = change
        return [
            {
                "file_path": change.file_path,
                "change_type": change.change_type,
                "tool_name": change.tool_name,
            }
            for change in oldest_by_path.values()
        ]

    # ---- 恢复 ----

    async def revert_changes(
        self,
        db: AsyncSession,
        session_id: int,
        since_time: Optional[datetime] = None,
    ) -> List[dict]:
        """逆序恢复指定范围的文件变更，返回逐文件恢复结果

        - create → 物理删除该文件（若有 file_id 一并软删 File 记录）
        - modify / delete → 备份内容写回原路径（delete 即还原被删文件），
          随后删除备份
        - 全部记录标记 is_reverted=1
        """
        changes = await self.get_changes_since(db, session_id, since_time)
        if not changes:
            return []

        results: List[dict] = []
        for change in reversed(changes):  # 逆序：最新变更先恢复
            item = {
                "file_path": change.file_path,
                "change_type": change.change_type,
                "tool_name": change.tool_name,
                "status": "ok",
            }
            try:
                if change.change_type == "create":
                    if os.path.exists(change.file_path):
                        await asyncio.to_thread(os.remove, change.file_path)
                    if change.file_id:
                        await self._soft_delete_file(db, change.file_id)
                elif change.change_type in ("modify", "delete"):
                    backup = Path(change.backup_path) if change.backup_path else None
                    if backup and backup.is_file():
                        await asyncio.to_thread(
                            self._restore_from_backup, backup, change.file_path
                        )
                        await asyncio.to_thread(os.remove, backup)
                    else:
                        item["status"] = "backup_missing"
                else:
                    item["status"] = "unknown_type"
            except Exception as e:
                logger.warning("文件恢复失败: %s, error=%s", change.file_path, e)
                item["status"] = "failed"

            results.append(item)

        now = datetime.now()
        for change in changes:
            change.is_reverted = 1
            change.revert_time = now
        await db.commit()
        return results

    @staticmethod
    def _restore_from_backup(backup: Path, target: str) -> None:
        """同步恢复：备份内容写回目标路径（父目录丢失时重建）"""
        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, target_path)

    async def _soft_delete_file(self, db: AsyncSession, file_id: int) -> None:
        """软删 File 表记录（产物文件随 create 回退一并移除）"""
        from app.models.file import File

        await db.execute(update(File).where(File.id == file_id).values(is_delete=1))

    # ---- 清理 ----

    async def cleanup_expired(self, retention_days: int = RETENTION_DAYS) -> None:
        """清理过期备份与记录（定时任务调用）

        - 已回退记录：备份已在恢复时删除，仅物理删除记录控制表体积
        - 未回退且超期记录：删除备份文件与记录
        - 顺带清理空会话目录
        """
        deadline = datetime.now() - timedelta(days=retention_days)
        try:
            async with AsyncSessionLocal() as db:
                # 已回退：物理删除记录
                await db.execute(
                    delete(AgentFileChange).where(AgentFileChange.is_reverted == 1)
                )
                # 超期未回退：先取备份路径，删文件再删记录
                query = select(AgentFileChange).where(
                    AgentFileChange.is_reverted == 0,
                    AgentFileChange.create_time < deadline,
                )
                result = await db.execute(query)
                expired = list(result.scalars().all())
                for change in expired:
                    if change.backup_path:
                        try:
                            await asyncio.to_thread(os.remove, change.backup_path)
                        except OSError:
                            pass
                if expired:
                    await db.execute(
                        delete(AgentFileChange).where(
                            AgentFileChange.id.in_([c.id for c in expired])
                        )
                    )
                await db.commit()
            self._remove_empty_session_dirs()
        except Exception as e:
            logger.warning("文件快照清理失败: %s", e)

    def _remove_empty_session_dirs(self) -> None:
        """删除空会话快照目录及空的快照根目录"""
        root = get_file_snapshots_dir()
        try:
            for child in root.iterdir():
                if child.is_dir() and not any(child.iterdir()):
                    child.rmdir()
            if not any(root.iterdir()):
                root.rmdir()
                get_file_snapshots_dir()  # 下次使用时自动重建
        except OSError:
            pass

    # ---- 会话删除 ----

    async def delete_session_changes(self, db: AsyncSession, session_id: int) -> None:
        """会话删除时清理其全部变更记录与备份文件（物理删除）"""
        query = select(AgentFileChange).where(AgentFileChange.session_id == session_id)
        result = await db.execute(query)
        records = list(result.scalars().all())
        for change in records:
            if change.backup_path:
                try:
                    await asyncio.to_thread(os.remove, change.backup_path)
                except OSError:
                    pass
        await db.execute(
            delete(AgentFileChange).where(AgentFileChange.session_id == session_id)
        )
        await db.commit()
        try:
            session_dir = get_file_snapshots_dir() / str(session_id)
            if session_dir.is_dir() and not any(session_dir.iterdir()):
                session_dir.rmdir()
        except OSError:
            pass


# 单例
agent_file_change_service = AgentFileChangeService()


async def record_tool_file_change(
    tool_name: str,
    file_path: str,
    change_type: str,
    backup_path: Optional[str] = None,
    file_id: Optional[int] = None,
) -> None:
    """工具埋点统一入口：自动读取执行上下文，非 Agent 模式（无会话）跳过记录"""
    from app.agent_flow.file_change_context import get_file_change_context

    ctx = get_file_change_context()
    if not ctx or ctx.session_id <= 0:
        return
    await agent_file_change_service.record_change(
        session_id=ctx.session_id,
        run_id=ctx.run_id,
        tool_name=tool_name,
        file_path=file_path,
        change_type=change_type,
        backup_path=backup_path,
        file_id=file_id,
    )


async def backup_tool_file(file_path: Path) -> Optional[str]:
    """工具埋点备份入口：备份原文件用于回退，非 Agent 模式返回 None"""
    from app.agent_flow.file_change_context import get_file_change_context

    ctx = get_file_change_context()
    if not ctx or ctx.session_id <= 0:
        return None
    return await agent_file_change_service.backup_file(ctx.session_id, file_path)

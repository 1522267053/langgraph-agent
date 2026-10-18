"""
异步等待交互的共享审计写入器

供 ToolApprovalService / QuestionService 复用：两类交互的审计行为完全同构
（asked 快照 → 终态补写），仅 kind 与 payload 结构不同。
写入为 fire-and-forget（独立 task，失败仅告警），绝不阻塞交互主流程；
item_id 唯一约束 + 终态幂等（仅 outcome IS NULL 时更新）保证每批次至多一行终态。
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import update

from app.config.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


class PendingAuditLogger:
    """按 kind + scope 固定的审计写入器（每个 Service/调用方持有一个实例）

    scope 决定 session_id 列的语义：agent=Agent 会话ID，flow=Flow 执行记录ID。
    两域主键独立自增会撞号，scope 必须与查询域一致（见 base_handler._has_session_grant）。
    """

    def __init__(self, kind: str, scope: str = "agent"):
        self._kind = kind
        self._scope = scope

    def _spawn(self, coro) -> None:
        """后台执行审计写入；异常仅告警，不影响交互主流程"""

        async def _runner():
            try:
                await coro
            except Exception:
                logger.exception("%s 审计写入失败（不影响交互流程）", self._kind)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # 无事件循环（如测试同步上下文），放弃本次审计写入
            coro.close()
            logger.warning("%s 审计写入跳过：无运行中的事件循环", self._kind)
            return
        asyncio.create_task(_runner())

    def audit_asked(
        self, session_id: int, item_id: str, payload: dict[str, Any]
    ) -> None:
        """register 后写入 asked 快照（outcome=NULL）"""
        from app.models.interaction_audit_log import InteractionAuditLog

        async def _write():
            async with AsyncSessionLocal() as db:
                db.add(
                    InteractionAuditLog(
                        session_id=session_id,
                        scope=self._scope,
                        item_id=item_id,
                        kind=self._kind,
                        payload=payload,
                        outcome=None,
                    )
                )
                await db.commit()

        self._spawn(_write())

    def audit_decided(
        self,
        session_id: int,
        item_id: str,
        outcome: str,
        answers: Optional[list[str]] = None,
    ) -> None:
        """终态补写：仅当 outcome 仍为 NULL 时更新（幂等，防超时与决策竞争双写）"""
        from app.models.interaction_audit_log import InteractionAuditLog

        async def _write():
            async with AsyncSessionLocal() as db:
                values: dict = {"outcome": outcome, "decided_at": datetime.now()}
                if answers is not None:
                    values["answers"] = answers
                await db.execute(
                    update(InteractionAuditLog)
                    .where(
                        InteractionAuditLog.item_id == item_id,
                        InteractionAuditLog.outcome.is_(None),
                    )
                    .values(**values)
                )
                await db.commit()

        self._spawn(_write())

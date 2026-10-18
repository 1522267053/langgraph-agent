"""
工具审批审计落盘测试

覆盖 ToolApprovalService 全生命周期四路径 + 幂等性：
register(asked) → resolve(approved/rejected) / mark_timeout / cancel(cancelled)。
对应实现：app/services/tool_approval_service.py、app/models/tool_approval_log.py。
"""

import asyncio

from sqlalchemy import select

from app.config.database import AsyncSessionLocal
from app.models.tool_approval_log import ToolApprovalLog
from app.services.tool_approval_service import ToolApprovalService


async def _rows(approval_id: str) -> list[ToolApprovalLog]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ToolApprovalLog).where(ToolApprovalLog.approval_id == approval_id)
        )
        return list(result.scalars().all())


async def test_asked_snapshot_persisted():
    """register 落 asked 快照：outcome=NULL、原因与工具调用完整保存"""
    svc = ToolApprovalService()
    svc.register(
        1,
        "t-asked",
        tool_calls=[{"id": "c1", "name": "shell", "args": {"cmd": "ls"}}],
        approval_needed=["shell"],
        approval_reason="工具已配置为需审批",
    )
    await asyncio.sleep(0.2)  # 等 fire-and-forget 审计写入

    rows = await _rows("t-asked")
    assert len(rows) == 1
    row = rows[0]
    assert row.outcome is None
    assert row.approval_reason == "工具已配置为需审批"
    assert row.tool_calls[0]["name"] == "shell"
    assert row.approval_needed == ["shell"]


async def test_resolve_approved():
    """用户批准 → 终态 approved + decided_at，仍只有一行"""
    svc = ToolApprovalService()
    svc.register(1, "t-ok", approval_needed=["shell"])
    await asyncio.sleep(0.2)

    assert svc.resolve(1, "t-ok", "approved") is True
    await asyncio.sleep(0.2)

    rows = await _rows("t-ok")
    assert len(rows) == 1
    assert rows[0].outcome == "approved"
    assert rows[0].decided_at is not None


async def test_resolve_rejected():
    """用户拒绝 → 终态 rejected"""
    svc = ToolApprovalService()
    svc.register(1, "t-no", approval_needed=["shell"])
    await asyncio.sleep(0.2)

    assert svc.resolve(1, "t-no", "rejected") is True
    await asyncio.sleep(0.2)

    rows = await _rows("t-no")
    assert rows[0].outcome == "rejected"


async def test_mark_timeout():
    """等待超时 → mark_timeout 补写终态 timeout"""
    svc = ToolApprovalService()
    svc.register(1, "t-timeout", approval_needed=["shell"])
    await asyncio.sleep(0.2)

    svc.mark_timeout(1, "t-timeout")
    await asyncio.sleep(0.2)

    rows = await _rows("t-timeout")
    assert rows[0].outcome == "timeout"


async def test_cancel_all_session():
    """会话取消 → 该 session 全部批次终态 cancelled"""
    svc = ToolApprovalService()
    svc.register(1, "t-ca", approval_needed=["shell"])
    svc.register(1, "t-cb", approval_needed=["shell"])
    await asyncio.sleep(0.2)

    svc.cancel(1)
    await asyncio.sleep(0.2)

    assert (await _rows("t-ca"))[0].outcome == "cancelled"
    assert (await _rows("t-cb"))[0].outcome == "cancelled"


async def test_duplicate_register_single_row():
    """重复注册复用 Future：不重复写 asked 行"""
    svc = ToolApprovalService()
    svc.register(2, "t-dup", approval_needed=["shell"])
    svc.register(2, "t-dup", approval_needed=["shell"])
    await asyncio.sleep(0.3)

    assert len(await _rows("t-dup")) == 1


async def test_decided_idempotent():
    """终态幂等：approved 后再 mark_timeout 不覆盖"""
    svc = ToolApprovalService()
    svc.register(1, "t-idem", approval_needed=["shell"])
    await asyncio.sleep(0.2)
    svc.resolve(1, "t-idem", "approved")
    await asyncio.sleep(0.2)

    svc.mark_timeout(1, "t-idem")
    await asyncio.sleep(0.2)

    assert (await _rows("t-idem"))[0].outcome == "approved"


async def test_resolve_unknown_id():
    """resolve 不存在的 approval_id：返回 False 且不写审计"""
    svc = ToolApprovalService()
    assert svc.resolve(99, "t-ghost", "approved") is False
    await asyncio.sleep(0.2)
    assert await _rows("t-ghost") == []

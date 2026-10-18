"""
交互审计落盘测试（统一表 interaction_audit_log）

覆盖两类交互（kind=approval / kind=question）的全生命周期四路径 + 幂等性：
register(asked) → resolve / mark_timeout / cancel。
对应实现：app/services/pending_audit.py、app/models/interaction_audit_log.py、
app/services/tool_approval_service.py、app/services/question_service.py。
"""

import asyncio

from sqlalchemy import select

from app.config.database import AsyncSessionLocal
from app.models.interaction_audit_log import InteractionAuditLog
from app.services.question_service import QuestionService
from app.services.tool_approval_service import ToolApprovalService


async def _rows(item_id: str) -> list[InteractionAuditLog]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(InteractionAuditLog).where(
                InteractionAuditLog.item_id == item_id
            )
        )
        return list(result.scalars().all())


# ---- 审批（kind=approval） ----


async def test_approval_asked_snapshot_persisted():
    """审批 register 落 asked 快照：kind=approval、outcome=NULL、原因与工具调用完整保存"""
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
    assert row.kind == "approval"
    assert row.outcome is None
    assert row.payload["approval_reason"] == "工具已配置为需审批"
    assert row.payload["tool_calls"][0]["name"] == "shell"
    assert row.payload["approval_needed"] == ["shell"]


async def test_approval_resolve_approved():
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


async def test_approval_resolve_rejected():
    """用户拒绝 → 终态 rejected"""
    svc = ToolApprovalService()
    svc.register(1, "t-no", approval_needed=["shell"])
    await asyncio.sleep(0.2)

    assert svc.resolve(1, "t-no", "rejected") is True
    await asyncio.sleep(0.2)

    assert (await _rows("t-no"))[0].outcome == "rejected"


async def test_approval_mark_timeout():
    """等待超时 → mark_timeout 补写终态 timeout"""
    svc = ToolApprovalService()
    svc.register(1, "t-timeout", approval_needed=["shell"])
    await asyncio.sleep(0.2)

    svc.mark_timeout(1, "t-timeout")
    await asyncio.sleep(0.2)

    assert (await _rows("t-timeout"))[0].outcome == "timeout"


async def test_approval_cancel_all_session():
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
    """resolve 不存在的 item_id：返回 False 且不写审计"""
    svc = ToolApprovalService()
    assert svc.resolve(99, "t-ghost", "approved") is False
    await asyncio.sleep(0.2)
    assert await _rows("t-ghost") == []


# ---- 反问（kind=question） ----


async def test_question_asked_snapshot_persisted():
    """反问 register 落 asked 快照：kind=question、问题原文与选项结构完整保存"""
    svc = QuestionService()
    svc.register(
        1,
        "q-asked",
        question="选择数据库",
        options=[{"label": "SQLite", "description": "零配置"}, {"label": "MySQL"}],
        multiple=False,
    )
    await asyncio.sleep(0.2)

    rows = await _rows("q-asked")
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "question"
    assert row.outcome is None
    assert row.payload["question"] == "选择数据库"
    assert row.payload["options"][0]["label"] == "SQLite"
    assert row.payload["multiple"] is False
    assert row.answers is None


async def test_question_resolve_answered():
    """用户回答 → 终态 answered + answers 落盘"""
    svc = QuestionService()
    svc.register(1, "q-ok", question="选一个", options=[{"label": "A"}], multiple=True)
    await asyncio.sleep(0.2)

    assert svc.resolve(1, "q-ok", ["A", "B"]) is True
    await asyncio.sleep(0.2)

    rows = await _rows("q-ok")
    assert rows[0].outcome == "answered"
    assert rows[0].answers == ["A", "B"]
    assert rows[0].decided_at is not None


async def test_question_resolve_empty_is_cancelled():
    """answers=[]（前端取消约定）→ 终态 cancelled"""
    svc = QuestionService()
    svc.register(1, "q-cancel", question="选一个", options=[{"label": "A"}])
    await asyncio.sleep(0.2)

    assert svc.resolve(1, "q-cancel", []) is True
    await asyncio.sleep(0.2)

    rows = await _rows("q-cancel")
    assert rows[0].outcome == "cancelled"


async def test_question_timeout_and_cancel():
    """反问超时 → timeout；会话取消 → cancelled"""
    svc = QuestionService()
    svc.register(1, "q-timeout", question="q1", options=[])
    svc.register(1, "q-cancelled", question="q2", options=[])
    await asyncio.sleep(0.2)

    svc.mark_timeout(1, "q-timeout")
    svc.cancel(1, "q-cancelled")
    await asyncio.sleep(0.2)

    assert (await _rows("q-timeout"))[0].outcome == "timeout"
    assert (await _rows("q-cancelled"))[0].outcome == "cancelled"

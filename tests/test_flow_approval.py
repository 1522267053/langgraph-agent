"""
Flow 模式工具审批测试（interrupt 路径）

覆盖：scope=flow 审计落盘、execution 级授权写入/命中、scope 隔离
（Agent 授权不误命中 Flow 域——两域主键独立自增会撞号）。
interrupt 本体（LangGraph 暂停/resume）由 QA 实跑覆盖，此处测决策解析与落盘。
"""

import asyncio

from sqlalchemy import select

from app.config.database import AsyncSessionLocal
from app.models.interaction_audit_log import InteractionAuditLog
from app.models.session_tool_grant import SessionToolGrant
from app.services.pending_audit import PendingAuditLogger


async def _audit_rows(item_id: str) -> list[InteractionAuditLog]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(InteractionAuditLog).where(
                InteractionAuditLog.item_id == item_id
            )
        )
        return list(result.scalars().all())


async def test_flow_approval_asked_with_scope():
    """Flow 审批 asked：scope=flow、session_id 列存 execution_id、payload 完整"""
    audit = PendingAuditLogger("approval", scope="flow")
    audit.audit_asked(
        501,  # execution_id
        "f-asked",
        {
            "tool_calls": [{"id": "c1", "name": "shell_executor", "args": {"command": "rm -rf x"}}],
            "approval_needed": ["shell_executor"],
            "approval_reason": "命令匹配危险模式: rm\\s+-rf",
        },
    )
    await asyncio.sleep(0.2)

    rows = await _audit_rows("f-asked")
    assert len(rows) == 1
    row = rows[0]
    assert row.scope == "flow"
    assert row.session_id == 501
    assert row.kind == "approval"
    assert row.outcome is None
    assert row.payload["approval_reason"] == "命令匹配危险模式: rm\\s+-rf"


async def test_flow_approval_decided():
    """Flow 审批决策：resume 后 audit_decided 补写终态"""
    audit = PendingAuditLogger("approval", scope="flow")
    audit.audit_asked(502, "f-ok", {"approval_needed": ["shell_executor"]})
    await asyncio.sleep(0.2)
    audit.audit_decided(502, "f-ok", "approved")
    await asyncio.sleep(0.2)

    rows = await _audit_rows("f-ok")
    assert rows[0].scope == "flow"
    assert rows[0].outcome == "approved"


async def test_flow_grant_scope_isolation():
    """scope 隔离：Agent 会话 501 的授权不得命中 Flow 执行 501 的查询"""
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    async with AsyncSessionLocal() as db:
        # Agent 侧授权：scope=agent, session_id=501, tool=shell_executor
        stmt = sqlite_insert(SessionToolGrant).values(
            scope="agent",
            session_id=501,
            tool_name="shell_executor",
            granted_by="t-agent-1",
        )
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["scope", "session_id", "tool_name"]
        )
        await db.execute(stmt)
        await db.commit()

    # Flow 侧查询同号 execution_id=501 + 同工具：不得命中（scope 不同）
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SessionToolGrant.id).where(
                SessionToolGrant.scope == "flow",
                SessionToolGrant.session_id == 501,
                SessionToolGrant.tool_name == "shell_executor",
                SessionToolGrant.is_delete == 0,
            )
        )
        assert result.first() is None

    # Agent 侧同条件查询：命中
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SessionToolGrant.id).where(
                SessionToolGrant.scope == "agent",
                SessionToolGrant.session_id == 501,
                SessionToolGrant.tool_name == "shell_executor",
                SessionToolGrant.is_delete == 0,
            )
        )
        assert result.first() is not None


async def test_flow_grant_upsert_idempotent():
    """Flow 授权重复写入幂等（on_conflict_do_nothing，保留首次 granted_by）"""
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    for approval_id in ("f-grant-1", "f-grant-2"):
        async with AsyncSessionLocal() as db:
            stmt = sqlite_insert(SessionToolGrant).values(
                scope="flow",
                session_id=503,
                tool_name="shell_executor",
                granted_by=approval_id,
            )
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["scope", "session_id", "tool_name"]
            )
            await db.execute(stmt)
            await db.commit()

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SessionToolGrant).where(
                SessionToolGrant.scope == "flow",
                SessionToolGrant.session_id == 503,
            )
        )
        rows = list(result.scalars().all())
    assert len(rows) == 1
    assert rows[0].granted_by == "f-grant-1"

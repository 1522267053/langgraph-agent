"""
逐工具审批决策（方案B）测试

覆盖：单窗口多卡片独立决策的分发链路——
1. resolve 携带 per_tool_decisions 时 future 正确记录
2. 批内等待方按 tool_call_id 取各自结局（缺失项 fail-closed 视为 rejected）
3. 整批决策（不携带 per_tool_decisions）行为不变（向后兼容）
"""

import asyncio

from app.services.tool_approval_service import ToolApprovalService


async def test_per_tool_decisions_recorded_on_resolve():
    """resolve 携带逐工具决策：future.per_tool_decisions 正确落位"""
    svc = ToolApprovalService()
    future = svc.register(1, "pt-1", approval_needed=["shell"])
    assert future.per_tool_decisions is None

    assert (
        svc.resolve(
            1,
            "pt-1",
            "rejected",
            per_tool_decisions={"call_a": "approved", "call_b": "rejected"},
        )
        is True
    )
    assert future.per_tool_decisions == {"call_a": "approved", "call_b": "rejected"}
    assert future.result == "rejected"  # 整批语义字段：存在拒绝项


async def test_per_tool_decisions_dispatch_by_call_id():
    """批内等待方按 call_id 分发：approved 的拿到 approved，rejected 的拿到 rejected"""
    svc = ToolApprovalService()
    future = svc.register(1, "pt-2", approval_needed=["shell"])

    results: dict[str, str] = {}

    async def waiter(call_id: str):
        await asyncio.wait_for(future.event.wait(), timeout=5)
        per_tool = future.per_tool_decisions
        if per_tool and call_id:
            results[call_id] = per_tool.get(call_id, "rejected")
        else:
            results[call_id] = future.result or "rejected"

    t1 = asyncio.create_task(waiter("call_a"))
    t2 = asyncio.create_task(waiter("call_b"))
    await asyncio.sleep(0.05)
    svc.resolve(
        1,
        "pt-2",
        "rejected",
        per_tool_decisions={"call_a": "approved", "call_b": "rejected"},
    )
    await asyncio.gather(t1, t2)

    assert results == {"call_a": "approved", "call_b": "rejected"}


async def test_per_tool_missing_id_fails_closed():
    """逐工具决策缺失 call_id：fail-closed 视为 rejected（不静默放行）"""
    svc = ToolApprovalService()
    future = svc.register(1, "pt-3", approval_needed=["shell"])
    svc.resolve(1, "pt-3", "rejected", per_tool_decisions={"call_a": "approved"})

    per_tool = future.per_tool_decisions or {}
    assert per_tool.get("call_unknown", "rejected") == "rejected"


async def test_batch_decision_without_per_tool_unchanged():
    """整批决策（不携带 per_tool_decisions）：行为与旧版完全一致"""
    svc = ToolApprovalService()
    future = svc.register(1, "pt-4", approval_needed=["shell"])
    svc.resolve(1, "pt-4", "approved")

    assert future.per_tool_decisions is None
    assert future.result == "approved"
    # 等待方回退读 result
    assert (future.per_tool_decisions and future.per_tool_decisions.get("x")) or (
        future.result or "rejected"
    ) == "approved"


async def test_per_tool_resolve_unknown_id():
    """逐工具 resolve 不存在的批次：返回 False，不写 future"""
    svc = ToolApprovalService()
    assert (
        svc.resolve(99, "pt-ghost", "approved", per_tool_decisions={"call_x": "approved"})
        is False
    )

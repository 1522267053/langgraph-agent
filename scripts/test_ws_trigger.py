"""WS trigger 协议集成测试脚本

针对 ws_trigger_api.py 的会话级并发、call_id 事件路由、resume、
tool_approval、cancel、空闲超时（4408）、Agent 单连接（4409）做端到端验证。

前置条件：
- 平台已启动（默认 http://127.0.0.1:8000）
- 数据库中存在至少一个 agent 类型和一个 flow 类型的流程

用法：
    poetry run python scripts/test_ws_trigger.py             # 基础用例
    poetry run python scripts/test_ws_trigger.py --idle     # 附加空闲超时用例（约 130 秒）
    poetry run python scripts/test_ws_trigger.py --agent-flow-id 16 --flow-flow-id 22

测试通过本地 DB 直接创建临时网关（绕过 HTTP 登录），结束后物理删除
网关、调用记录和测试会话，不污染现有数据。
"""

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Optional

from websockets.asyncio.client import connect

# 项目根目录加入 sys.path（脚本方式运行时找不到 app 包）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_URL = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000"

_PASS = 0
_FAIL = 0


def report(name: str, ok: bool, detail: str = ""):
    """记录单条用例结果"""
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f"[PASS] {name}" + (f"  ({detail})" if detail else ""))
    else:
        _FAIL += 1
        print(f"[FAIL] {name}" + (f"  ({detail})" if detail else ""))


# ---- 测试网关管理（本地 DB 直连） ----


async def pick_flow_ids() -> tuple[Optional[int], Optional[int]]:
    """查询数据库：优先选择名称含 test 的 agent/flow，否则取第一个"""
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.flow import Flow, FlowType

    async with AsyncSessionLocal() as db:
        agents = (
            (
                await db.execute(
                    select(Flow).where(Flow.flow_type == FlowType.AGENT.value)
                )
            )
            .scalars()
            .all()
        )
        flows = (
            (
                await db.execute(
                    select(Flow).where(Flow.flow_type == FlowType.FLOW.value)
                )
            )
            .scalars()
            .all()
        )
    # 排除软删除
    agents = [a for a in agents if not a.is_delete]
    flows = [f for f in flows if not f.is_delete]

    def pick(items):
        named = [i for i in items if i.name and "test" in i.name.lower()]
        return (named or items or [None])[0]

    return (pick(agents).id if agents else None, pick(flows).id if flows else None)


async def create_test_gateway(flow_id: int, name: str) -> tuple[int, str]:
    """创建临时测试网关，返回 (gateway_id, token)"""

    from app.config.database import AsyncSessionLocal
    from app.models.ws_gateway import WsGatewayConfig

    async with AsyncSessionLocal() as db:
        gw = WsGatewayConfig(
            flow_id=flow_id,
            name=name,
            token=uuid.uuid4().hex,
            description="WS trigger 集成测试临时网关（自动清理）",
            is_enabled=1,
            call_count=0,
        )
        db.add(gw)
        await db.commit()
        await db.refresh(gw)
        return gw.id, gw.token


async def cleanup_test_gateways(gateway_ids: list[int]):
    """物理删除测试网关及其调用记录、网关创建的会话"""
    from sqlalchemy import delete, select

    from app.config.database import AsyncSessionLocal
    from app.models.agent_session import AgentSession
    from app.models.ws_gateway import WsGatewayConfig
    from app.models.ws_gateway_call_record import WsGatewayCallRecord

    if not gateway_ids:
        return
    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(WsGatewayCallRecord).where(
                WsGatewayCallRecord.gateway_id.in_(gateway_ids)
            )
        )
        sessions = (
            await db.execute(
                select(AgentSession).where(AgentSession.gateway_id.in_(gateway_ids))
            )
        ).scalars()
        for s in sessions:
            await db.delete(s)
        for gid in gateway_ids:
            gw = await db.get(WsGatewayConfig, gid)
            if gw:
                await db.delete(gw)
        await db.commit()


# ---- WS 客户端封装：按 call_id 路由事件 ----


class TriggerClient:
    """WS trigger 客户端：后台读取事件，call_id 事件进 per-call 队列，其余进控制队列"""

    def __init__(self, ws):
        self.ws = ws
        self.control: asyncio.Queue = asyncio.Queue()
        self.calls: dict = {}
        self._returned_calls: set = set()
        self._reader = asyncio.create_task(self._read_loop())
        self.closed_code: Optional[int] = None

    async def _read_loop(self):
        """持续读取：pong 进控制队列，JSON 按 call_id 分发（call_started 双路分发）"""
        try:
            async for raw in self.ws:
                if not raw.strip():
                    continue
                if raw == "pong":
                    self.control.put_nowait({"type": "__pong__"})
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                call_id = event.get("call_id")
                if call_id is not None:
                    self.calls.setdefault(call_id, asyncio.Queue())
                    self.calls[call_id].put_nowait(event)
                    if event.get("type") == "call_started":
                        self.control.put_nowait(event)
                else:
                    self.control.put_nowait(event)
        except Exception as e:
            rcvd = getattr(e, "rcvd", None)
            if rcvd is not None:
                self.closed_code = rcvd.code
            self.control.put_nowait({"type": "__closed__"})

    async def send(self, payload: dict):
        """发送 JSON 指令"""
        await self.ws.send(json.dumps(payload, ensure_ascii=False))

    async def wait_control(self, timeout: float = 10) -> dict:
        """等待下一个控制事件（无 call_id）"""
        return await asyncio.wait_for(self.control.get(), timeout=timeout)

    async def expect_control(self, type_: str, timeout: float = 10) -> Optional[dict]:
        """在控制队列中等待指定类型事件（跳过其他类型，超时返回 None）"""
        try:
            while True:
                ev = await asyncio.wait_for(self.control.get(), timeout=timeout)
                if ev.get("type") == type_:
                    return ev
                if ev.get("type") == "__closed__":
                    return None
        except asyncio.TimeoutError:
            return None

    async def wait_call_started(self, timeout: float = 30) -> Optional[int]:
        """等待新执行的 call_started，返回 call_id（同一 call_id 只返回一次）"""
        try:
            while True:
                ev = await asyncio.wait_for(self.control.get(), timeout=timeout)
                if ev.get("type") == "__closed__":
                    return None
                if ev.get("type") == "call_started":
                    cid = ev.get("data", {}).get("call_id")
                    if cid is not None and cid not in self._returned_calls:
                        self._returned_calls.add(cid)
                        return cid
        except asyncio.TimeoutError:
            return None

    async def drain_call(self, call_id: int, timeout: float = 120) -> list:
        """收集一个执行的完整事件流直到终态（flow_done / 带 call_id 的 error）"""
        q = self.calls.get(call_id)
        events = []
        try:
            while True:
                ev = await asyncio.wait_for(q.get(), timeout=timeout)
                events.append(ev)
                if ev.get("type") in ("flow_done", "error"):
                    return events
        except asyncio.TimeoutError:
            return events


# ---- 用例 ----


async def test_connect_and_heartbeat(client: TriggerClient):
    """用例1：connected 事件字段 + ping/pong"""
    ev = await client.expect_control("connected", timeout=10)
    ok = ev is not None and all(
        k in ev.get("data", {}) for k in ("gateway_id", "flow_id", "flow_type")
    )
    report("connected 事件", bool(ok))
    await client.ws.send("ping")
    pong = await client.expect_control("__pong__", timeout=5)
    report("ping/pong", pong is not None)


async def test_agent_single_connection(token: str):
    """用例2：Agent 网关第二条连接被 4409 拒绝"""
    try:
        async with connect(f"{WS_URL}/ws/trigger/{token}", open_timeout=10) as ws2:
            client2 = TriggerClient(ws2)
            await client2.expect_control("__closed__", timeout=10)
            report("Agent 第二连接拒绝(4409)", client2.closed_code == 4409)
            client2._reader.cancel()
    except Exception as e:
        report("Agent 第二连接拒绝(4409)", False, f"异常: {e}")


async def test_concurrent_sessions(client: TriggerClient, msg: str):
    """用例3：两个不同会话并发 execute，事件按 call_id 分流不串扰"""
    # 创建两个会话
    sids = []
    for _ in range(2):
        await client.send({"action": "create_session", "title": "ws测试会话"})
        ev = await client.expect_control("session_created", timeout=10)
        if not ev:
            break
        sids.append(ev["data"]["session_id"])
    report("create_session x2", len(sids) == 2, f"sessions={sids}")
    if len(sids) != 2:
        return []

    # 并发 execute
    await client.send({"action": "execute", "session_id": sids[0], "message": msg})
    await client.send({"action": "execute", "session_id": sids[1], "message": msg})

    call1 = await client.wait_call_started(timeout=30)
    call2 = await client.wait_call_started(timeout=30)
    report(
        "并发 execute 均获 call_started",
        call1 is not None and call2 is not None,
        f"call_ids={call1},{call2}",
    )
    if call1 is None or call2 is None:
        return sids
    report("call_id 互不相同", call1 != call2)

    events1, events2 = await asyncio.gather(
        client.drain_call(call1), client.drain_call(call2)
    )
    ok1 = all(e.get("call_id") == call1 for e in events1)
    ok2 = all(e.get("call_id") == call2 for e in events2)
    report(
        "事件流按 call_id 分流", ok1 and ok2, f"n1={len(events1)}, n2={len(events2)}"
    )
    return sids


async def test_same_session_rejection(client: TriggerClient, sid: int, msg: str):
    """用例4：同一会话并发 execute，第二条被会话锁拒绝"""
    await client.send({"action": "execute", "session_id": sid, "message": msg})
    await client.send({"action": "execute", "session_id": sid, "message": msg})

    rejection = False
    started = False
    done_calls = []
    debug_events = []
    try:
        while True:
            ev = await asyncio.wait_for(client.control.get(), timeout=60)
            t = ev.get("type")
            debug_events.append(
                f"{t}:{json.dumps(ev.get('data', {}), ensure_ascii=False)[:60]}"
            )
            if t == "error" and "正在执行中" in ev.get("data", {}).get("message", ""):
                rejection = True
            elif t == "call_started":
                started = True
                done_calls.append(ev["data"].get("call_id"))
            if rejection and started:
                break
    except asyncio.TimeoutError:
        pass
    report(
        "同会话并发拒绝",
        rejection and started,
        "; ".join(debug_events[:6]) if not (rejection and started) else "",
    )

    # 等首个执行结束，避免影响后续用例
    for cid in done_calls:
        await client.drain_call(cid, timeout=120)


async def test_tool_approval(client: TriggerClient, sid: int):
    """用例5：tool_approval 无待审批返回 resolved=false；非法会话报错"""
    await client.send(
        {"action": "tool_approval", "session_id": sid, "result": "approved"}
    )
    ev = await client.expect_control("tool_approval_result", timeout=10)
    report(
        "tool_approval 无待审批 resolved=false",
        ev is not None and ev.get("data", {}).get("resolved") is False,
    )

    await client.send(
        {"action": "tool_approval", "session_id": 99999999, "result": "approved"}
    )
    ev = await client.expect_control("error", timeout=10)
    report(
        "tool_approval 非法会话报错",
        ev is not None and "不属于该网关" in ev.get("data", {}).get("message", ""),
    )

    await client.send({"action": "tool_approval", "session_id": sid, "result": "yes"})
    ev = await client.expect_control("error", timeout=10)
    report(
        "tool_approval 非法 result 报错",
        ev is not None
        and "approved 或 rejected" in ev.get("data", {}).get("message", ""),
    )


async def test_resume_errors(client: TriggerClient):
    """用例6：resume 参数校验与归属校验错误路径"""
    await client.send({"action": "resume", "session_id": 1})
    ev = await client.expect_control("error", timeout=10)
    report(
        "resume 缺少 input 报错",
        ev is not None and "input" in ev.get("data", {}).get("message", ""),
    )

    await client.send({"action": "resume", "session_id": 99999999, "input": "测试"})
    ev = await client.expect_control("error", timeout=10)
    report(
        "resume 非法会话报错",
        ev is not None and "不属于该网关" in ev.get("data", {}).get("message", ""),
    )


async def test_cancel(client: TriggerClient):
    """用例7：cancel 已归属会话返回 cancel_accepted（用一次性会话，随后删除）"""
    await client.send({"action": "create_session", "title": "cancel测试"})
    ev = await client.expect_control("session_created", timeout=10)
    if not ev:
        report("cancel 会话创建", False)
        return
    sid = ev["data"]["session_id"]

    await client.send({"action": "cancel", "session_id": sid})
    ev = await client.expect_control("cancel_accepted", timeout=10)
    report(
        "cancel 已归属会话 accepted",
        ev is not None and ev.get("data", {}).get("session_id") == sid,
    )

    await client.send({"action": "cancel", "session_id": 99999999})
    ev = await client.expect_control("error", timeout=10)
    report(
        "cancel 非法会话报错",
        ev is not None and "不属于该网关" in ev.get("data", {}).get("message", ""),
    )

    await client.send({"action": "delete_session", "session_id": sid})
    ev = await client.expect_control("session_deleted", timeout=10)
    report("一次性会话清理", ev is not None)


async def test_flow_gateway_inner(ws, token: str):
    """用例8：Flow 网关 execute（call_id 一致性）与 cancel 归属校验"""
    fc = TriggerClient(ws)
    await fc.expect_control("connected", timeout=10)

    await fc.send({"action": "execute"})
    cid = await fc.wait_call_started(timeout=30)
    report("Flow execute call_started", cid is not None, f"call_id={cid}")
    if cid is not None:
        events = await fc.drain_call(cid, timeout=120)
        ok = all(e.get("call_id") == cid for e in events)
        report("Flow 事件 call_id 一致", ok, f"n={len(events)}")

    await fc.send({"action": "cancel", "execution_id": 99999999})
    ev = await fc.expect_control("error", timeout=10)
    report(
        "Flow cancel 非法 execution 报错",
        ev is not None and "不属于该网关" in ev.get("data", {}).get("message", ""),
    )
    fc._reader.cancel()


async def test_idle_timeout(token: str):
    """用例9（--idle）：空闲超时服务端以 4408 断开"""
    from app.config.settings import settings

    timeout_s = settings.ws_trigger_idle_timeout
    if timeout_s <= 0:
        report("空闲超时(4408)", False, "服务端配置 WS_TRIGGER_IDLE_TIMEOUT=0 未启用")
        return
    print(f"  等待 {timeout_s + 15} 秒（空闲超时 {timeout_s}s）...")
    try:
        async with connect(f"{WS_URL}/ws/trigger/{token}", open_timeout=10) as ws:
            client = TriggerClient(ws)
            await client.expect_control("connected", timeout=10)
            deadline = asyncio.get_event_loop().time() + timeout_s + 15
            while (
                client.closed_code is None
                and asyncio.get_event_loop().time() < deadline
            ):
                await asyncio.sleep(1)
            report(
                "空闲超时(4408)",
                client.closed_code == 4408,
                f"code={client.closed_code}",
            )
            client._reader.cancel()
    except Exception as e:
        report("空闲超时(4408)", False, f"异常: {e}")


# ---- 主流程 ----


async def main():
    global BASE_URL, WS_URL
    parser = argparse.ArgumentParser(description="WS trigger 协议集成测试")
    parser.add_argument("--base-url", default=BASE_URL, help="平台地址")
    parser.add_argument("--agent-flow-id", type=int, default=None)
    parser.add_argument("--flow-flow-id", type=int, default=None)
    parser.add_argument(
        "--idle", action="store_true", help="附加空闲超时用例（约2分钟）"
    )
    args = parser.parse_args()

    WS_URL = args.base_url.replace("http://", "ws://").replace("https://", "wss://")

    agent_id, flow_id = args.agent_flow_id, args.flow_flow_id
    if agent_id is None or flow_id is None:
        picked_agent, picked_flow = await pick_flow_ids()
        agent_id = agent_id if agent_id is not None else picked_agent
        flow_id = flow_id if flow_id is not None else picked_flow

    if not agent_id:
        print("数据库中无 agent 类型流程，无法测试")
        sys.exit(2)

    print(f"测试目标: agent_flow={agent_id} flow_flow={flow_id}")
    gw_agent, gw_flow = None, None
    try:
        gw_agent = await create_test_gateway(agent_id, "ws-test-agent")
        if flow_id:
            gw_flow = await create_test_gateway(flow_id, "ws-test-flow")
        print(f"临时网关: agent={gw_agent[0]} flow={gw_flow[0] if gw_flow else '-'}")

        # Agent 网关主连接
        async with connect(f"{WS_URL}/ws/trigger/{gw_agent[1]}", open_timeout=10) as ws:
            client = TriggerClient(ws)
            await test_connect_and_heartbeat(client)
            await test_agent_single_connection(gw_agent[1])

            sids = await test_concurrent_sessions(client, "收到请回复：测试OK")
            if sids:
                await test_same_session_rejection(client, sids[0], "收到请回复：锁测试")
                await test_tool_approval(client, sids[0])
            await test_resume_errors(client)
            await test_cancel(client)

            for sid in sids or []:
                await client.send({"action": "delete_session", "session_id": sid})
                await client.expect_control("session_deleted", timeout=10)
            client._reader.cancel()

        if gw_flow:
            async with connect(
                f"{WS_URL}/ws/trigger/{gw_flow[1]}", open_timeout=10
            ) as ws:
                await test_flow_gateway_inner(ws, gw_flow[1])

        if args.idle:
            await test_idle_timeout(gw_agent[1])

    finally:
        ids = [g[0] for g in (gw_agent, gw_flow) if g]
        await cleanup_test_gateways(ids)
        print(f"\n清理完成（网关 {ids}）")

    print(f"\n结果: {_PASS} 通过, {_FAIL} 失败")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())

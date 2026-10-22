"""ReAct 中途落库 + 内容回放水位推进 + 已落库事件内存释放 的单元测试

覆盖：
1. advance_content_watermark：无 run no-op / 活跃 run 推进水位 / done 后不推进
2. prune_persisted_events：水位前内容类事件置 None；水位前轻量事件保留；
   水位后事件不动；None 占位幂等
3. subscribe_run 遇 None 占位：游标正常推进不死循环，后续事件正常送达
"""

import asyncio

from app.services.agent_executor_service import (
    _PERSISTED_CONTENT_EVENT_TYPES,
    AgentExecutorService,
    _AgentRun,
)


def _make_event(event_id: int, event_type: str) -> dict:
    return {"id": event_id, "type": event_type, "data": {}}


def _make_run(event_specs: list[tuple[int, str]], watermark: int = 0) -> _AgentRun:
    """构造带预置事件的 run（事件 ID = 列表索引 + 1，与发布逻辑一致）"""
    run = _AgentRun(run_id="run-test", session_id=1)
    for event_id, event_type in event_specs:
        run.events.append(_make_event(event_id, event_type))
    run.next_event_id = len(event_specs) + 1
    run.content_watermark_event_id = watermark
    return run


# ---- advance_content_watermark ----


async def test_advance_watermark_no_run_noop():
    """无活跃 run（Flow 模式/会话未执行）时调用不抛错、无副作用"""
    service = AgentExecutorService()
    service.advance_content_watermark(9999)  # 不应抛 KeyError/AttributeError


async def test_advance_watermark_active_run():
    """活跃 run：水位推进到落库时刻 last_event_id，内容类事件 prune 置 None，
    并发布 content_watermark 面板事件（实时订阅者同步前端本地水位）"""
    service = AgentExecutorService()
    run = _make_run(
        [
            (1, "node_content"),
            (2, "node_thinking"),
            (3, "todo_update"),
            (4, "node_done"),
        ],
        watermark=0,
    )
    service._agent_runs[run.session_id] = run

    service.advance_content_watermark(run.session_id)

    # 水位 = 推进时刻的 last_event_id；发布的水位事件自身 id 更大（5）
    assert run.content_watermark_event_id == 4
    assert run.last_event_id == 5
    # 水位前内容类事件已释放（置 None 占位）
    assert run.events[0] is None  # node_content
    assert run.events[1] is None  # node_thinking
    # 水位前轻量事件保留（计划面板恢复依赖）
    assert run.events[2] is not None  # todo_update
    assert run.events[3] is not None  # node_done
    # 水位事件已发布，携带推进后的水位值
    watermark_event = run.events[4]
    assert watermark_event is not None
    assert watermark_event["type"] == "content_watermark"
    assert watermark_event["data"]["watermark_event_id"] == 4


async def test_advance_watermark_done_run_noop():
    """run 已结束时水位不推进（回放兜底走 retention 清理）"""
    service = AgentExecutorService()
    run = _make_run([(1, "node_content")], watermark=0)
    run.done = True
    service._agent_runs[run.session_id] = run

    service.advance_content_watermark(run.session_id)

    assert run.content_watermark_event_id == 0
    assert run.events[0] is not None


async def test_node_done_publishes_watermark_event():
    """node_done 经真实发布路径推进水位时同步发布 content_watermark 事件。

    无工具轮次（LLM 单节点直出）没有中途落库推进、全程直播也不走 running_status
    初始化——若 node_done 不发水位事件，前端水位恒 undefined，flow_done 终态拼接
    时「宁多勿丢」兜底保留全部 live 段，与 DB 全量段成对重复（实测无工具长文本
    轮内容渲染两份）。
    """
    service = AgentExecutorService()
    run = _make_run(
        [
            (1, "node_thinking"),
            (2, "node_content"),
        ],
        watermark=0,
    )
    service._agent_runs[run.session_id] = run

    # 经真实发布路径发 node_done（与生产一致：id 分配/水位/事件流/唤醒全走统一入口）
    service._publish_agent_run_event(run, {"type": "node_done", "data": {}})

    # 发布路径分配 id=3；水位推进到 node_done 自身 id，随后紧邻发布水位事件（id=4）
    assert run.content_watermark_event_id == 3
    assert run.events[2] is not None
    assert run.events[2]["type"] == "node_done"
    assert run.events[3] is not None
    assert run.events[3]["type"] == "content_watermark"
    assert run.events[3]["data"]["watermark_event_id"] == 3
    assert run.last_event_id == 4
    # 水位前的内容类事件已被 prune 置 None；node_done/水位事件保留
    assert run.events[0] is None
    assert run.events[1] is None


# ---- prune_persisted_events ----


def test_prune_keeps_events_after_watermark():
    """水位之后的事件（含内容类）不释放——属于进行中节点，回放必需"""
    run = _make_run(
        [
            (1, "node_content"),
            (2, "tool_call_end"),
            (3, "node_content"),
        ],
        watermark=2,
    )
    run.prune_persisted_events()

    assert run.events[0] is None
    assert run.events[1] is None
    assert run.events[2] is not None  # 水位后内容类保留


def test_prune_keeps_non_content_events():
    """水位前的非内容类事件全部保留（等待/进度/节点边界语义）"""
    specs = [
        (1, "node_start"),
        (2, "node_done"),
        (3, "todo_update"),
        (4, "sub_agent_progress"),
        (5, "tool_approval_required"),
        (6, "question_request"),
        (7, "node_content"),
    ]
    run = _make_run(specs, watermark=7)
    run.prune_persisted_events()

    for i, (_, event_type) in enumerate(specs[:-1]):
        assert run.events[i] is not None, f"event {event_type} 不应被释放"
    assert run.events[6] is None  # 唯一的内容类被释放


def test_prune_idempotent_over_none():
    """重复 prune 遇 None 占位不报错（幂等）"""
    run = _make_run([(1, "node_content")], watermark=1)
    run.prune_persisted_events()
    run.prune_persisted_events()  # 第二次遍历跳过 None
    assert run.events[0] is None


def test_prune_covers_all_persisted_types():
    """_PERSISTED_CONTENT_EVENT_TYPES 全部成员都会被释放（防新增类型遗漏语义）"""
    assert _PERSISTED_CONTENT_EVENT_TYPES  # 非空防御
    specs = [(i + 1, t) for i, t in enumerate(sorted(_PERSISTED_CONTENT_EVENT_TYPES))]
    run = _make_run(specs, watermark=len(specs))
    run.prune_persisted_events()
    assert all(event is None for event in run.events)


# ---- subscribe_run 遇 None 占位 ----


async def test_subscribe_run_skips_none_placeholders():
    """回放循环遇 None 占位：不死循环、占位跳过、后续事件正常送达；
    面板类事件（todo_update）按最新快照语义在追赶结束后统一回放"""
    service = AgentExecutorService()
    run = _make_run(
        [
            (1, "node_content"),  # 将被 prune 置 None
            (2, "todo_update"),  # 保留，面板类 → 缓冲后统一回放
            (3, "node_content"),  # 水位后，应回放
        ],
        watermark=1,
    )
    run.prune_persisted_events()
    assert run.events[0] is None
    run.done = True  # 回放完即返回，测试可终止
    service._agent_runs[run.session_id] = run

    received = []
    async for event in service.subscribe_run(run.session_id, run.run_id):
        received.append(event)

    # None 占位被跳过；内容类实时事件直发（id=3 先出），面板类缓冲在
    # 追赶结束后统一回放（id=2 后出）——仅面板类重排，无丢失
    assert [(e["id"], e["type"]) for e in received] == [
        (3, "node_content"),
        (2, "todo_update"),
    ]


async def test_subscribe_run_none_placeholder_before_watermark_filtered():
    """None 占位即使未被 prune（理论上不可能）也不绕过水位过滤——双保险验证：
    占位路径与水位过滤路径互不干扰，游标推进语义一致"""
    service = AgentExecutorService()
    run = _make_run(
        [
            (1, "node_content"),
            (2, "node_content"),
        ],
        watermark=2,
    )
    # 不 prune：两个内容类事件都在水位前，应被水位过滤跳过
    run.done = True
    service._agent_runs[run.session_id] = run

    received = []
    async for event in service.subscribe_run(run.session_id, run.run_id):
        received.append(event)
    assert received == []


async def test_subscribe_run_panel_snapshots_latest_only():
    """面板类事件回放快照化：同类型多条只回放最新一条（回放回声消除——
    历史 todo_update 全量重发会与 DB 落库行渲染的卡片成对重复）"""
    service = AgentExecutorService()
    run = _make_run(
        [
            (1, "todo_update"),
            (2, "node_content"),
            (3, "todo_update"),
            (4, "sub_agent_progress"),
            (5, "sub_agent_progress"),
        ],
        watermark=2,  # node_content(id=2) 被水位过滤
    )
    run.done = True
    service._agent_runs[run.session_id] = run

    received = []
    async for event in service.subscribe_run(run.session_id, run.run_id):
        received.append(event)

    # todo_update 只留 id=3；sub_agent_progress 只留 id=5；按 id 序回放
    assert [(e["id"], e["type"]) for e in received] == [
        (3, "todo_update"),
        (5, "sub_agent_progress"),
    ]


async def test_subscribe_run_live_panel_events_direct():
    """面板类事件实时直发不受快照缓冲影响：追赶结束（panel_flushed）后到达的
    todo_update 照常立即送达（实时流式建卡依赖此路径）"""
    service = AgentExecutorService()
    run = _make_run([(1, "todo_update")], watermark=1)
    service._agent_runs[run.session_id] = run

    async def publish_soon():
        # 订阅建立后经真实发布路径追加事件：_publish_agent_run_event 维护
        # next_event_id/events/订阅者唤醒，生成器从 wait() 醒来后走实时直发
        await asyncio.sleep(0.05)
        service._publish_agent_run_event(run, {"type": "todo_update", "data": {}})

    publisher = asyncio.ensure_future(publish_soon())
    received = []
    # 生成器消费到实时事件后即断开（生产语义：客户端收到所需数据主动关闭），
    # 显式关闭触发 finally 清理订阅者；不依赖 run.done（该路径与被测行为无关）
    gen = service.subscribe_run(run.session_id, run.run_id)
    async for event in gen:
        received.append(event)
        if event["id"] == 2:
            break
    await publisher
    await gen.aclose()

    # 追赶期缓冲的 id=1 快照先统一回放，实时到达的 id=2 直发
    assert [(e["id"], e["type"]) for e in received] == [
        (1, "todo_update"),
        (2, "todo_update"),
    ]


async def test_subscribe_run_live_wait_after_replay():
    """回放到 last_event_id 后 run 未结束：等待新事件（唤醒机制不因占位失效）"""
    service = AgentExecutorService()
    run = _make_run([(1, "node_content")], watermark=1)
    run.prune_persisted_events()
    service._agent_runs[run.session_id] = run

    async def finish_soon():
        await asyncio.sleep(0.05)
        run.done = True
        for wake in tuple(run.subscribers):
            wake.set()

    finisher = asyncio.ensure_future(finish_soon())
    received = []
    async for event in service.subscribe_run(run.session_id, run.run_id):
        received.append(event)
    await finisher
    assert received == []  # 唯一事件已 prune，回放为空后正常退出


# ---- 步长取模逻辑（纯逻辑断言，不跑真实 LLM） ----


def test_iteration_step_modulo():
    from app.agent_flow.node_handlers.llm_tool_handler import (
        REACT_MIDWAY_SAVE_EVERY_N_ITERATIONS,
    )

    n = REACT_MIDWAY_SAVE_EVERY_N_ITERATIONS
    assert n >= 1
    triggered = [i for i in range(1, 13) if i % n == 0]
    assert triggered == [5, 10]  # 每 N 轮触发一次

"""
Agent执行服务模块

与FlowExecutorService的区别：
1. 不创建Execution/NodeExecution记录
2. 使用session_id作为thread_id
3. 消息保存到AgentMessage表
4. 简化的流程验证（不需要input_schema/output_schema）
"""

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Optional, AsyncGenerator, Awaitable, Callable, Dict, Any, List

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.types import Command
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flow import Flow, FlowType
from app.models.flow_node import NodeType
from app.models.agent_session import AgentSession
from app.models.agent_message import AgentMessage
from app.agent_flow.file_change_context import (
    reset_file_change_context,
    set_file_change_context,
)
from app.agent_flow.flow_context import FlowContext
from app.agent_flow.flow_event import FlowEventFactory
from app.constants.node_types import NODE_TYPE_LABELS
from app.constants.timing import COMPLETED_RUN_RETENTION_SECONDS
from app.services.base_executor_service import BaseExecutorService
from app.services.agent_conversation_service import agent_conversation_service
from app.services.file_service import file_service
from app.services.interrupt_service import interrupt_service
from app.services.question_service import question_service
from app.services.tool_approval_service import tool_approval_service
from app.config.settings import settings
from app.utils.media_resolver import guess_mime_by_ext
from app.utils.message_utils import extract_text_content, extract_token_usage

logger = logging.getLogger(__name__)


def format_exception_message(e: BaseException) -> str:
    """格式化异常信息，str() 为空时回退到 repr() 以保留异常类名。"""
    msg = str(e).strip()
    return msg if msg else repr(e)


def normalize_work_dir(work_dir: Optional[str]) -> Optional[str]:
    """校验并规范化会话工作路径

    None/空串返回 None（表示未设置）；路径不存在或不是目录时抛 ValueError；
    有效路径返回 resolve() 后的规范化绝对路径。
    """
    if work_dir is None:
        return None
    stripped = work_dir.strip()
    if not stripped:
        return None
    path = Path(stripped).expanduser()
    if not path.exists():
        raise ValueError(f"路径不存在: {stripped}")
    if not path.is_dir():
        raise ValueError(f"路径不是目录: {stripped}")
    return str(path.resolve())


_RUN_END_EVENT_TYPES = frozenset({"error", "flow_done", "waiting_human"})
_PROGRESS_EVENT_TYPES = frozenset(
    {"node_start", "node_content", "tool_call_start", "node_done"}
)


@dataclass
class _AgentRun:
    """后台 Agent 执行及其可回放事件。"""

    run_id: str
    session_id: int
    approval_callback: Callable[[dict[str, Any]], None] | None = None
    event_callback: Callable[[dict[str, Any]], None] | None = None
    events: list[dict[str, Any]] = dataclass_field(default_factory=list)
    subscribers: set[asyncio.Event] = dataclass_field(default_factory=set)
    result_ready: asyncio.Event = dataclass_field(default_factory=asyncio.Event)
    result: dict[str, Any] | None = None
    failure_status: str | None = None
    failure_error: str | None = None
    task: asyncio.Task[None] | None = None
    started: bool = False
    cancel_requested: bool = False
    finalizing: bool = False
    done: bool = False
    completed_at: float | None = None
    terminal_event_type: str | None = None
    next_event_id: int = 1

    @property
    def last_event_id(self) -> int:
        return self.next_event_id - 1


class AgentExecutorService(BaseExecutorService):
    """
    Agent执行服务

    复用流程编排能力，但不创建执行记录
    对话历史保存到AgentMessage表
    """

    def __init__(self):
        super().__init__()
        self._compressing_sessions: set[int] = set()
        self._compress_results: dict[int, dict] = {}
        self._running_sessions: set[int] = set()
        self._waiting_sessions: set[int] = set()
        self._waiting_events: dict[int, dict] = {}
        self._pending_save_sessions: set[int] = set()
        self._streaming_tasks: Dict[str, asyncio.Task] = {}
        self._compression_tasks: dict[int, asyncio.Task[None]] = {}
        self._direct_streaming_tasks: dict[int, set[asyncio.Task]] = {}
        self._agent_runs: dict[int, _AgentRun] = {}
        self._shutting_down = False

    def _prune_agent_runs(self) -> None:
        """清理已结束且超过回放保留时间的执行。"""
        now = time.monotonic()
        stale_session_ids = [
            session_id
            for session_id, run in self._agent_runs.items()
            if run.done
            and session_id not in self._waiting_sessions
            and run.completed_at is not None
            and now - run.completed_at > COMPLETED_RUN_RETENTION_SECONDS
        ]
        for session_id in stale_session_ids:
            self._agent_runs.pop(session_id, None)

    def _publish_agent_run_event(self, run: _AgentRun, event: dict) -> None:
        """记录事件并唤醒当前订阅者。"""
        stored_event = {
            "id": run.next_event_id,
            "type": event.get("type", "unknown"),
            "data": dict(event.get("data") or {}),
        }
        run.next_event_id += 1
        run.events.append(stored_event)
        if stored_event["type"] == "tool_approval_required" and run.approval_callback:
            try:
                run.approval_callback(stored_event)
            except Exception:
                logger.warning(
                    "转发子Agent工具审批事件失败: session_id=%s, run_id=%s",
                    run.session_id,
                    run.run_id,
                    exc_info=True,
                )
        if stored_event["type"] in _PROGRESS_EVENT_TYPES and run.event_callback:
            try:
                run.event_callback(stored_event)
            except Exception:
                logger.warning(
                    "转发子Agent进度事件失败: session_id=%s, run_id=%s",
                    run.session_id,
                    run.run_id,
                    exc_info=True,
                )
        for wake_event in tuple(run.subscribers):
            wake_event.set()

    @staticmethod
    def _build_agent_run_result(terminal_event: dict) -> dict[str, Any]:
        """将结束事件转换为不依赖 SSE 消费的执行结果。"""
        event_type = terminal_event.get("type", "error")
        raw_data = terminal_event.get("data")
        data = dict(raw_data) if isinstance(raw_data, dict) else {}

        if event_type == "flow_done":
            raw_output_data = data.get("output_data")
            output_data = (
                dict(raw_output_data) if isinstance(raw_output_data, dict) else {}
            )
            return {
                "status": str(data.get("status") or "failed"),
                "output_data": output_data,
                "error": None,
                "waiting_data": None,
            }

        if event_type == "waiting_human":
            return {
                "status": "waiting_human",
                "output_data": {},
                "error": None,
                "waiting_data": data,
            }

        return {
            "status": "error",
            "output_data": {},
            "error": str(data.get("message") or "Agent 执行失败"),
            "waiting_data": None,
        }

    @staticmethod
    async def _await_cancellation_safe(
        awaitable: Awaitable[Any],
    ) -> BaseException | None:
        """即使外层任务被重复取消，也等待清理 awaitable 真正结束。"""
        cleanup_task = asyncio.ensure_future(awaitable)
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                continue
        try:
            cleanup_task.result()
        except (Exception, asyncio.CancelledError) as exc:
            return exc
        return None

    def _finalize_agent_run(self, run: _AgentRun, terminal_event: dict) -> None:
        """原子发布结束事件并释放所有订阅者。"""
        if run.done:
            return
        result = self._build_agent_run_result(terminal_event)
        if result["status"] == "success" and run.failure_status:
            result["status"] = run.failure_status
            result["error"] = run.failure_error
        run.terminal_event_type = terminal_event.get("type", "error")
        if run.terminal_event_type == "waiting_human":
            self._waiting_sessions.add(run.session_id)
            self._waiting_events[run.session_id] = terminal_event
        try:
            self._publish_agent_run_event(run, terminal_event)
        finally:
            run.approval_callback = None
            run.event_callback = None
            run.result = result
            run.done = True
            run.completed_at = time.monotonic()
            run.result_ready.set()
            for wake_event in tuple(run.subscribers):
                wake_event.set()

    async def _consume_agent_run(
        self,
        run: _AgentRun,
        stream_generator: AsyncGenerator[Dict[str, Any], None],
    ) -> None:
        """独立于 SSE 连接消费执行流，并将事件写入会话回放缓冲。"""
        run.started = True
        terminal_event: dict | None = None
        # 文件变更追踪上下文：本 task 内执行的文件写入工具据此记录变更，
        # 供回退消息时恢复文件（子 Agent run 为独立 task，会覆盖为自己的 session）
        ctx_token = set_file_change_context(run.session_id, run.run_id)
        try:
            async for event in stream_generator:
                event_type = event.get("type")
                if event_type == "tool_call_limit":
                    event_data = event.get("data") or {}
                    max_iterations = event_data.get("max_iterations")
                    node_key = event_data.get("node_key") or "未知节点"
                    run.failure_status = "tool_call_limit"
                    run.failure_error = (
                        f"子Agent节点 {node_key} 超过最大工具调用次数: {max_iterations}"
                    )
                if event_type in _RUN_END_EVENT_TYPES:
                    terminal_event = event
                    break
                else:
                    self._publish_agent_run_event(run, event)
        except asyncio.CancelledError:
            terminal_event = FlowEventFactory.flow_done(
                execution_id=run.session_id,
                output_data={},
                status="cancelled",
            )
        except Exception as exc:
            logger.exception(
                "后台 Agent 执行异常: session_id=%s, run_id=%s",
                run.session_id,
                run.run_id,
            )
            terminal_event = FlowEventFactory.error(
                f"执行失败: {format_exception_message(exc)}"
            )
        finally:
            run.finalizing = True
            try:
                cleanup_error = await self._await_cancellation_safe(
                    stream_generator.aclose()
                )
                if cleanup_error:
                    raise cleanup_error
            except BaseException:
                logger.debug("关闭 Agent 执行流失败", exc_info=True)
            finally:
                if terminal_event is None:
                    terminal_event = FlowEventFactory.error("Agent 执行未返回结束事件")

                if run.cancel_requested:
                    if terminal_event.get("type") == "waiting_human":
                        cleanup_error = await self._await_cancellation_safe(
                            self._cleanup_thread_checkpoint(run.session_id)
                        )
                        if cleanup_error:
                            logger.warning(f"取消时清理checkpoint失败: {cleanup_error}")
                    self._waiting_sessions.discard(run.session_id)
                    self._waiting_events.pop(run.session_id, None)
                    interrupt_service.clear_agent_interrupted(run.session_id)
                    terminal_event = FlowEventFactory.flow_done(
                        execution_id=run.session_id,
                        output_data={},
                        status="cancelled",
                    )
                self._finalize_agent_run(run, terminal_event)
        # 外层 finally 体末尾：无论结果如何都恢复文件变更追踪上下文
        reset_file_change_context(ctx_token)

    def _start_agent_run(
        self,
        session_id: int,
        stream_factory: Callable[[], AsyncGenerator[Dict[str, Any], None]],
        *,
        allow_waiting: bool = False,
        approval_callback: Callable[[dict[str, Any]], None] | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        """启动单个会话的后台执行并返回 run_id。"""
        if self._shutting_down:
            raise ValueError("服务正在关闭，无法启动 Agent 执行")
        self._prune_agent_runs()
        current_run = self._agent_runs.get(session_id)
        if current_run and not current_run.done:
            raise ValueError("会话正在执行中，请稍后再发送消息")
        if session_id in self._running_sessions:
            raise ValueError("会话正在执行中，请稍后再发送消息")
        if session_id in self._compressing_sessions:
            raise ValueError("正在压缩上下文，请稍后再发送消息")
        if session_id in self._waiting_sessions and not allow_waiting:
            raise ValueError("会话正在等待人工输入，请使用恢复执行")

        run = _AgentRun(
            run_id=uuid.uuid4().hex,
            session_id=session_id,
            approval_callback=approval_callback,
            event_callback=event_callback,
        )
        self._agent_runs[session_id] = run
        stream_generator = stream_factory()
        task = asyncio.create_task(self._consume_agent_run(run, stream_generator))
        run.task = task

        task_key = str(session_id)
        self._streaming_tasks[task_key] = task

        def remove_completed_task(completed_task: asyncio.Task) -> None:
            if self._streaming_tasks.get(task_key) is completed_task:
                self._streaming_tasks.pop(task_key, None)
            if not run.done and not run.finalizing:
                if completed_task.cancelled():
                    terminal_event = FlowEventFactory.flow_done(
                        execution_id=session_id,
                        output_data={},
                        status="cancelled",
                    )
                else:
                    error = completed_task.exception()
                    terminal_event = FlowEventFactory.error(
                        f"执行失败: {format_exception_message(error)}"
                        if error
                        else "Agent 执行异常结束"
                    )
                self._waiting_sessions.discard(session_id)
                self._pending_save_sessions.discard(session_id)
                self._finalize_agent_run(run, terminal_event)

        task.add_done_callback(remove_completed_task)
        return run.run_id

    def start_chat_run(
        self,
        session_id: int,
        user_message: str,
        params: dict | None = None,
        *,
        model: str | None = None,
        approval_callback: Callable[[dict[str, Any]], None] | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        """启动后台 Agent 对话。"""
        return self._start_agent_run(
            session_id,
            lambda: self.chat_stream(
                session_id,
                user_message,
                dict(params or {}),
                model=model,
                _managed_run=True,
            ),
            approval_callback=approval_callback,
            event_callback=event_callback,
        )

    def start_resume_run(self, session_id: int, human_input: str) -> str:
        """启动后台 Agent 人工恢复。"""
        current_run = self._agent_runs.get(session_id)
        if current_run and current_run.done and not self.is_waiting(session_id):
            raise ValueError("会话当前没有等待中的人工输入")
        return self._start_agent_run(
            session_id,
            lambda: self.resume_stream(
                session_id,
                human_input,
                _managed_run=True,
            ),
            allow_waiting=True,
        )

    async def cancel_run(
        self,
        session_id: int,
        *,
        expected_run_id: str | None = None,
    ) -> bool:
        """标记并取消当前托管执行，由 Runner 完成保存和结束事件。"""
        run = self._agent_runs.get(session_id)
        if not run or not run.task:
            return False
        if expected_run_id is not None and run.run_id != expected_run_id:
            return False
        if run.done and not self.is_waiting(session_id):
            return False

        interrupt_service.set_agent_interrupted(session_id)
        from app.services.tool_approval_service import tool_approval_service

        tool_approval_service.cancel(session_id)
        self._pending_save_sessions.add(session_id)

        if run.done:
            if session_id in self._running_sessions:
                return True
            self._running_sessions.add(session_id)
            try:
                cleanup_error = await self._await_cancellation_safe(
                    self._cleanup_thread_checkpoint(session_id)
                )
                if cleanup_error:
                    logger.warning(f"取消等待状态时清理checkpoint失败: {cleanup_error}")
            finally:
                self._waiting_sessions.discard(session_id)
                self._waiting_events.pop(session_id, None)
                interrupt_service.clear_agent_interrupted(session_id)
                self._pending_save_sessions.discard(session_id)
                self._running_sessions.discard(session_id)
                if self._agent_runs.get(session_id) is run:
                    self._agent_runs.pop(session_id, None)
            return True
        was_started = run.started
        run.cancel_requested = True
        run.finalizing = not run.started
        self._waiting_sessions.discard(session_id)
        self._waiting_events.pop(session_id, None)
        if not run.task.done():
            run.task.cancel()
        if not was_started:
            cleanup_error = await self._await_cancellation_safe(
                self._cleanup_thread_checkpoint(session_id)
            )
            if cleanup_error:
                logger.warning(f"取消时清理checkpoint失败: {cleanup_error}")
            interrupt_service.clear_agent_interrupted(session_id)
            self._pending_save_sessions.discard(session_id)
            self._finalize_agent_run(
                run,
                FlowEventFactory.flow_done(
                    execution_id=session_id,
                    output_data={},
                    status="cancelled",
                ),
            )
        return True

    async def wait_run_result(
        self,
        session_id: int,
        run_id: str,
    ) -> dict[str, Any]:
        """等待托管执行完成，但不消费或订阅其 SSE 事件。"""
        self._prune_agent_runs()
        run = self._agent_runs.get(session_id)
        if not run or run.run_id != run_id:
            raise ValueError("Agent 执行不存在或结果已过期")

        if not run.done:
            await run.result_ready.wait()

        result = run.result
        if result is None:
            raise RuntimeError("Agent 执行已结束但没有最终结果")

        raw_output_data = result.get("output_data")
        raw_waiting_data = result.get("waiting_data")
        return {
            "status": result.get("status", "error"),
            "output_data": (
                dict(raw_output_data) if isinstance(raw_output_data, dict) else {}
            ),
            "error": result.get("error"),
            "waiting_data": (
                dict(raw_waiting_data) if isinstance(raw_waiting_data, dict) else None
            ),
        }

    async def subscribe_run(
        self,
        session_id: int,
        run_id: str,
        after_event_id: int = 0,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """订阅后台执行；断线重连时回放游标之后的事件。"""
        self._prune_agent_runs()
        run = self._agent_runs.get(session_id)
        if not run or run.run_id != run_id:
            yield FlowEventFactory.error("Agent 执行不存在或事件回放已过期")
            return

        cursor = max(after_event_id, 0)
        wake_event = asyncio.Event()
        run.subscribers.add(wake_event)
        try:
            while True:
                wake_event.clear()
                # 事件 ID 从 1 连续递增，cursor 同时是下一事件的列表索引。
                while cursor < run.last_event_id:
                    event = run.events[cursor]
                    cursor = event["id"]
                    if event["type"] in (
                        "tool_approval_required",
                        "question_request",
                    ):
                        # 回放的「等待用户响应」事件以服务端实时剩余时间标注（存量
                        # 事件是发布时的静态副本），避免刷新重连后前端倒计时与后端
                        # 死线失同步。子Agent 审批的死线在子会话的等待句柄上，按
                        # data.sub_session_id 解析；等待已终结（无句柄）时不标注，
                        # 前端回退默认倒计时
                        data = event["data"]
                        approval_session_id = (
                            int(data.get("sub_session_id") or 0)
                            if data.get("is_sub_agent")
                            else session_id
                        )
                        remaining = tool_approval_service.remaining_seconds(
                            approval_session_id
                        ) if event["type"] == "tool_approval_required" else (
                            question_service.remaining_seconds(approval_session_id)
                        )
                        if remaining is not None:
                            event = {
                                **event,
                                "data": {**data, "expires_in": remaining},
                            }
                    yield event
                if run.done:
                    return
                await wake_event.wait()
        finally:
            run.subscribers.discard(wake_event)

    def emit_file_change(self, session_id: int, change_dict: dict) -> None:
        """工具埋点：写入文件后向当前订阅者推送 file_changed 事件。

        由 agent_file_change_service.record_change 调用；无活跃 run 时静默忽略
        （如页面刷新后历史回退不会触发侧栏实时更新）。
        """
        self._prune_agent_runs()
        run = self._agent_runs.get(session_id)
        if not run or run.done:
            return
        event = FlowEventFactory.file_changed(change=change_dict)
        self._publish_agent_run_event(run, event)

    def get_run_status(self, session_id: int) -> dict[str, Any]:
        """返回会话运行状态及当前可订阅游标。"""
        self._prune_agent_runs()
        run = self._agent_runs.get(session_id)
        managed_running = bool(run and not run.done)
        return {
            "running": managed_running or session_id in self._running_sessions,
            "managed_running": managed_running,
            "waiting_human": session_id in self._waiting_sessions,
            "run_id": run.run_id if run else None,
            "last_event_id": run.last_event_id if run else 0,
            "terminal_event_type": run.terminal_event_type if run else None,
            "waiting_event": self._waiting_events.get(session_id),
        }

    def is_waiting(self, session_id: int) -> bool:
        """检查会话是否停在 LangGraph 人工输入中断点。"""
        return session_id in self._waiting_sessions

    def start_compress_background(
        self, session_id: int, custom_prompt: str = ""
    ) -> None:
        """原子预占会话并启动手动上下文压缩。"""
        if self._shutting_down:
            raise ValueError("服务正在关闭，无法压缩上下文")
        if session_id in self._compressing_sessions:
            raise ValueError("正在压缩中，请稍后再试")
        if self.is_running(session_id):
            raise ValueError("会话正在执行中，请稍后再试")
        if self.is_waiting(session_id):
            raise ValueError("会话正在等待人工输入，无法压缩上下文")

        self._compressing_sessions.add(session_id)
        task = asyncio.create_task(
            self._run_compress_background(session_id, custom_prompt)
        )
        self._compression_tasks[session_id] = task

        def release_reservation(completed_task: asyncio.Task) -> None:
            self._compressing_sessions.discard(session_id)
            if self._compression_tasks.get(session_id) is completed_task:
                self._compression_tasks.pop(session_id, None)

        task.add_done_callback(release_reservation)

    async def shutdown_runs(self) -> None:
        """应用退出时停止所有后台 Agent 执行。"""
        self._shutting_down = True
        run_tasks = [
            run.task for run in self._agent_runs.values() if run.task and not run.done
        ]
        direct_tasks = {
            task for tasks in self._direct_streaming_tasks.values() for task in tasks
        }
        tasks = list({*run_tasks, *self._compression_tasks.values(), *direct_tasks})
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _validate_agent_flow(self, flow: Flow) -> None:
        """
        验证Agent流程结构

        检查：
        1. 流程类型为 agent
        2. 节点类型白名单
        3. start/end/llm 唯一性
        4. 工具边连接规则（工具节点→llm via tools handle）

        Args:
            flow: Flow对象

        Raises:
            ValueError: 流程结构不合法时抛出
        """
        if flow.flow_type != FlowType.AGENT.value:
            raise ValueError(f"流程类型不是agent: {flow.flow_type}")

        from app.models.flow_node import (
            AGENT_ALLOWED_NODE_TYPES,
            AGENT_TOOL_NODE_TYPES,
            NodeType,
        )

        if not flow.nodes:
            raise ValueError("智能体流程没有节点")

        type_labels = NODE_TYPE_LABELS
        for node in flow.nodes:
            if node.node_type not in AGENT_ALLOWED_NODE_TYPES:
                label = type_labels.get(node.node_type, node.node_type)
                raise ValueError(f"智能体不支持「{label}」类型的节点")

            if node.node_type == NodeType.SUB_AGENT.value:
                agent_id = (node.base_config or {}).get("agent_id")
                if agent_id and int(agent_id) == flow.id:
                    raise ValueError("子Agent节点不能引用当前Agent自身")

        start_nodes = [n for n in flow.nodes if n.node_type == NodeType.START.value]
        end_nodes = [n for n in flow.nodes if n.node_type == NodeType.END.value]
        llm_nodes = [n for n in flow.nodes if n.node_type == NodeType.LLM.value]

        if not start_nodes:
            raise ValueError("智能体缺少开始节点")
        if len(start_nodes) > 1:
            raise ValueError("智能体只能有一个开始节点")
        if not end_nodes:
            raise ValueError("智能体缺少结束节点")
        if len(end_nodes) > 1:
            raise ValueError("智能体只能有一个结束节点")
        if not llm_nodes:
            raise ValueError("智能体缺少大模型调用节点")
        if len(llm_nodes) > 1:
            raise ValueError("智能体只能有一个大模型调用节点")

        if not flow.edges:
            raise ValueError("智能体流程没有连接")

        node_map = {n.node_key: n.node_type for n in flow.nodes}
        for edge in flow.edges:
            source_type = node_map.get(edge.source_node_key, "")
            target_type = node_map.get(edge.target_node_key, "")
            if edge.source_handle == "tools":
                if source_type not in AGENT_TOOL_NODE_TYPES:
                    label = type_labels.get(source_type, source_type)
                    raise ValueError(
                        f"智能体模式下「{label}」节点不能作为工具连接到 LLM"
                    )
                if target_type != NodeType.LLM.value:
                    raise ValueError("智能体模式下工具节点只能连接到大模型调用节点")

    async def _get_session(
        self, db: AsyncSession, session_id: int
    ) -> Optional[AgentSession]:
        """获取会话"""
        query = select(AgentSession).where(
            AgentSession.id == session_id, AgentSession.is_delete == 0
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_sessions(
        self, db: AsyncSession, flow_id: int, page: int = 1, page_size: int = 20
    ) -> tuple[List[AgentSession], int]:
        """
        获取会话列表（分页）

        Args:
            db: 数据库会话
            flow_id: Agent Flow ID
            page: 页码
            page_size: 每页数量

        Returns:
            tuple[List[AgentSession], int]: 会话列表和总数
        """
        # 计算总数
        count_query = (
            select(func.count())
            .select_from(AgentSession)
            .where(AgentSession.flow_id == flow_id, AgentSession.is_delete == 0)
        )
        count_result = await db.execute(count_query)
        total = count_result.scalar() or 0

        # 分页查询
        offset = (page - 1) * page_size
        query = (
            select(AgentSession)
            .where(AgentSession.flow_id == flow_id, AgentSession.is_delete == 0)
            .order_by(AgentSession.id.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await db.execute(query)
        sessions = list(result.scalars().all())

        return sessions, total

    async def create_session(
        self,
        db: AsyncSession,
        flow_id: int,
        gateway_id: Optional[int] = None,
        work_dir: Optional[str] = None,
    ) -> AgentSession:
        """创建新会话（公开方法）

        Args:
            gateway_id: 可选，由 网关触发创建时传入，用于区分 网关会话与用户聊天会话
            work_dir: 可选，会话级项目工作路径（需先经 normalize_work_dir 校验）
        """
        return await self._create_session(db, flow_id, gateway_id, work_dir=work_dir)

    async def update_work_dir(
        self, db: AsyncSession, session_id: int, work_dir: Optional[str]
    ) -> Optional[AgentSession]:
        """更新会话工作路径；work_dir 为 None/空串时清除（回退 Agent 默认工作目录）"""
        session = await self._get_session(db, session_id)
        if not session:
            return None
        session.work_dir = normalize_work_dir(work_dir)
        await db.commit()
        await db.refresh(session)
        return session

    async def get_or_create_sub_agent_session(
        self,
        db: AsyncSession,
        flow_id: int,
        parent_session_id: int,
        parent_node_key: str,
    ) -> AgentSession:
        """获取同一父会话与节点对应的子Agent会话，不存在时创建。"""
        query = (
            select(AgentSession)
            .where(
                AgentSession.flow_id == flow_id,
                AgentSession.parent_session_id == parent_session_id,
                AgentSession.parent_node_key == parent_node_key,
                AgentSession.is_delete == 0,
            )
            .order_by(AgentSession.id.asc())
            .limit(1)
        )
        result = await db.execute(query)
        session = result.scalar_one_or_none()
        if session:
            return session

        session = await self._create_session(
            db,
            flow_id,
            parent_session_id=parent_session_id,
            parent_node_key=parent_node_key,
        )
        return session

    async def search_history(
        self, db: AsyncSession, flow_id: int, keyword: str
    ) -> dict:
        """搜索会话标题和消息内容

        Args:
            db: 数据库会话
            flow_id: Agent Flow ID
            keyword: 搜索关键词

        Returns:
            {"sessions": [...], "messages": [...]}
        """
        search_pattern = f"%{keyword}%"

        # 搜索会话标题
        session_query = (
            select(
                AgentSession.id,
                AgentSession.title,
                AgentSession.create_time,
            )
            .where(
                AgentSession.flow_id == flow_id,
                AgentSession.is_delete == 0,
                AgentSession.title.like(search_pattern),
            )
            .order_by(AgentSession.id.desc())
            .limit(20)
        )
        session_result = await db.execute(session_query)
        sessions = [
            {
                "id": row.id,
                "title": row.title,
                "create_time": str(row.create_time) if row.create_time else "",
            }
            for row in session_result.all()
        ]

        # 搜索消息内容（JOIN agent_session 过滤 flow_id）
        msg_query = (
            select(
                AgentMessage.id,
                AgentMessage.session_id,
                AgentMessage.role,
                AgentMessage.content,
                AgentSession.title.label("session_title"),
                AgentMessage.create_time,
            )
            .join(AgentSession, AgentMessage.session_id == AgentSession.id)
            .where(
                AgentSession.flow_id == flow_id,
                AgentSession.is_delete == 0,
                AgentMessage.is_delete == 0,
                AgentMessage.role.in_(["human", "ai"]),
                (
                    AgentMessage.content.like(search_pattern)
                    | AgentMessage.original_content.like(search_pattern)
                ),
            )
            .order_by(AgentMessage.id.desc())
            .limit(50)
        )
        msg_result = await db.execute(msg_query)
        messages = [
            {
                "id": row.id,
                "session_id": row.session_id,
                "session_title": row.session_title,
                "role": row.role,
                "content_preview": (row.content or "")[:200],
                "create_time": str(row.create_time) if row.create_time else "",
            }
            for row in msg_result.all()
        ]

        return {"sessions": sessions, "messages": messages}

    async def _cleanup_thread_checkpoint(self, session_id: int) -> None:
        """清理会话对应的 LangGraph checkpoint，确保下次执行从 DB 重建历史"""
        try:
            thread_id = f"agent_{session_id}"
            await self._checkpointer.adelete_thread(thread_id)
        except Exception as e:
            logger.warning(f"清理checkpoint失败: {e}")

    async def delete_session(self, db: AsyncSession, session_id: int) -> bool:
        session = await self._get_session(db, session_id)
        if not session:
            return False
        session.is_delete = 1
        await db.execute(
            update(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .values(is_delete=1)
        )
        await db.commit()

        await self._cleanup_thread_checkpoint(session_id)
        self._waiting_sessions.discard(session_id)
        self._waiting_events.pop(session_id, None)

        # 同步清理该会话的文件变更记录与备份
        from app.services.agent_file_change_service import agent_file_change_service

        await agent_file_change_service.delete_session_changes(db, session_id)

        return True

    async def delete_messages_from(
        self,
        db: AsyncSession,
        session_id: int,
        message_id: int,
        restore_files: bool = True,
    ) -> Optional[dict]:
        """
        删除指定消息及之后的所有消息，返回被删除的用户消息内容

        Args:
            db: 数据库会话
            session_id: 会话ID
            message_id: 起始消息ID（该消息及之后的所有消息都会被删除）
            restore_files: 是否同步恢复该范围内的文件变更（回退消息并恢复文件）

        Returns:
            被删除的第一条用户消息的 {content, files, input_data}（用于回退恢复）
            及 reverted_files（文件恢复结果），消息不存在返回 None
        """
        session = await self._get_session(db, session_id)
        if not session:
            return None

        query = (
            select(AgentMessage)
            .where(
                AgentMessage.session_id == session_id,
                AgentMessage.is_delete == 0,
                AgentMessage.id >= message_id,
            )
            .order_by(AgentMessage.id.asc())
        )
        result = await db.execute(query)
        messages_to_delete = list(result.scalars().all())

        if not messages_to_delete:
            return None

        user_message_content = ""
        user_files: Optional[list] = None
        user_input_data: Optional[dict] = None
        for msg in messages_to_delete:
            if msg.role == "human":
                user_message_content = msg.original_content or msg.content
                user_files = msg.files if isinstance(msg.files, list) else None
                user_input_data = (
                    msg.input_data if isinstance(msg.input_data, dict) else None
                )
                break

        if user_files:
            enriched_files = []
            for item in user_files:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                file_obj = await file_service.get_by_id(
                    db, int(item["id"]), raise_not_found=False
                )
                if file_obj:
                    enriched_files.append(
                        {
                            **item,
                            "id": file_obj.id,
                            "original_name": file_obj.original_name,
                            "file_path": file_obj.file_path,
                            "file_type": file_obj.file_type,
                            "file_size": file_obj.file_size,
                            "mime_type": file_obj.mime_type,
                            "preview_url": file_obj.preview_url
                            or f"/{file_obj.file_path}",
                        }
                    )
                else:
                    enriched_files.append(item)
            user_files = enriched_files

        message_ids = [msg.id for msg in messages_to_delete]
        await db.execute(
            update(AgentMessage)
            .where(AgentMessage.id.in_(message_ids))
            .values(is_delete=1)
        )
        await db.commit()

        await self._cleanup_thread_checkpoint(session_id)
        self._waiting_sessions.discard(session_id)
        self._waiting_events.pop(session_id, None)

        # 文件恢复：边界为锚点前最后一条保留消息的创建时间（工具执行晚于上一轮落库）
        reverted_files: list = []
        if restore_files:
            from app.services.agent_file_change_service import agent_file_change_service

            boundary = await agent_file_change_service.get_changes_boundary(
                db, session_id, message_id
            )
            reverted_files = await agent_file_change_service.revert_changes(
                db, session_id, since_time=boundary
            )

        return {
            "content": user_message_content,
            "files": user_files,
            "input_data": user_input_data,
            "reverted_files": reverted_files,
        }

    async def get_revert_preview(
        self, db: AsyncSession, session_id: int, message_id: int
    ) -> Optional[dict]:
        """预览回退到指定消息时将恢复的文件清单（不去重前按文件聚合）"""
        from app.services.agent_file_change_service import agent_file_change_service

        session = await self._get_session(db, session_id)
        if not session:
            return None
        boundary = await agent_file_change_service.get_changes_boundary(
            db, session_id, message_id
        )
        changes = await agent_file_change_service.get_changes_since(
            db, session_id, since_time=boundary
        )
        return {
            "files": agent_file_change_service.summarize_changes(changes),
        }

    async def restore_files_only(
        self, db: AsyncSession, session_id: int, message_id: int
    ) -> Optional[list]:
        """仅恢复文件变更（保留对话消息不动）"""
        from app.services.agent_file_change_service import agent_file_change_service

        session = await self._get_session(db, session_id)
        if not session:
            return None
        boundary = await agent_file_change_service.get_changes_boundary(
            db, session_id, message_id
        )
        return await agent_file_change_service.revert_changes(
            db, session_id, since_time=boundary
        )

    async def get_messages(
        self,
        db: AsyncSession,
        session_id: int,
        limit: int = 0,
        before_id: Optional[int] = None,
        after_id: Optional[int] = None,
    ) -> tuple[List[AgentMessage], int]:
        """
        获取会话消息历史（公开方法），支持分页

        Args:
            db: 数据库会话
            session_id: 会话ID
            limit: 最大消息数，0 表示不限制
            before_id: 分页游标，返回此 ID 之前的消息（不含 before_id 本身）
            after_id: 增量游标，返回此 ID 之后的消息（不含 after_id 本身）

        Returns:
            tuple[List[AgentMessage], int]: 消息列表和总数
        """
        messages = await self._get_messages(db, session_id, limit, before_id, after_id)
        total = await self._get_messages_count(db, session_id)
        return messages, total

    async def _create_session(
        self,
        db: AsyncSession,
        flow_id: int,
        gateway_id: Optional[int] = None,
        parent_session_id: Optional[int] = None,
        parent_node_key: Optional[str] = None,
        work_dir: Optional[str] = None,
    ) -> AgentSession:
        """创建新会话"""
        session = AgentSession(
            flow_id=flow_id,
            title="新对话",
            status=1,
            gateway_id=gateway_id,
            parent_session_id=parent_session_id,
            parent_node_key=parent_node_key,
            work_dir=work_dir,
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session

    async def _get_messages(
        self,
        db: AsyncSession,
        session_id: int,
        limit: int = 0,
        before_id: Optional[int] = None,
        after_id: Optional[int] = None,
    ) -> List[AgentMessage]:
        """获取会话消息历史，支持 before_id 分页游标与 after_id 增量游标"""
        query = select(AgentMessage).where(
            AgentMessage.session_id == session_id, AgentMessage.is_delete == 0
        )
        if before_id is not None:
            query = query.where(AgentMessage.id < before_id)
        elif after_id is not None:
            query = query.where(AgentMessage.id > after_id)
        query = query.order_by(AgentMessage.id.desc())
        if limit > 0:
            query = query.limit(limit)
        result = await db.execute(query)
        items = list(result.scalars().all())
        items.reverse()
        return items

    async def _get_messages_count(self, db: AsyncSession, session_id: int) -> int:
        """获取会话消息总数"""
        query = select(func.count(AgentMessage.id)).where(
            AgentMessage.session_id == session_id, AgentMessage.is_delete == 0
        )
        result = await db.execute(query)
        return result.scalar() or 0

    async def _save_message(
        self,
        db: AsyncSession,
        session_id: int,
        role: str,
        content: str,
        thinking: Optional[str] = None,
        tool_calls: Optional[dict] = None,
        tool_call_id: Optional[str] = None,
    ) -> AgentMessage:
        """保存单条消息"""
        query = select(func.max(AgentMessage.sequence)).where(
            AgentMessage.session_id == session_id, AgentMessage.is_delete == 0
        )
        result = await db.execute(query)
        max_seq = result.scalar()
        next_seq = (max_seq or -1) + 1

        kwargs: dict = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "sequence": next_seq,
        }
        if thinking is not None:
            kwargs["thinking"] = thinking
        if tool_calls is not None:
            kwargs["tool_calls"] = tool_calls
        if tool_call_id is not None:
            kwargs["tool_call_id"] = tool_call_id

        message = AgentMessage(**kwargs)
        db.add(message)
        await db.commit()
        await db.refresh(message)
        return message

    async def _resolve_input_params(
        self,
        db: AsyncSession,
        params: dict,
        input_schema: dict,
        input_data: dict,
    ) -> list[dict]:
        """
        解析 input_schema 参数，将文件信息解析为文件路径，同时收集附件元信息

        支持两种输入格式：
        1. 完整文件信息数组 [{id, file_path, ...}] — 直接使用，补充绝对路径
        2. 纯 ID 数组 [1, 2, ...] — 查询 DB 补全文件信息

        直接在传入的 input_data 上添加字段，不会覆盖已有数据。

        Returns:
            pending_files: 附件元信息列表
        """
        pending_files: list[dict] = []
        fields = input_schema.get("fields", [])

        for field in fields:
            field_name = field.get("name")
            field_type = field.get("type")
            if not field_name or field_name not in params:
                continue
            if field_type == "file_list":
                raw_value = params[field_name]
                if not isinstance(raw_value, list) or len(raw_value) == 0:
                    continue

                # 判断是完整文件信息还是纯 ID 列表
                if isinstance(raw_value[0], dict):
                    resolved = []
                    for item in raw_value:
                        fid = item.get("id")
                        if not fid:
                            continue
                        file_path = item.get("file_path", "")
                        # 缺 file_path 时按 id 从 DB 补全（回退恢复的附件仅带 id 与元信息）
                        if not file_path:
                            try:
                                (
                                    path,
                                    original_name,
                                    mime_type,
                                ) = await file_service.get_download_path(db, fid)
                                file_path = str(path)
                                if not item.get("original_name"):
                                    item["original_name"] = original_name
                                if not item.get("mime_type"):
                                    item["mime_type"] = mime_type
                            except FileNotFoundError:
                                continue
                        if file_path and not file_path.startswith("/"):
                            abs_path = settings.get_absolute_path(file_path)
                            item["file_path"] = str(abs_path) if abs_path else file_path
                        mime_type = item.get("mime_type", "") or guess_mime_by_ext(
                            item.get("original_name", "") or file_path
                        )
                        resolved.append(item)
                        pending_files.append(
                            {
                                "id": fid,
                                "original_name": item.get("original_name", ""),
                                "mime_type": mime_type,
                            }
                        )
                    if resolved:
                        input_data[field_name] = resolved
                else:
                    # 纯 ID 列表，查 DB 补全（向后兼容）
                    resolved = []
                    for fid in raw_value:
                        try:
                            (
                                path,
                                original_name,
                                mime_type,
                            ) = await file_service.get_download_path(db, fid)
                            resolved.append(
                                {
                                    "id": fid,
                                    "file_path": str(path),
                                    "mime_type": mime_type,
                                    "original_name": original_name,
                                }
                            )
                            pending_files.append(
                                {
                                    "id": fid,
                                    "original_name": original_name,
                                    "mime_type": mime_type,
                                }
                            )
                        except FileNotFoundError:
                            pass
                    if resolved and len(resolved) > 0:
                        input_data[field_name] = resolved
            else:
                input_data[field_name] = params[field_name]

        return pending_files

    async def chat_stream(
        self,
        session_id: int,
        user_message: str,
        params: dict | None = None,
        *,
        model: str | None = None,
        _managed_run: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        执行Agent对话（流式）

        Args:
            session_id: 会话ID
            user_message: 用户消息
            params: 额外参数
            model: 临时覆盖 LLM 模型（仅同供应商内，capabilities 等按模型元数据联动）

        Yields:
            SSE事件字典
        """
        from app.config.database import AsyncSessionLocal

        if self._shutting_down:
            yield FlowEventFactory.error("服务正在关闭，无法启动 Agent 执行")
            return

        db: AsyncSession | None = None
        direct_task: asyncio.Task | None = None
        owns_execution = False
        preserve_checkpoint = False
        try:
            db = AsyncSessionLocal()
        except Exception as e:
            yield FlowEventFactory.error(f"数据库连接失败: {e}")
            return

        try:
            current_run = self._agent_runs.get(session_id)
            if not _managed_run and current_run and not current_run.done:
                yield FlowEventFactory.error("会话正在执行中，请稍后再发送消息")
                return
            if session_id in self._running_sessions:
                yield FlowEventFactory.error("会话正在执行中，请稍后再发送消息")
                return
            if session_id in self._compressing_sessions:
                yield FlowEventFactory.error("正在压缩上下文，请稍后再发送消息")
                return
            if session_id in self._waiting_sessions:
                yield FlowEventFactory.error("会话正在等待人工输入，请使用恢复执行")
                return

            self._running_sessions.add(session_id)
            owns_execution = True
            if not _managed_run:
                direct_task = asyncio.current_task()
                if direct_task:
                    self._direct_streaming_tasks.setdefault(session_id, set()).add(
                        direct_task
                    )

            # 获取会话
            session = await self._get_session(db, session_id)
            if not session:
                yield FlowEventFactory.error("会话不存在")
                return

            # 获取Flow
            flow = await self._get_flow_with_details(
                db, session.flow_id, FlowType.AGENT
            )
            if not flow:
                yield FlowEventFactory.error("Agent不存在")
                return

            # 验证流程
            try:
                self._validate_agent_flow(flow)
            except ValueError as e:
                yield FlowEventFactory.error(str(e))
                logger.exception(e)
                return

            # 临时模型覆盖（仅同供应商内切换）：改的是本次从 DB 加载的内存副本，
            # expunge 摘除 ORM 变更跟踪，防止后续 commit 把覆盖值写回 flow_node 表；
            # capabilities/context_length 按模型元数据联动，不回退全局默认
            if model:
                from app.utils.node_config_helper import derive_model_runtime_meta

                for node in flow.nodes or []:
                    if node.node_type != NodeType.LLM.value:
                        continue
                    # 与节点已配置模型相同：视为未切换，跳过覆盖以保留手动定制
                    if (node.base_config or {}).get("model") == model:
                        continue
                    db.expunge(node)
                    cfg = dict(node.base_config or {})
                    cfg["model"] = model
                    meta = await derive_model_runtime_meta(
                        db, cfg.get("provider") or "", model
                    )
                    if meta:
                        cfg["capabilities"] = meta["capabilities"]
                        if meta["context_length"]:
                            cfg["context_length"] = meta["context_length"]
                    node.base_config = cfg

            # 检查是否首次对话（用于自动生成标题）
            existing_messages = await self._get_messages(db, session_id, 1)
            is_first_message = len(existing_messages) == 0
            # 首次对话即时生成标题：内容取用户消息截断，不依赖执行结果——
            # 手动停止（CancelledError 直达 finally）、等待人工输入（interrupt return）、
            # 执行失败等提前终止路径不会经过流末尾的原标题逻辑
            if is_first_message and session.title == "新对话":
                title = user_message[:50]
                if len(user_message) > 50:
                    title += "..."
                await self._update_session_title(db, session_id, title)

            # 构建图
            graph = self._build_graph(
                flow, 0, agent_conversation_service, session_id=session_id
            )
            config: RunnableConfig = {
                "configurable": {
                    "thread_id": f"agent_{session_id}",
                    "scope_type": "agent",
                }
            }

            # 初始化上下文
            input_data: dict = {}

            # 计划模式标志（前端通过 params 传入，独立于 input_schema）
            plan_mode = bool((params or {}).pop("__plan_mode__", False))

            # 统一通过 input_schema 解析所有参数（包括 message）
            if flow.input_schema:
                all_params = dict(params) if params else {}
                all_params["message"] = user_message
                await self._resolve_input_params(
                    db, all_params, flow.input_schema, input_data
                )
            else:
                input_data["message"] = user_message

            context = FlowContext(
                flow_id=flow.id,
                execution_id=0,  # Agent不使用execution_id
                input_data=input_data,
            )
            context.start()
            if plan_mode:
                context.state.set_variable("plan_mode", True)

            # 清除可能残留的中断状态，发送流程开始事件
            interrupt_service.clear_agent_interrupted(session_id)
            yield FlowEventFactory.flow_start(flow_id=flow.id, execution_id=session_id)

            # 收集LLM响应内容
            llm_content = ""
            llm_thinking = ""

            try:
                # 执行图
                async for event in graph.astream(
                    input=context.state.model_dump(),
                    config=config,
                    stream_mode=["updates", "custom"],
                ):
                    if not isinstance(event, tuple) or len(event) != 2:
                        continue

                    stream_mode_type, event_data = event

                    # 检查用户是否主动中断
                    if interrupt_service.is_agent_interrupted(session_id):
                        logger.info(f"Agent会话被中断: session_id={session_id}")
                        break

                    # 处理 custom 事件（流式输出）
                    if stream_mode_type == "custom":
                        if hasattr(event_data, "to_dict"):
                            event_dict = event_data.to_dict()
                            yield event_dict

                            # 收集LLM内容
                            if event_dict.get("type") == "node_content":
                                llm_content += event_dict.get("data", {}).get(
                                    "content", ""
                                )
                            elif event_dict.get("type") == "node_thinking":
                                llm_thinking += event_dict.get("data", {}).get(
                                    "content", ""
                                )
                        continue

                    # 处理 updates 事件（节点更新）
                    if stream_mode_type != "updates":
                        continue

                    for node_key, result in event_data.items():
                        # 处理 interrupt
                        if node_key == "__interrupt__":
                            interrupt_data = result[0].value if result else {}
                            preserve_checkpoint = True
                            self._waiting_sessions.add(session_id)
                            waiting_event = await self._handle_interrupt(
                                db, session_id, interrupt_data
                            )
                            self._waiting_events[session_id] = waiting_event
                            yield waiting_event
                            return

                        node = next(
                            (n for n in flow.nodes if n.node_key == node_key), None
                        )
                        if not node:
                            continue

                        # 更新上下文状态
                        if isinstance(result, dict):
                            if "variables" in result:
                                for k, v in result.get("variables", {}).items():
                                    context.state.set_variable(k, v)
                            if "output_data" in result:
                                context.state.output_data.update(
                                    result.get("output_data", {})
                                )
                            if "errors" in result:
                                context.state.errors.extend(result.get("errors", []))

                        # 发送节点事件
                        yield FlowEventFactory.node_start(
                            node_key=node.node_key,
                            node_type=node.node_type,
                            node_name=node.node_name,
                        )

                        node_error = None
                        for err in context.state.errors:
                            if err.get("node_key") == node.node_key:
                                node_error = err.get("message")
                                break

                        yield FlowEventFactory.node_done(
                            node_key=node.node_key,
                            node_type=node.node_type,
                            error=node_error,
                        )

                # 发送完成事件（携带结束节点输出，前端按钮展示 + 持久化到 AI 消息）
                is_interrupted = interrupt_service.is_agent_interrupted(session_id)
                end_output = (
                    {}
                    if is_interrupted
                    else self._filter_end_output(context.state.output_data)
                )
                await self._save_end_output_to_message(db, session_id, end_output)
                # ---- WebSocket 广播（chat 完成通知）----
                try:
                    from app.services.ws_manager import ws_manager

                    await ws_manager.notify_execution_done(
                        execution_id=session_id,
                        flow_id=session.flow_id if session else None,
                        flow_name=session.title or "Agent对话",
                        status="cancelled" if is_interrupted else "success",
                        source="agent",
                        last_user_message=user_message,
                    )
                except Exception:
                    pass
                yield FlowEventFactory.flow_done(
                    execution_id=session_id,
                    output_data={"content": llm_content, "end_output": end_output},
                    status="cancelled" if is_interrupted else "success",
                )
                interrupt_service.clear_agent_interrupted(session_id)

            except Exception as e:
                error_msg = format_exception_message(e)
                logger.exception(f"Agent执行失败: {e}")
                # ---- WebSocket 广播（chat 失败通知）----
                try:
                    from app.services.ws_manager import ws_manager

                    await ws_manager.notify_execution_done(
                        execution_id=session_id,
                        flow_id=session.flow_id if session else None,
                        flow_name=session.title or "Agent对话",
                        status="failed",
                        source="agent",
                        error_message=error_msg,
                        last_user_message=user_message,
                    )
                except Exception:
                    pass
                yield FlowEventFactory.error(f"执行失败: {error_msg}")
                interrupt_service.clear_agent_interrupted(session_id)

        finally:
            try:
                if owns_execution:
                    try:
                        from app.services.tool_approval_service import (
                            tool_approval_service,
                        )

                        tool_approval_service.cancel(session_id)
                    except Exception as approval_err:
                        logger.warning(f"清理工具确认状态失败: {approval_err}")
                    if not preserve_checkpoint:
                        cleanup_error = await self._await_cancellation_safe(
                            self._cleanup_thread_checkpoint(session_id)
                        )
                        if cleanup_error:
                            logger.warning(f"清理checkpoint失败: {cleanup_error}")
                if db is not None:
                    close_error = await self._await_cancellation_safe(db.close())
                    if close_error:
                        logger.debug(f"关闭Agent数据库会话失败: {close_error}")
            finally:
                if owns_execution:
                    self._running_sessions.discard(session_id)
                    self._pending_save_sessions.discard(session_id)
                    if direct_task:
                        tasks = self._direct_streaming_tasks.get(session_id)
                        if tasks:
                            tasks.discard(direct_task)
                            if not tasks:
                                self._direct_streaming_tasks.pop(session_id, None)

    @staticmethod
    def _filter_end_output(raw: Optional[dict]) -> dict:
        """过滤结束节点输出：去掉未配置输出变量时写入的全量 variables dump"""
        if not isinstance(raw, dict):
            return {}
        return {k: v for k, v in raw.items() if k != "variables"}

    async def _save_end_output_to_message(
        self, db: AsyncSession, session_id: int, end_output: dict
    ) -> None:
        """把结束节点输出持久化到该会话最后一条 AI 消息（刷新后随消息恢复按钮）"""
        if not end_output:
            return
        try:
            result = await db.execute(
                select(AgentMessage)
                .where(
                    AgentMessage.session_id == session_id,
                    AgentMessage.role == "ai",
                    AgentMessage.is_delete == 0,
                )
                .order_by(AgentMessage.id.desc())
                .limit(1)
            )
            msg = result.scalar_one_or_none()
            if msg is not None:
                msg.end_output = end_output
                await db.commit()
        except Exception as e:
            logger.warning("保存结束节点输出失败 session_id=%s: %s", session_id, e)

    async def _update_session_title(
        self, db: AsyncSession, session_id: int, title: str
    ) -> None:
        """更新会话标题"""
        query = select(AgentSession).where(AgentSession.id == session_id)
        result = await db.execute(query)
        session = result.scalar_one_or_none()
        if session:
            session.title = title
            await db.commit()

    async def _handle_interrupt(
        self, db: AsyncSession, session_id: int, interrupt_data: dict
    ) -> Dict[str, Any]:
        """
        处理interrupt事件

        Args:
            db: 数据库会话
            session_id: 会话ID
            interrupt_data: interrupt数据

        Returns:
            SSE事件字典
        """
        interrupt_type = interrupt_data.get("type", "unknown")
        if interrupt_type == "human_input_required":
            pass

        return FlowEventFactory.waiting_human(
            execution_id=0,
            node_key=interrupt_data.get("node_key", ""),
            question=interrupt_data.get("question", "需要您的输入"),
            context=interrupt_data.get("context"),
            wait_data={
                "type": interrupt_type,
                "question": interrupt_data.get("question", "需要您的输入"),
                "context": interrupt_data.get("context"),
                "tool_call_id": interrupt_data.get("tool_call_id"),
            },
        )

    async def resume_stream(
        self,
        session_id: int,
        human_input: str,
        *,
        _managed_run: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        恢复Agent执行（流式）

        Args:
            session_id: 会话ID
            human_input: 人工输入

        Yields:
            SSE事件字典
        """
        from app.config.database import AsyncSessionLocal

        if self._shutting_down:
            yield FlowEventFactory.error("服务正在关闭，无法恢复 Agent 执行")
            return

        db: AsyncSession | None = None
        direct_task: asyncio.Task | None = None
        owns_execution = False
        was_waiting = self.is_waiting(session_id)
        preserve_checkpoint = was_waiting
        resume_claimed = False
        try:
            db = AsyncSessionLocal()
        except Exception as e:
            yield FlowEventFactory.error(f"数据库连接失败: {e}")
            return

        try:
            current_run = self._agent_runs.get(session_id)
            if not _managed_run and current_run and not current_run.done:
                yield FlowEventFactory.error("会话正在执行中，请稍后再恢复")
                return
            if session_id in self._running_sessions:
                yield FlowEventFactory.error("会话正在执行中，请稍后再恢复")
                return
            if session_id in self._compressing_sessions:
                yield FlowEventFactory.error("正在压缩上下文，请稍后再恢复")
                return

            self._running_sessions.add(session_id)
            owns_execution = True
            if not _managed_run:
                direct_task = asyncio.current_task()
                if direct_task:
                    self._direct_streaming_tasks.setdefault(session_id, set()).add(
                        direct_task
                    )

            # 获取会话
            session = await self._get_session(db, session_id)
            if not session:
                yield FlowEventFactory.error("会话不存在")
                return

            # 获取Flow
            flow = await self._get_flow_with_details(
                db, session.flow_id, FlowType.AGENT
            )
            if not flow:
                yield FlowEventFactory.error("Agent不存在")
                return

            # 构建图
            graph = self._build_graph(
                flow, 0, agent_conversation_service, session_id=session_id
            )
            config: RunnableConfig = {
                "configurable": {
                    "thread_id": f"agent_{session_id}",
                    "scope_type": "agent",
                    "_human_resume_input": human_input,
                }
            }
            # 收集LLM响应内容
            llm_content = ""
            llm_thinking = ""
            resume_end_output: dict = {}

            try:
                # 使用Command(resume=...)恢复执行
                async for event in graph.astream(
                    input=Command(resume=human_input),
                    config=config,
                    stream_mode=["updates", "custom"],
                ):
                    if not resume_claimed:
                        resume_claimed = True
                        self._waiting_sessions.discard(session_id)
                        self._waiting_events.pop(session_id, None)
                        preserve_checkpoint = False
                    if not isinstance(event, tuple) or len(event) != 2:
                        continue

                    stream_mode_type, event_data = event

                    # 检查用户是否主动中断
                    if interrupt_service.is_agent_interrupted(session_id):
                        logger.info(f"Agent会话恢复被中断: session_id={session_id}")
                        break

                    # 处理 custom 事件
                    if stream_mode_type == "custom":
                        if hasattr(event_data, "to_dict"):
                            event_dict = event_data.to_dict()
                            yield event_dict
                            if event_dict.get("type") == "node_content":
                                llm_content += event_dict.get("data", {}).get(
                                    "content", ""
                                )
                            elif event_dict.get("type") == "node_thinking":
                                llm_thinking += event_dict.get("data", {}).get(
                                    "content", ""
                                )
                        continue

                    # 处理 updates 事件
                    if stream_mode_type != "updates":
                        continue

                    for node_key, result in event_data.items():
                        # 处理再次interrupt
                        if node_key == "__interrupt__":
                            interrupt_data = result[0].value if result else {}
                            preserve_checkpoint = True
                            self._waiting_sessions.add(session_id)
                            waiting_event = await self._handle_interrupt(
                                db, session_id, interrupt_data
                            )
                            self._waiting_events[session_id] = waiting_event
                            yield waiting_event
                            return

                        node = next(
                            (n for n in flow.nodes if n.node_key == node_key), None
                        )
                        if not node:
                            continue

                        # 累积各节点 output_data（end 节点最后执行，结果即结束输出）
                        if isinstance(result, dict) and isinstance(
                            result.get("output_data"), dict
                        ):
                            resume_end_output.update(result["output_data"])

                        yield FlowEventFactory.node_start(
                            node_key=node.node_key,
                            node_type=node.node_type,
                            node_name=node.node_name,
                        )

                        node_error = None
                        if isinstance(result, dict) and "errors" in result:
                            for err in result.get("errors", []):
                                if err.get("node_key") == node_key:
                                    node_error = err.get("message")
                                    break

                        yield FlowEventFactory.node_done(
                            node_key=node.node_key,
                            node_type=node.node_type,
                            error=node_error,
                        )

                if was_waiting and not resume_claimed:
                    raise RuntimeError("未找到可恢复的 Agent 中断状态")

                # 发送完成事件（携带结束节点输出，前端按钮展示 + 持久化到 AI 消息）
                is_interrupted = interrupt_service.is_agent_interrupted(session_id)
                end_output = (
                    {} if is_interrupted else self._filter_end_output(resume_end_output)
                )
                await self._save_end_output_to_message(db, session_id, end_output)
                # ---- WebSocket 广播（resume 完成通知）----
                try:
                    from app.services.ws_manager import ws_manager

                    await ws_manager.notify_execution_done(
                        execution_id=session_id,
                        flow_id=session.flow_id if session else None,
                        flow_name=session.title or "Agent对话",
                        status="cancelled" if is_interrupted else "success",
                        source="agent",
                        last_user_message=human_input,
                    )
                except Exception:
                    pass
                yield FlowEventFactory.flow_done(
                    execution_id=session_id,
                    output_data={"content": llm_content, "end_output": end_output},
                    status="cancelled" if is_interrupted else "success",
                )
                interrupt_service.clear_agent_interrupted(session_id)

            except Exception as e:
                error_msg = format_exception_message(e)
                logger.exception(f"Agent恢复执行失败: {e}")
                # ---- WebSocket 广播（resume 失败通知）----
                try:
                    from app.services.ws_manager import ws_manager

                    await ws_manager.notify_execution_done(
                        execution_id=session_id,
                        flow_id=session.flow_id if session else None,
                        flow_name=session.title or "Agent对话",
                        status="failed",
                        source="agent",
                        error_message=error_msg,
                        last_user_message=human_input,
                    )
                except Exception:
                    pass
                yield FlowEventFactory.error(f"执行失败: {error_msg}")
                interrupt_service.clear_agent_interrupted(session_id)

        finally:
            try:
                if owns_execution:
                    try:
                        from app.services.tool_approval_service import (
                            tool_approval_service,
                        )

                        tool_approval_service.cancel(session_id)
                    except Exception as approval_err:
                        logger.warning(f"清理工具确认状态失败: {approval_err}")
                    if not preserve_checkpoint:
                        cleanup_error = await self._await_cancellation_safe(
                            self._cleanup_thread_checkpoint(session_id)
                        )
                        if cleanup_error:
                            logger.warning(f"清理checkpoint失败: {cleanup_error}")
                if db is not None:
                    close_error = await self._await_cancellation_safe(db.close())
                    if close_error:
                        logger.debug(f"关闭Agent数据库会话失败: {close_error}")
            finally:
                if owns_execution:
                    self._running_sessions.discard(session_id)
                    self._pending_save_sessions.discard(session_id)
                    if direct_task:
                        tasks = self._direct_streaming_tasks.get(session_id)
                        if tasks:
                            tasks.discard(direct_task)
                            if not tasks:
                                self._direct_streaming_tasks.pop(session_id, None)

    COMPRESS_MARKER = "[上下文压缩]"
    CONTEXT_SUMMARY_TYPE = "context_summary"

    async def migrate_legacy_compression_messages(self, db: AsyncSession) -> int:
        """将旧压缩消息归一为带 message_type 的单条摘要记录。"""
        query = (
            select(AgentMessage)
            .where(
                AgentMessage.role == "human",
                AgentMessage.message_type.is_(None),
                AgentMessage.content.startswith(self.COMPRESS_MARKER),
            )
            .order_by(AgentMessage.id.asc())
        )
        result = await db.execute(query)
        markers = list(result.scalars().all())

        migrated = 0
        for marker in markers:
            notice, separator, summary = (marker.content or "").partition("\n\n")
            summary = summary.strip() if separator else ""

            # 旧手动压缩格式为 Human marker + AI summary，将两条合并。
            if not summary:
                next_query = (
                    select(AgentMessage)
                    .where(
                        AgentMessage.session_id == marker.session_id,
                        AgentMessage.sequence > marker.sequence,
                    )
                    .order_by(AgentMessage.sequence.asc(), AgentMessage.id.asc())
                    .limit(1)
                )
                next_result = await db.execute(next_query)
                next_message = next_result.scalar_one_or_none()
                if (
                    next_message
                    and next_message.role == "ai"
                    and not next_message.tool_calls
                ):
                    summary = (next_message.content or "").strip()
                    marker.prompt_tokens = next_message.prompt_tokens
                    marker.completion_tokens = next_message.completion_tokens
                    marker.total_tokens = next_message.total_tokens
                    next_message.is_delete = 1

            if not summary:
                logger.warning("旧压缩消息缺少摘要内容: message_id=%s", marker.id)
                continue

            count_match = re.search(r"共\s*(\d+)\s*条", notice)
            marker.content = summary
            marker.message_type = self.CONTEXT_SUMMARY_TYPE
            marker.input_data = {
                "removed_count": int(count_match.group(1)) if count_match else 0
            }
            migrated += 1

        if migrated:
            await db.commit()
            logger.info("已迁移 %s 条旧上下文压缩消息", migrated)
        return migrated

    async def is_compressing_session(self, db: AsyncSession, session_id: int) -> bool:
        """检查指定会话是否正在压缩上下文"""
        return session_id in self._compressing_sessions

    def is_pending_save(self, session_id: int) -> bool:
        """检查指定会话是否正在等待中断后的消息保存完成"""
        return session_id in self._pending_save_sessions

    def is_running(self, session_id: int) -> bool:
        """检查指定会话是否正在执行（用于刷新后前端检测并显示停止按钮）"""
        return bool(self.get_run_status(session_id)["running"])

    async def _run_compress_background(
        self, session_id: int, custom_prompt: str = ""
    ) -> None:
        """后台压缩任务，独立于 HTTP 请求生命周期，前端通过轮询 /compressing 检测完成"""
        from app.config.database import AsyncSessionLocal

        try:
            async with AsyncSessionLocal() as db:
                result = await self.compress_session(
                    db, session_id, custom_prompt, record_token_usage=True
                )
                self._compress_results[session_id] = result
        except Exception as e:
            logger.error(f"后台压缩会话上下文失败: session_id={session_id}, error={e}")
            self._compress_results[session_id] = {
                "summary": None,
                "kept_count": 0,
                "removed_count": 0,
                "error": f"后台压缩异常: {e}",
            }

    def pop_compress_result(self, session_id: int) -> dict | None:
        """读取并清理压缩结果，每个结果只返回一次"""
        return self._compress_results.pop(session_id, None)

    async def compress_session(
        self,
        db: AsyncSession,
        session_id: int,
        custom_prompt: str = "",
        *,
        exclude_tail_count: int = 0,
        continue_react: bool = False,
        cleanup_checkpoint: bool = True,
        record_token_usage: bool = False,
    ) -> dict[str, Any]:
        """
        压缩会话上下文（手动/自动统一入口）

        用 Agent 自身的 LLM 将全部对话总结为摘要。
        同时清理 LangGraph checkpoint，确保下次执行从压缩后的历史重建。

        Args:
            custom_prompt: 自定义压缩提示词，非空时追加到默认提示词后
            exclude_tail_count: 不参与摘要并在事务中原样重建的末尾消息数
            continue_react: 是否为需要继续执行的 ReAct 中途压缩
            cleanup_checkpoint: 压缩完成后是否立即清理 LangGraph checkpoint
            record_token_usage: 是否将压缩 LLM 调用的 token_usage 持久化到数据库。
                                自动压缩路径（ReAct 节点内）由调用方自行记录，
                                默认 False 避免重复；手动压缩设为 True 以补齐对称能力。

        Returns:
            {"summary": str|None, "kept_count": int, "removed_count": int, "token_usage": dict}
        """
        self._compressing_sessions.add(session_id)
        try:
            result = await self._do_compress(
                db,
                session_id,
                custom_prompt,
                exclude_tail_count=exclude_tail_count,
                continue_react=continue_react,
                cleanup_checkpoint=cleanup_checkpoint,
            )
            if record_token_usage:
                await self._record_compress_token_usage(
                    db, session_id, result.get("token_usage") or {}
                )
            return result
        finally:
            self._compressing_sessions.discard(session_id)

    async def _do_compress(
        self,
        db: AsyncSession,
        session_id: int,
        custom_prompt: str = "",
        *,
        exclude_tail_count: int = 0,
        continue_react: bool = False,
        cleanup_checkpoint: bool = True,
    ) -> dict[str, Any]:
        """压缩会话上下文的实际执行逻辑"""
        session = await self._get_session(db, session_id)
        if not session:
            return {"summary": None, "kept_count": 0, "removed_count": 0}

        all_messages = await self._get_messages(db, session_id, limit=9999)
        total = len(all_messages)

        if total == 0:
            return {"summary": None, "kept_count": 0, "removed_count": 0}

        exclude_tail_count = max(0, exclude_tail_count)
        if exclude_tail_count > total:
            return {
                "summary": None,
                "kept_count": total,
                "removed_count": 0,
                "error": "保留消息数量超过当前会话消息总数",
            }
        tail_messages = all_messages[-exclude_tail_count:] if exclude_tail_count else []
        messages_to_compress = (
            all_messages[:-exclude_tail_count] if exclude_tail_count else all_messages
        )
        if not messages_to_compress:
            return {
                "summary": None,
                "kept_count": exclude_tail_count,
                "removed_count": 0,
            }

        # 获取 flow 及 LLM 配置
        flow = await self._get_flow_with_details(db, session.flow_id, FlowType.AGENT)
        if not flow:
            return {"summary": None, "kept_count": total, "removed_count": 0}

        llm_config = self._extract_llm_config(flow)
        if not llm_config.get("model"):
            return {"summary": None, "kept_count": total, "removed_count": 0}

        removed_count = len(messages_to_compress)

        # ReAct 中途压缩仅纳入最近的有限工具输出，避免摘要请求本身溢出。
        recent_tool_contents: dict[int, str] = {}
        recent_tool_calls: dict[int, str] = {}
        if continue_react:
            recent_tool_contents, recent_tool_calls = self._trim_recent_tool_payloads(
                messages_to_compress
            )

        conversation_lines = []
        for index, msg in enumerate(messages_to_compress):
            role_label = (
                "上下文摘要"
                if msg.message_type == self.CONTEXT_SUMMARY_TYPE
                else {"human": "用户", "ai": "AI", "tool": "工具"}.get(
                    msg.role, msg.role
                )
            )
            if msg.role == "tool":
                content = recent_tool_contents.get(index)
                if content is None:
                    continue
                content = f"tool_call_id={msg.tool_call_id or ''}\n{content}"
            else:
                content = msg.content or ""
                tool_calls = recent_tool_calls.get(index)
                if tool_calls:
                    content = (
                        f"{content}\n工具调用: {tool_calls}"
                        if content
                        else f"工具调用: {tool_calls}"
                    )
            conversation_lines.append(f"{role_label}: {content}")
        conversation_text = "\n".join(conversation_lines)

        # 调用 LLM 总结

        try:
            provider_name = llm_config.get("provider", "")
            from app.agent_flow.ai_provider import create_provider

            provider = create_provider(
                provider_name,
                llm_config.get("api_key", ""),
                llm_config.get("base_url", ""),
            )
            llm = provider.create_chat_model(
                model=llm_config.get("model", ""),
                temperature=0.3,
                max_tokens=4096,
            )
            summary_prompt = (
                "你是一个对话上下文压缩助手。请将以下对话历史压缩为结构化摘要。\n\n"
                "要求：\n"
                "1. 按主题分段，用「## 主题名」标记每个段落\n"
                "2. 用简洁的要点列表（bullets）而非段落\n"
                "3. 保留所有关键决策、结论和重要上下文\n"
                "4. 保留用户明确的偏好和约束条件\n"
                "5. 精确保留文件路径、函数名、配置项、变量名等技术标识符\n"
                "6. 省略工具调用的中间过程和重复内容\n"
                "7. 保留未完成的任务和待跟进的事项\n"
                "8. 移除已过时或不再相关的信息，只保留对后续对话仍有价值的内容\n"
                "9. 保持简洁紧凑，用最少的文字传达最多的有效信息\n"
                "10. 直接输出摘要，不要添加前缀、标题或元说明（如「以下是摘要」等）\n"
                "11. 使用与对话相同的语言输出\n"
                "12. 不要回答对话本身的内容，只做压缩"
            )
            # 自定义提示词追加到默认规则后作为补充要求
            if custom_prompt.strip():
                summary_prompt += f"\n\n## 补充要求\n{custom_prompt.strip()}"
            response = await llm.ainvoke(
                [
                    SystemMessage(content=summary_prompt),
                    HumanMessage(content=conversation_text),
                ]
            )
            summary = (
                extract_text_content(response.content).strip()
                if response.content
                else ""
            )
            if summary == "":
                return {
                    "summary": None,
                    "kept_count": total,
                    "removed_count": 0,
                    "error": "LLM调用失败: 返回结果为空",
                }
            # 提取压缩 LLM 调用的 token 用量
            compress_usage = extract_token_usage(response)
        except Exception as e:
            logger.exception(f"压缩上下文时LLM调用失败: {e}")
            return {
                "summary": None,
                "kept_count": total,
                "removed_count": 0,
                "error": f"LLM调用失败: {e}",
            }

        # 软删除全部消息
        all_ids = [msg.id for msg in all_messages]
        await db.execute(
            update(AgentMessage).where(AgentMessage.id.in_(all_ids)).values(is_delete=1)
        )

        # 手动和自动压缩统一为单条 Human 摘要，避免自动压缩时摘要 AI
        # 与待处理的 AI tool_calls 连续出现。input_data 仅用于 UI 识别内部消息。
        summary_kwargs: dict[str, Any] = {
            "session_id": session_id,
            "role": "human",
            "message_type": self.CONTEXT_SUMMARY_TYPE,
            "content": summary,
            "input_data": {"removed_count": removed_count},
            "sequence": 0,
        }
        if compress_usage.get("prompt_tokens") is not None:
            summary_kwargs["prompt_tokens"] = compress_usage["prompt_tokens"]
        if compress_usage.get("completion_tokens") is not None:
            summary_kwargs["completion_tokens"] = compress_usage["completion_tokens"]
        if compress_usage.get("total_tokens") is not None:
            summary_kwargs["total_tokens"] = compress_usage["total_tokens"]
        summary_user = AgentMessage(**summary_kwargs)
        db.add(summary_user)
        await db.flush()

        summary_message_count = 1

        # 活动 ReAct 尾部在同一事务内重建，确保 AI tool_calls 与 ToolMessage
        # 不会在摘要提交和后续保存之间因取消或进程退出而丢失。
        for sequence, message in enumerate(tail_messages, start=summary_message_count):
            values = {
                column.name: getattr(message, column.name)
                for column in AgentMessage.__table__.columns
                if column.name != "id"
            }
            values["sequence"] = sequence
            values["is_delete"] = 0
            db.add(AgentMessage(**values))

        await db.commit()

        if cleanup_checkpoint:
            await self._cleanup_thread_checkpoint(session_id)

        return {
            "summary": summary,
            "kept_count": exclude_tail_count,
            "removed_count": removed_count,
            "token_usage": compress_usage,
        }

    @staticmethod
    def _trim_recent_tool_payloads(
        messages: list[AgentMessage],
    ) -> tuple[dict[int, str], dict[int, str]]:
        """从消息列表尾部向前，按字节预算挑选 tool 输出 / ai tool_calls。

        ReAct 中途压缩时，仅纳入最近的有限工具负载，避免摘要请求本身溢出。
        返回 (recent_tool_contents, recent_tool_calls)，key 为消息在 messages 中的索引。
        """
        tool_content_budget = max(settings.tool_output_max_bytes * 2, 4096)
        tool_call_budget = 4096

        recent_tool_contents: dict[int, str] = {}
        recent_tool_calls: dict[int, str] = {}
        remaining = tool_content_budget
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if message.role != "tool" or remaining <= 0:
                continue
            content_bytes = (message.content or "").encode("utf-8")
            if len(content_bytes) <= remaining:
                recent_tool_contents[index] = message.content or ""
                remaining -= len(content_bytes)
            else:
                recent_tool_contents[index] = (
                    content_bytes[:remaining].decode("utf-8", errors="ignore")
                    + "\n...[工具输出已截断]"
                )
                remaining = 0

        remaining_call_bytes = tool_call_budget
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if (
                message.role != "ai"
                or not message.tool_calls
                or remaining_call_bytes <= 0
            ):
                continue
            call_bytes = json.dumps(
                message.tool_calls,
                ensure_ascii=False,
                default=str,
            ).encode("utf-8")
            if len(call_bytes) > remaining_call_bytes:
                call_bytes = call_bytes[:remaining_call_bytes]
            recent_tool_calls[index] = call_bytes.decode("utf-8", errors="ignore")
            remaining_call_bytes -= len(call_bytes)

        return recent_tool_contents, recent_tool_calls

    @staticmethod
    async def _record_compress_token_usage(
        db: AsyncSession, session_id: int, token_usage: dict
    ) -> None:
        """将压缩 LLM 调用的 token_usage 落库；空用量或异常一律吞掉，不影响主流程。"""
        if not token_usage.get("total_tokens"):
            return
        try:
            from app.services.token_usage_service import token_usage_service

            await token_usage_service.record_usage(
                db,
                source_type="agent",
                source_id=session_id,
                node_key="_compress",
                prompt_tokens=token_usage.get("prompt_tokens", 0),
                completion_tokens=token_usage.get("completion_tokens", 0),
                total_tokens=token_usage.get("total_tokens", 0),
            )
        except Exception as exc:
            logger.warning(
                f"记录压缩 token_usage 失败: session_id={session_id}, error={exc}"
            )

    @staticmethod
    def _extract_llm_config(flow: Flow) -> dict[str, Any]:
        """从 Flow 节点中提取 LLM 配置（model/api_key/base_url/context_length）"""
        from app.models.flow_node import NodeType

        for node in flow.nodes:
            if node.node_type == NodeType.LLM.value and node.base_config:
                config = node.base_config if isinstance(node.base_config, dict) else {}
                return {
                    "provider": config.get("provider", ""),
                    "model": config.get("model", ""),
                    "api_key": config.get("api_key", ""),
                    "base_url": config.get("base_url", ""),
                    "context_length": config.get("context_length", 0) or 0,
                }
        return {}


# 单例
agent_executor_service = AgentExecutorService()

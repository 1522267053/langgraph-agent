"""文件变更追踪上下文

Agent 执行期间通过 ContextVar 传递 session_id / run_id，
供工具埋点（file_write / text_editor / __save_file__ 等）记录文件变更，
回退消息时据此恢复文件。

- 仅 Agent 模式设置（Flow 模式无会话概念，不追踪）
- 每个 Agent run 是独立的 asyncio task（含子 Agent，见 _start_agent_run），
  task 拥有独立的 contextvars 副本，子 Agent 执行时自然覆盖为自己的 session
"""

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Optional


@dataclass
class FileChangeContext:
    """当前 Agent 执行的文件变更追踪上下文"""

    session_id: int
    run_id: str


_current_context: ContextVar[Optional[FileChangeContext]] = ContextVar(
    "file_change_context", default=None
)


def set_file_change_context(session_id: int, run_id: str) -> Token:
    """设置当前执行的文件变更追踪上下文，返回 token 用于恢复"""
    return _current_context.set(FileChangeContext(session_id=session_id, run_id=run_id))


def reset_file_change_context(token: Token) -> None:
    """恢复之前的上下文"""
    _current_context.reset(token)


def get_file_change_context() -> Optional[FileChangeContext]:
    """获取当前执行的文件变更追踪上下文，非 Agent 模式返回 None"""
    return _current_context.get()


@contextmanager
def file_change_context_scope(session_id: int, run_id: str):
    """临时切换文件变更追踪上下文的作用域管理器"""
    token = set_file_change_context(session_id, run_id)
    try:
        yield
    finally:
        reset_file_change_context(token)

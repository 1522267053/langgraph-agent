"""执行上下文 - 在图执行期间传递运行时依赖给循环节点并发执行"""

from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any, AsyncGenerator, Dict, Optional

if TYPE_CHECKING:
    from app.agent_flow.node_handlers.base_handler import BaseNodeHandler


class ExecutionContext:
    """当前流程执行的运行时上下文"""

    def __init__(self):
        self.handler_map: Dict[str, "BaseNodeHandler"] = {}
        self.llm_kwargs: Dict[str, Any] = {}
        self.expanded_flow: Optional[Any] = None
        self.execution_id: int = 0
        self.flow_id: int = 0
        self.parent_path: str = ""


_current_context: ContextVar[Optional[ExecutionContext]] = ContextVar(
    "execution_context", default=None
)


def set_execution_context(ctx: ExecutionContext) -> Token:
    """设置执行上下文，返回 token 用于恢复"""
    return _current_context.set(ctx)


def get_execution_context() -> Optional[ExecutionContext]:
    return _current_context.get()


def reset_execution_context(token: Token) -> None:
    """恢复之前的执行上下文"""
    _current_context.reset(token)


def clear_execution_context() -> None:
    _current_context.set(None)


@asynccontextmanager
async def execution_context_scope(
    ctx: ExecutionContext,
) -> AsyncGenerator[ExecutionContext, None]:
    """临时切换执行上下文的作用域管理器，支持嵌套（用于 async with）"""
    token = _current_context.set(ctx)
    try:
        yield ctx
    finally:
        _current_context.reset(token)

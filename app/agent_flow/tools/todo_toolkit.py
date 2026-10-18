"""
任务计划工具集（TodoToolKit）

从 todo_handler.get_tool 抽出的 2 个工具：todowrite / todoread。
任务列表按会话（Agent）或执行（Flow）维度持久化存储到数据库。

依赖注入：resolve_context 回调由 handler 注入（原 _resolve_context，
解析 ref_type/ref_id）；writer 回调用于 SSE 推送 TodoUpdateEvent。

依赖方向：tools/* → app/services/*、app/config/*；
本模块不 import node_handlers。
"""

from typing import TYPE_CHECKING, Awaitable, Callable, Optional, Sequence

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent_flow.flow_event import TodoUpdateEvent
from app.agent_flow.tools.base_abs_tool import BaseToolKit
from app.config.database import AsyncSessionLocal
from app.services.todo_service import todo_service
from langgraph.types import StreamWriter

if TYPE_CHECKING:
    from app.models.flow_node import FlowNode

# ---- 描述单源（唯一维护点；tool_info 与 build_tools 共用）----
TODO_TOOL_DESCRIPTIONS: dict[str, str] = {
    "todowrite": (
        "创建或更新任务列表（LLM 自身的任务规划工具，专用于拆解和跟踪你正在执行的"
        "多步任务；不要用它记录用户的待办事项）。"
        "参数 todos 为 JSON 数组，每条: content(必填), status, priority。"
        "每次调用替换整个列表，同一时间仅一个 in_progress。"
    ),
    "todoread": "读取当前的任务计划列表。返回按排序位置排列的所有任务项。",
}

# 回调签名：() -> (ref_type, ref_id)（handler 注入，原 _resolve_context）
ResolveContextFn = Callable[[], Awaitable[tuple[Optional[str], Optional[int]]]]
# 回调签名：() -> Optional[StreamWriter]（handler 注入，SSE 推送用）
GetWriterFn = Callable[[], StreamWriter]


class TodoItem(BaseModel):
    model_config = {"extra": "ignore"}
    content: str = Field(..., description="任务描述")
    status: str = Field(
        "pending", description="pending/in_progress/completed/cancelled"
    )
    priority: str = Field("medium", description="high/medium/low")


class TodoWriteInput(BaseModel):
    todos: list[TodoItem] = Field(
        ...,
        description="任务列表，每次调用替换整个列表",
    )


class TodoReadInput(BaseModel):
    pass


class TodoToolKit(BaseToolKit):
    """任务计划工具集（2 个工具）

    由 todo_handler.get_tool 构造，注入 resolve_context / get_writer 回调。
    """

    def __init__(self, resolve_context: ResolveContextFn, get_writer: GetWriterFn):
        self._resolve_context = resolve_context
        self._get_writer = get_writer

    async def build_tools(self) -> Sequence[StructuredTool]:
        resolve_context = self._resolve_context
        get_writer = self._get_writer
        # write_todos 与 write_todos_with_event 共享的最新列表（原 handler._last_todos）
        last_todos: list[dict] = []

        async def write_todos(todos: list[TodoItem]) -> dict:
            """写入/更新任务计划列表"""
            ref_type, ref_id = await resolve_context()
            if not ref_type or not ref_id:
                return {"error": "无法获取上下文信息"}

            items = todos

            valid_statuses = {"pending", "in_progress", "completed", "cancelled"}
            valid_priorities = {"high", "medium", "low"}

            validated = []
            for item in items:
                content = (item.content or "").strip()
                if not content:
                    continue
                status = item.status
                if status not in valid_statuses:
                    status = "pending"
                priority = item.priority
                if priority not in valid_priorities:
                    priority = "medium"
                validated.append(
                    {
                        "content": content,
                        "status": status,
                        "priority": priority,
                    }
                )

            if not validated:
                return {"error": "任务列表不能为空"}

            async with AsyncSessionLocal() as db:
                result = await todo_service.update_ref_todos(
                    db=db, ref_type=ref_type, ref_id=ref_id, todos=validated
                )
                await db.commit()

            last_todos.clear()
            last_todos.extend(
                {**item, "id": r["id"]} for item, r in zip(validated, result)
            )
            return {"success": True, "count": len(result), "todos": last_todos}

        async def read_todos() -> dict:
            """读取当前任务计划列表"""
            ref_type, ref_id = await resolve_context()
            if not ref_type or not ref_id:
                return {"error": "无法获取上下文信息"}

            async with AsyncSessionLocal() as db:
                items = await todo_service.get_by_ref(db, ref_type, ref_id)

            result = [
                {
                    "id": item.id,
                    "content": item.content,
                    "status": item.status,
                    "priority": item.priority,
                    "position": item.position,
                }
                for item in items
            ]
            return {"todos": result, "total": len(result)}

        async def write_todos_with_event(todos: list[TodoItem]) -> dict:
            """写入任务列表并通过 SSE 推送更新事件"""
            result_str = await write_todos(todos)
            writer = get_writer()
            if writer and last_todos:
                try:
                    writer(TodoUpdateEvent(todos=last_todos))
                except Exception:
                    pass
            return result_str

        async def read_todos_with_event() -> dict:
            """读取任务列表并通过 SSE 推送更新事件"""
            result_str = await read_todos()
            writer = get_writer()
            ref_type, ref_id = await resolve_context()
            if writer and ref_type and ref_id:
                try:
                    async with AsyncSessionLocal() as db:
                        items = await todo_service.get_by_ref(db, ref_type, ref_id)
                    todos = [
                        {
                            "id": item.id,
                            "content": item.content,
                            "status": item.status,
                            "priority": item.priority,
                            "position": item.position,
                        }
                        for item in items
                    ]
                    writer(TodoUpdateEvent(todos=todos))
                except Exception:
                    pass
            return result_str

        return [
            StructuredTool(
                name="todowrite",
                description=TODO_TOOL_DESCRIPTIONS["todowrite"],
                func=None,
                coroutine=write_todos_with_event,
                args_schema=TodoWriteInput,
            ),
            StructuredTool(
                name="todoread",
                description=TODO_TOOL_DESCRIPTIONS["todoread"],
                func=None,
                coroutine=read_todos_with_event,
                args_schema=TodoReadInput,
            ),
        ]

    @classmethod
    def tool_info(cls, node: "FlowNode") -> list[dict]:
        """从描述常量派生（单源：与 build_tools 产出逐字节一致）"""
        return [
            {"name": n, "description": d} for n, d in TODO_TOOL_DESCRIPTIONS.items()
        ]

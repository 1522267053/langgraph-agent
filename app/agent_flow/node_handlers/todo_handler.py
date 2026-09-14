"""
任务计划节点处理器

为 Agent/Flow 提供 LLM 工具，使 LLM 能自主创建和更新任务列表。
任务列表按会话（Agent）或执行（Flow）维度持久化存储到数据库。

提供的工具：
1. todowrite - 写入/更新任务计划列表
2. todoread - 读取当前任务计划列表
3. update_todo - 按 id 更新单条任务（content/status/priority）
"""

from typing import Optional, TYPE_CHECKING
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool, BaseTool
from langgraph.types import StreamWriter
from pydantic import BaseModel, Field

from app.models.flow_node import FlowNode
from app.agent_flow.flow_context import FlowState
from app.agent_flow.node_handlers.base_handler import BaseNodeHandler
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.flow_event import TodoUpdateEvent
from app.config.database import AsyncSessionLocal
from app.services.todo_service import todo_service

if TYPE_CHECKING:
    pass


@NodeHandlerRegistry.register("todo")
class TodoNodeHandler(BaseNodeHandler):
    """
    任务计划节点处理器

    作为 LLM 工具提供者使用（通过 tools Handle 连接到 LLM 节点）。
    提供任务列表的创建、更新和读取能力，帮助 LLM 跟踪复杂任务的执行进度。
    """

    def __init__(self):
        super().__init__()
        self._writer: Optional[StreamWriter] = None
        self._ref_type: Optional[str] = None
        self._ref_id: Optional[int] = None

    def _resolve_context(
        self, config: Optional[RunnableConfig] = None
    ) -> tuple[Optional[str], Optional[int]]:
        """从 config 中解析 ref_type 和 ref_id"""
        if self._ref_type is not None:
            return self._ref_type, self._ref_id
        if config and "configurable" in config:
            configurable = config["configurable"]
            scope_type = configurable.get("scope_type", "agent")
            thread_id = configurable.get("thread_id")
            if thread_id:
                try:
                    self._ref_type = scope_type
                    _, id_str = thread_id.split("_", 1)
                    self._ref_id = int(id_str)
                    return self._ref_type, self._ref_id
                except (ValueError, TypeError):
                    pass
        return None, None

    @classmethod
    def get_config_schema(cls) -> list[dict]:
        return []

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState | dict:
        return state

    @classmethod
    def allow_multiple_tool_connections(cls) -> bool:
        """任务计划系统只需一个，同一 LLM 不允许多实例"""
        return False

    async def get_system_prompt_hint(self, node: FlowNode) -> Optional[str]:
        """返回任务计划使用提示，追加到 LLM system_prompt"""
        return (
            "\n\n## 任务计划系统(专给大模型使用，不能给用户使用，不需要记录用户的待办)\n"
            "拥有任务规划与进度跟踪能力。使用规则：\n"
            "- 复杂任务（3步以上）时，使用 todowrite 创建任务列表来组织和跟踪进度\n"
            "- 收到新指令后立即捕获，更新任务列表\n"
            "- 完成任务后立即标记为 completed（不要批量更新）\n"
            "- 开始新任务时标记为 in_progress（同一时间仅一个 in_progress）\n"
            "- 取消无关任务时标记为 cancelled\n"
            "- 需要修改单条任务的内容/状态/优先级时，使用 update_todo 按 id 更新（id 通过 todoread 获取）\n"
            "- 将复杂任务拆分为小步骤\n"
            "- 需要查看当前任务列表时使用 todoread\n"
            "- 简单任务（1-2步）或纯对话不需要使用任务列表"
        )

    async def get_tool(self, node: FlowNode) -> list[BaseTool]:
        handler = self

        async def write_todos(todos: list[TodoItem]) -> dict:
            """写入/更新任务计划列表"""
            ref_type, ref_id = handler._resolve_context()
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

            handler._last_todos = [
                {**item, "id": r["id"]} for item, r in zip(validated, result)
            ]
            return {"success": True, "count": len(result), "todos": handler._last_todos}

        async def read_todos() -> dict:
            """读取当前任务计划列表"""
            ref_type, ref_id = handler._resolve_context()
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

        async def update_todo(
            todo_id: int, content: str, status: str, priority: str
        ) -> dict:
            """按 id 更新单条任务（content/status/priority）"""
            ref_type, ref_id = handler._resolve_context()
            if not ref_type or not ref_id:
                return {"error": "无法获取上下文信息"}

            item_content = (content or "").strip()
            if not item_content:
                return {"error": "任务描述不能为空"}

            valid_statuses = {"pending", "in_progress", "completed", "cancelled"}
            valid_priorities = {"high", "medium", "low"}
            if status not in valid_statuses:
                return {
                    "error": f"无效的 status: {status}，可选值: pending/in_progress/completed/cancelled"
                }
            if priority not in valid_priorities:
                return {
                    "error": f"无效的 priority: {priority}，可选值: high/medium/low"
                }

            async with AsyncSessionLocal() as db:
                item = await todo_service.update_todo_by_id(
                    db,
                    todo_id=todo_id,
                    ref_type=ref_type,
                    ref_id=ref_id,
                    content=item_content,
                    status=status,
                    priority=priority,
                )
                await db.commit()

            if not item:
                return {"error": f"id 为 {todo_id} 的任务不存在或已删除"}

            updated = {
                "id": item.id,
                "content": item.content,
                "status": item.status,
                "priority": item.priority,
                "position": item.position,
            }
            # 更新 handler 缓存中的对应项，保持 todowrite 缓存与 DB 一致
            if getattr(handler, "_last_todos", None):
                handler._last_todos = [
                    updated if t.get("id") == todo_id else t
                    for t in handler._last_todos
                ]
            return {"success": True, "todo": updated}

        # 保存 handler 引用，用于 writer 回调
        self._write_todos_func = write_todos
        self._last_todos = []

        async def write_todos_with_event(todos: list[TodoItem]) -> dict:
            """写入任务列表并通过 SSE 推送更新事件"""
            result_str = await write_todos(todos)
            if self._writer and self._last_todos:
                try:
                    self._writer(TodoUpdateEvent(todos=self._last_todos))
                except Exception:
                    pass
            return result_str

        async def read_todos_with_event() -> dict:
            """读取任务列表并通过 SSE 推送更新事件"""
            ref_type, ref_id = handler._resolve_context()
            result_str = await read_todos()
            if self._writer and ref_type and ref_id:
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
                    self._writer(TodoUpdateEvent(todos=todos))
                except Exception:
                    pass
            return result_str

        async def update_todo_with_event(
            todo_id: int, content: str, status: str, priority: str
        ) -> dict:
            """更新单条任务并通过 SSE 推送更新事件

            与 write/read 的推送策略一致：更新失败（校验错误/id 不存在）时
            不推事件，仅返回 error 信息给 LLM
            """
            result = await update_todo(todo_id, content, status, priority)
            if result.get("success") and self._writer:
                try:
                    async with AsyncSessionLocal() as db:
                        ref_type, ref_id = handler._resolve_context()
                        if ref_type and ref_id:
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
                        self._writer(TodoUpdateEvent(todos=todos))
                except Exception:
                    pass
            return result

        # 重新绑定 writer 引用的方法
        self._write_todos_func = write_todos_with_event

        tools: list[BaseTool] = [
            StructuredTool(
                name="todowrite",
                description=(
                    "创建或更新任务列表。"
                    "参数 todos 为 JSON 数组，每条: content(必填), status, priority。"
                    "每次调用替换整个列表，同一时间仅一个 in_progress。"
                ),
                func=None,
                coroutine=write_todos_with_event,
                args_schema=TodoWriteInput,
            ),
            StructuredTool(
                name="todoread",
                description=(
                    "读取当前的任务计划列表。返回按排序位置排列的所有任务项。"
                ),
                func=None,
                coroutine=read_todos_with_event,
                args_schema=TodoReadInput,
            ),
            StructuredTool(
                name="update_todo",
                description=(
                    "按 id 更新单条任务的内容/状态/优先级（content 必填，"
                    "status 与 priority 全量覆盖）。任务 id 通过 todoread 获取。"
                ),
                func=None,
                coroutine=update_todo_with_event,
                args_schema=TodoUpdateInput,
            ),
        ]

        return tools

    @classmethod
    def get_tool_info(cls, node: FlowNode) -> list[dict]:
        return [
            {"name": "todowrite", "description": "创建或更新任务列表"},
            {"name": "todoread", "description": "读取当前任务计划列表"},
            {"name": "update_todo", "description": "按 id 更新单条任务"},
        ]


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


class TodoUpdateInput(BaseModel):
    todo_id: int = Field(..., description="任务项 id（通过 todoread 获取）")
    content: str = Field(..., description="任务描述")
    status: str = Field(
        "pending", description="pending/in_progress/completed/cancelled"
    )
    priority: str = Field("medium", description="high/medium/low")

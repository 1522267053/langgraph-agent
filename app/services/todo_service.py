"""
任务计划服务

管理 LLM todowrite/todoread 工具的数据库操作。
采用增量更新策略：每次写入时删除旧数据，批量插入新数据。
"""

from typing import List, Optional
from sqlalchemy import select, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.todo_item import TodoItem
from app.schemas.todo_schema import TodoItemCreate, TodoItemUpdate
from app.services.base_service import BaseService


class TodoService(BaseService[TodoItem, TodoItemCreate, TodoItemUpdate]):
    def __init__(self):
        super().__init__(TodoItem)

    async def get_by_ref(
        self, db: AsyncSession, ref_type: str, ref_id: int
    ) -> List[TodoItem]:
        """按关联类型和ID获取任务列表，按 position 排序"""
        query = (
            select(TodoItem)
            .where(
                and_(
                    TodoItem.ref_type == ref_type,
                    TodoItem.ref_id == ref_id,
                    TodoItem.is_delete == 0,
                )
            )
            .order_by(TodoItem.position.asc(), TodoItem.id.asc())
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def update_ref_todos(
        self,
        db: AsyncSession,
        ref_type: str,
        ref_id: int,
        todos: List[dict],
    ) -> List[dict]:
        """增量更新：删除旧数据 + 批量插入新数据，返回更新后的列表"""
        # 删除该关联的旧数据（软删除）
        delete_stmt = (
            delete(TodoItem)
            .where(
                and_(
                    TodoItem.ref_type == ref_type,
                    TodoItem.ref_id == ref_id,
                    TodoItem.is_delete == 0,
                )
            )
            .execution_options(include_deleted=True)
        )
        await db.execute(delete_stmt)
        await db.flush()

        # 批量插入新数据
        items = []
        for idx, todo in enumerate(todos):
            content = (todo.get("content") or "").strip()
            if not content:
                continue
            item = TodoItem(
                ref_type=ref_type,
                ref_id=ref_id,
                content=content[:500],
                status=todo.get("status", "pending"),
                priority=todo.get("priority", "medium"),
                position=idx,
            )
            db.add(item)
            items.append(item)

        await db.flush()

        # 返回更新后的列表（含自增ID）
        return [
            {
                "id": item.id,
                "content": item.content,
                "status": item.status,
                "priority": item.priority,
                "position": item.position,
            }
            for item in items
        ]


    async def update_todo_by_id(
        self,
        db: AsyncSession,
        todo_id: int,
        ref_type: str,
        ref_id: int,
        content: str,
        status: str,
        priority: str,
    ) -> Optional[TodoItem]:
        """按 id 更新单条任务（content/status/priority 全量覆盖）

        归属校验：todo 必须属于 (ref_type, ref_id)，防止 LLM 传入其他
        会话/执行的 id 跨域修改。不存在或不属于当前上下文返回 None。

        Args:
            db: 数据库异步会话
            todo_id: 任务项主键 id
            ref_type: 关联类型（agent/flow）
            ref_id: 关联 ID（session_id / execution_id）
            content: 任务内容（调用方保证非空，此处截断到 500）
            status: 状态（调用方已校验白名单）
            priority: 优先级（调用方已校验白名单）

        Returns:
            更新后的 TodoItem；id 不存在或不属于当前上下文时返回 None
        """
        query = select(TodoItem).where(
            and_(
                TodoItem.id == todo_id,
                TodoItem.ref_type == ref_type,
                TodoItem.ref_id == ref_id,
                TodoItem.is_delete == 0,
            )
        )
        result = await db.execute(query)
        item = result.scalar_one_or_none()
        if not item:
            return None
        item.content = (content or "").strip()[:500]
        item.status = status
        item.priority = priority
        await db.flush()
        return item


todo_service = TodoService()

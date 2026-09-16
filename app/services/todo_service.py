"""
任务计划服务

管理 LLM todowrite/todoread 工具的数据库操作。
采用增量更新策略：每次写入时删除旧数据，批量插入新数据。
"""

from typing import List
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


todo_service = TodoService()

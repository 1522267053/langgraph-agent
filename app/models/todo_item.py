"""
任务计划项模型

用于 LLM 的 todowrite/todoread 工具，按会话/执行维度持久化任务列表。
Agent 模式: ref_type='agent', ref_id=session_id
Flow 模式: ref_type='flow', ref_id=execution_id
"""

from sqlalchemy import String, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class TodoItem(DbBaseModel):
    """
    任务计划项表模型

    每次 LLM 调用 todowrite 时，删除旧数据并批量插入新数据（增量更新策略）。
    """

    __tablename__ = "todo_item"

    ref_type: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="关联类型：agent/flow"
    )
    ref_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="关联ID：session_id 或 execution_id"
    )
    content: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="任务内容"
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        comment="状态：pending/in_progress/completed/cancelled",
    )
    priority: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="medium",
        comment="优先级：high/medium/low",
    )
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="排序位置（数组索引）"
    )

    def __repr__(self) -> str:
        return f"<TodoItem(id={self.id}, ref_type={self.ref_type}, ref_id={self.ref_id}, content={self.content[:20]})>"

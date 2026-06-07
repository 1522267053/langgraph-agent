"""
任务计划项 Schema
"""

from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict
from app.schemas.base_schema import ChinaDateTime


class TodoItemBase(BaseModel):
    """任务计划项基础 Schema"""

    content: str = Field(..., description="任务内容")
    status: str = Field(
        default="pending", description="状态：pending/in_progress/completed/cancelled"
    )
    priority: str = Field(default="medium", description="优先级：high/medium/low")
    position: int = Field(default=0, description="排序位置")


class TodoItemCreate(TodoItemBase):
    """创建任务计划项"""

    pass


class TodoItemUpdate(BaseModel):
    """更新任务计划项"""

    content: Optional[str] = Field(None, description="任务内容")
    status: Optional[str] = Field(None, description="状态")
    priority: Optional[str] = Field(None, description="优先级")
    position: Optional[int] = Field(None, description="排序位置")


class TodoItemResponse(BaseModel):
    """任务计划项响应"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="ID")
    ref_type: str = Field(..., description="关联类型：agent/flow")
    ref_id: int = Field(..., description="关联ID")
    content: str = Field(..., description="任务内容")
    status: str = Field(..., description="状态")
    priority: str = Field(..., description="优先级")
    position: int = Field(..., description="排序位置")
    created_at: Optional[ChinaDateTime] = Field(None, description="创建时间")


class TodoListResponse(BaseModel):
    """任务计划列表响应"""

    ref_type: str = Field(..., description="关联类型")
    ref_id: int = Field(..., description="关联ID")
    todos: List[TodoItemResponse] = Field(default_factory=list, description="任务列表")

"""
统一通知中心相关数据模型
"""

from datetime import datetime
from typing import Optional

from pydantic import Field

from app.schemas.base_schema import BaseView, ChinaDateTime


class NotificationBase(BaseView):
    """通知基础模型"""

    type: Optional[str] = Field(
        default=None,
        description="类型：execution_done/execution_failed/execution_cancelled/human_input/agenda_reminder/reminder",
    )
    title: Optional[str] = Field(default=None, description="标题")
    content: Optional[str] = Field(default=None, description="正文")
    ref_type: Optional[str] = Field(
        default=None, description="关联类型：execution/session/agenda/task"
    )
    ref_id: Optional[int] = Field(default=None, description="关联ID")
    ref_sub_id: Optional[int] = Field(
        default=None, description="辅助关联ID：session 通知存 agent flow_id"
    )
    is_read: Optional[int] = Field(default=None, description="已读标记：0=未读/1=已读")
    read_time: Optional[ChinaDateTime] = Field(default=None, description="已读时间")


class NotificationCreate(NotificationBase):
    """创建通知（主要供服务层内部使用，前端不直接创建）"""

    type: str = Field(..., description="类型")
    title: str = Field(..., description="标题")


class NotificationUpdate(NotificationBase):
    """更新通知（已读标记）"""

    id: int = Field(..., description="通知ID")


class NotificationCondition(BaseView):
    """通知查询条件"""

    type: Optional[str] = Field(default=None, description="类型精确匹配")
    is_read: Optional[int] = Field(default=None, description="已读标记：0=未读/1=已读")
    title: Optional[str] = Field(default=None, description="标题（模糊查询）")
    read_before: Optional[datetime] = Field(
        default=None, description="仅查此时间前创建的通知（预留）"
    )

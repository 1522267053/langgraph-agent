"""
日程管理相关数据模型
"""

from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.base_schema import BaseView, ChinaDateTime


class AgendaBase(BaseView):
    """日程基础模型"""

    title: Optional[str] = Field(None, description="标题")
    description: Optional[str] = Field(None, description="备注")
    start_time: Optional[ChinaDateTime] = Field(None, description="开始时间")
    end_time: Optional[ChinaDateTime] = Field(None, description="结束时间")
    category: Optional[str] = Field(None, description="分类：work/life/study/other")
    priority: Optional[int] = Field(None, description="优先级：1=低/2=中/3=高")
    location: Optional[str] = Field(None, description="地点")
    recurrence: Optional[str] = Field(
        None, description="重复规则：none/daily/weekly/monthly"
    )
    status: Optional[int] = Field(None, description="状态：0=待办/1=进行中/2=已完成")
    completed_at: Optional[ChinaDateTime] = Field(None, description="完成时间")
    color: Optional[str] = Field(None, description="颜色标签")
    remind_at: Optional[ChinaDateTime] = Field(None, description="提醒时间")
    is_reminded: Optional[int] = Field(None, description="是否已推送提醒")


class AgendaCreate(AgendaBase):
    """创建日程"""

    title: str = Field(..., description="标题")


class AgendaUpdate(AgendaBase):
    """更新日程"""

    id: int = Field(..., description="日程ID")


class AgendaCondition(BaseView):
    """日程查询条件"""

    title: Optional[str] = Field(None, description="标题（模糊查询）")
    category: Optional[str] = Field(None, description="分类")
    status: Optional[int] = Field(None, description="状态")
    creator_name: Optional[str] = Field(None, description="创建人名称")
    start_date: Optional[str] = Field(None, description="开始时间范围起（YYYY-MM-DD）")
    end_date: Optional[str] = Field(None, description="开始时间范围止（YYYY-MM-DD）")


class CalendarEventsRequest(BaseModel):
    """日历查询请求"""

    start_date: str = Field(..., description="开始日期（YYYY-MM-DD）")
    end_date: str = Field(..., description="结束日期（YYYY-MM-DD）")

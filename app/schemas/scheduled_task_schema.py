"""
定时任务相关数据模型
"""

from typing import Optional
from pydantic import Field
from app.schemas.base_schema import BaseView, ChinaDateTime


class ScheduledTaskBase(BaseView):
    """定时任务基础模型"""

    name: Optional[str] = Field(None, description="任务名称")
    schedule_type: Optional[str] = Field(
        None, description="调度类型：cron=循环执行, once=执行一次"
    )
    cron_expression: Optional[str] = Field(None, description="Cron表达式")
    run_at: Optional[ChinaDateTime] = Field(
        None, description="单次执行的运行时间（once 模式）"
    )
    target_type: Optional[str] = Field(
        None, description="执行目标类型：self/flow/agent"
    )
    target_id: Optional[int] = Field(None, description="目标ID")
    input_data: Optional[dict] = Field(None, description="预设输入参数")
    is_enabled: Optional[int] = Field(None, description="是否启用：0=禁用, 1=启用")
    next_run_time: Optional[ChinaDateTime] = Field(None, description="下次执行时间")
    last_run_time: Optional[ChinaDateTime] = Field(None, description="上次执行时间")
    last_run_status: Optional[int] = Field(None, description="上次执行状态")


class ScheduledTaskCreate(ScheduledTaskBase):
    """创建定时任务"""

    name: str = Field(..., description="任务名称")
    schedule_type: str = Field("cron", description="调度类型")
    target_type: str = Field(..., description="执行目标类型")


class ScheduledTaskUpdate(ScheduledTaskBase):
    """更新定时任务"""

    id: int = Field(..., description="任务ID")


class ScheduledTaskCondition(BaseView):
    """定时任务查询条件"""

    name: Optional[str] = Field(None, description="任务名称")
    is_enabled: Optional[int] = Field(None, description="是否启用")
    target_type: Optional[str] = Field(None, description="执行目标类型")
    schedule_type: Optional[str] = Field(None, description="调度类型")


class ScheduledTaskLogBase(BaseView):
    """定时任务执行日志基础模型"""

    task_id: Optional[int] = Field(None, description="关联定时任务ID")
    execution_id: Optional[int] = Field(None, description="关联flow_execution.id")
    session_id: Optional[int] = Field(None, description="关联agent_session.id")
    agent_id: Optional[int] = Field(None, description="目标Agent的flow_id")
    status: Optional[int] = Field(None, description="状态：0=运行中, 1=成功, 2=失败")
    trigger_type: Optional[int] = Field(None, description="触发类型：1=定时, 2=手动")
    start_time: Optional[ChinaDateTime] = Field(None, description="开始时间")
    end_time: Optional[ChinaDateTime] = Field(None, description="结束时间")
    duration_ms: Optional[int] = Field(None, description="执行耗时（毫秒）")
    error_message: Optional[str] = Field(None, description="错误信息")
    input_snapshot: Optional[dict] = Field(None, description="输入参数快照")


class ScheduledTaskLogCondition(BaseView):
    """定时任务日志查询条件"""

    task_id: Optional[int] = Field(None, description="关联定时任务ID")
    status: Optional[int] = Field(None, description="状态")
    trigger_type: Optional[int] = Field(None, description="触发类型")

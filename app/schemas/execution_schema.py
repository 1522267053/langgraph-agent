"""
执行记录相关数据模型
"""

from typing import Optional, List
from pydantic import Field
from app.schemas.base_schema import BaseView, ChinaDateTime


class FlowExecutionBase(BaseView):
    """流程执行记录基础模型"""

    flow_id: Optional[int] = Field(None, description="流程ID")
    flow_name: Optional[str] = Field(None, description="流程名称")
    status: Optional[int] = Field(
        None, description="状态：0=待执行，1=执行中，2=成功，3=失败，4=已取消"
    )
    input_data: Optional[dict] = Field(None, description="输入数据(JSON)")
    output_data: Optional[dict] = Field(None, description="输出数据(JSON)")
    error_message: Optional[str] = Field(None, description="错误信息")
    start_time: Optional[ChinaDateTime] = Field(None, description="开始时间")
    end_time: Optional[ChinaDateTime] = Field(None, description="结束时间")
    files: Optional[list] = Field(None, description="附件文件信息")


class FlowExecutionCreate(BaseView):
    """创建流程执行记录"""

    flow_id: int = Field(..., description="流程ID")
    input_data: Optional[dict] = Field(None, description="输入数据")


class NodeExecutionBase(BaseView):
    """节点执行记录基础模型"""

    flow_execution_id: Optional[int] = Field(None, description="流程执行记录ID")
    node_key: Optional[str] = Field(None, description="节点唯一标识")
    node_type: Optional[str] = Field(None, description="节点类型")
    node_name: Optional[str] = Field(None, description="节点名称")
    status: Optional[int] = Field(
        None, description="状态：0=待执行，1=执行中，2=成功，3=失败，4=跳过，5=已取消"
    )
    input_data: Optional[dict] = Field(None, description="输入数据(JSON)")
    output_data: Optional[dict] = Field(None, description="输出数据(JSON)")
    error_message: Optional[str] = Field(None, description="错误信息")
    start_time: Optional[ChinaDateTime] = Field(None, description="开始时间")
    end_time: Optional[ChinaDateTime] = Field(None, description="结束时间")
    execution_steps: Optional[List[dict]] = Field(
        None, description="执行步骤记录(JSON)"
    )
    prompt_tokens: Optional[int] = Field(None, description="输入token数")
    completion_tokens: Optional[int] = Field(None, description="输出token数")
    total_tokens: Optional[int] = Field(None, description="总token数")


class ExecutionInput(BaseView):
    """执行输入参数"""

    input_data: Optional[dict] = Field(default=None, description="输入数据")
    files: Optional[list] = Field(default=None, description="附件文件信息")

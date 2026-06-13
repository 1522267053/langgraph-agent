"""Token 消耗统计 Schema"""

from typing import Optional
from pydantic import BaseModel, Field


class TokenStatisticsQuery(BaseModel):
    """统计查询参数"""

    start_date: Optional[str] = Field(None, description="开始日期 YYYY-MM-DD")
    end_date: Optional[str] = Field(None, description="结束日期 YYYY-MM-DD")
    time_grain: str = Field("day", description="聚合粒度：day/week/month")


class TokenOverview(BaseModel):
    """Token 概览"""

    total_prompt_tokens: int = Field(0, description="总输入token数")
    total_completion_tokens: int = Field(0, description="总输出token数")
    total_tokens: int = Field(0, description="总token数")
    llm_call_count: int = Field(0, description="LLM调用次数")


class TokenTrendItem(BaseModel):
    """时间趋势项"""

    date: str = Field("", description="日期标签")
    prompt_tokens: int = Field(0, description="输入token数")
    completion_tokens: int = Field(0, description="输出token数")
    total_tokens: int = Field(0, description="总token数")


class TokenByFlowItem(BaseModel):
    """按流程/Agent统计项"""

    flow_id: int = Field(0, description="流程ID")
    flow_name: str = Field("", description="流程名称")
    flow_type: str = Field("", description="类型：flow/agent")
    prompt_tokens: int = Field(0, description="输入token数")
    completion_tokens: int = Field(0, description="输出token数")
    total_tokens: int = Field(0, description="总token数")
    call_count: int = Field(0, description="LLM调用次数")


class TokenByModelItem(BaseModel):
    """按模型统计项"""

    model: str = Field("", description="模型名称")
    provider: str = Field("", description="供应商标识")
    prompt_tokens: int = Field(0, description="输入token数")
    completion_tokens: int = Field(0, description="输出token数")
    total_tokens: int = Field(0, description="总token数")
    call_count: int = Field(0, description="LLM调用次数")
    cache_read_tokens: int = Field(0, description="缓存读取token数")
    cache_write_tokens: int = Field(0, description="缓存写入token数")
    reasoning_tokens: int = Field(0, description="推理token数")

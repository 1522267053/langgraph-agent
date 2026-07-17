"""
AI 供应商与模型 Schema
"""

from typing import Optional
from pydantic import Field
from app.schemas.base_schema import BaseView


class AIProviderBase(BaseView):
    """AI 供应商基础模型"""

    provider_id: str = Field(..., description="供应商标识")
    name: str = Field(..., description="供应商名称")
    api_url: Optional[str] = Field(None, description="API 地址")
    doc_url: Optional[str] = Field(None, description="文档地址")
    env_vars: Optional[dict] = Field(None, description="所需环境变量列表")
    npm_package: Optional[str] = Field(None, description="npm 包名")
    adapter_type: str = Field("openai_compatible", description="适配器类型")


class AIProviderCreate(AIProviderBase):
    """创建 AI 供应商"""

    pass


class AIProviderUpdate(AIProviderBase):
    """更新 AI 供应商"""

    pass


class AIModelBase(BaseView):
    """AI 模型基础模型"""

    model_id: str = Field(..., description="模型标识")
    name: str = Field(..., description="模型名称")
    description: Optional[str] = Field(None, description="模型描述")
    provider_id: str = Field(..., description="供应商标识")
    provider_name: Optional[str] = Field(None, description="供应商名称（冗余展示）")
    modalities: Optional[dict] = Field(None, description="输入输出模态")
    limits: Optional[dict] = Field(None, description="上下文限制")
    cost: Optional[dict] = Field(None, description="费用")
    reasoning: int = Field(0, description="是否支持推理")
    tool_call: int = Field(0, description="是否支持函数调用")
    temperature: int = Field(0, description="是否支持温度")
    attachment: int = Field(0, description="是否支持附件")
    open_weights: int = Field(0, description="是否开源权重")
    is_experimental: int = Field(0, description="是否实验性")
    structured_output: int = Field(0, description="是否支持结构化输出")
    reasoning_options: Optional[list] = Field(None, description="推理选项配置")
    knowledge: Optional[str] = Field(None, description="知识截止日期")
    release_date: Optional[str] = Field(None, description="发布日期")
    last_updated: Optional[str] = Field(None, description="最后更新日期")
    family: Optional[str] = Field(None, description="模型家族")
    status: Optional[str] = Field(None, description="模型状态")


class AIModelCreate(AIModelBase):
    """创建 AI 模型"""

    pass


class AIModelUpdate(AIModelBase):
    """更新 AI 模型"""

    pass

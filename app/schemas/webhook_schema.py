"""
Webhook 配置 Schema
"""

from typing import Optional
from pydantic import Field

from app.schemas.base_schema import BaseView, ChinaDateTime


class WebhookConfigBase(BaseView):
    """Webhook 配置基础视图"""

    flow_id: Optional[int] = Field(None, description="关联流程ID")
    name: Optional[str] = Field(None, description="Webhook 名称")
    token: Optional[str] = Field(None, description="唯一令牌")
    description: Optional[str] = Field(None, description="描述")
    input_config: Optional[dict] = Field(None, description="默认输入参数模板")
    callback_url: Optional[str] = Field(None, description="回调 URL")
    is_enabled: Optional[int] = Field(None, description="是否启用")
    call_count: Optional[int] = Field(None, description="调用次数")
    last_call_time: Optional[ChinaDateTime] = Field(None, description="最后调用时间")


class WebhookConfigCreate(BaseView):
    """创建 Webhook"""

    flow_id: int = Field(..., description="关联流程ID")
    name: str = Field(..., description="Webhook 名称")
    description: Optional[str] = Field(None, description="描述")
    input_config: Optional[dict] = Field(None, description="默认输入参数模板")
    callback_url: Optional[str] = Field(None, description="回调 URL")
    is_enabled: int = Field(1, description="是否启用")


class WebhookConfigUpdate(BaseView):
    """更新 Webhook"""

    id: int = Field(..., description="ID")
    flow_id: Optional[int] = Field(None, description="关联流程ID")
    name: Optional[str] = Field(None, description="Webhook 名称")
    description: Optional[str] = Field(None, description="描述")
    input_config: Optional[dict] = Field(None, description="默认输入参数模板")
    callback_url: Optional[str] = Field(None, description="回调 URL")
    is_enabled: Optional[int] = Field(None, description="是否启用")


class WebhookConfigCondition(BaseView):
    """查询条件"""

    name: Optional[str] = Field(None, description="名称关键词")
    flow_id: Optional[int] = Field(None, description="流程ID")
    is_enabled: Optional[int] = Field(None, description="是否启用")

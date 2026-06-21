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


# ---- Webhook 调用记录 Schema ----


class WebhookCallRecordBase(BaseView):
    """Webhook 调用记录基础视图"""

    webhook_id: Optional[int] = Field(None, description="关联 webhook_config.id")
    flow_id: Optional[int] = Field(None, description="关联 flow.id")
    ref_type: Optional[str] = Field(None, description="引用类型：session/execution")
    ref_id: Optional[int] = Field(None, description="引用ID")
    input_data: Optional[dict] = Field(None, description="输入数据快照")
    status: Optional[int] = Field(None, description="状态")
    output_data: Optional[dict] = Field(None, description="输出数据")
    error_message: Optional[str] = Field(None, description="错误信息")
    callback_status: Optional[str] = Field(None, description="回调状态")
    started_at: Optional[ChinaDateTime] = Field(None, description="触发时间")
    finished_at: Optional[ChinaDateTime] = Field(None, description="完成时间")


class WebhookCallRecordResponse(WebhookCallRecordBase):
    """调用记录响应（含消息摘要）"""

    message_count: Optional[int] = Field(None, description="消息数量")


class WebhookCallRecordListResponse(BaseView):
    """调用记录列表响应"""

    total: int = Field(0, description="总数")
    items: list[WebhookCallRecordResponse] = Field(
        default_factory=list, description="列表"
    )


class WebhookCallRecordPageRequest(BaseView):
    """调用记录分页请求"""

    page: int = Field(1, description="页码")
    page_size: int = Field(20, description="每页条数")


class WebhookMessageResponse(BaseView):
    """Webhook 查询消息响应"""

    id: Optional[int] = Field(None, description="消息ID")
    role: Optional[str] = Field(None, description="角色")
    content: Optional[str] = Field(None, description="内容")
    thinking: Optional[str] = Field(None, description="思考内容")
    tool_calls: Optional[list] = Field(None, description="工具调用")
    tool_call_id: Optional[str] = Field(None, description="工具调用ID")
    status: Optional[str] = Field(None, description="状态")
    sequence: Optional[int] = Field(None, description="排序序号")
    created_at: Optional[ChinaDateTime] = Field(None, description="创建时间")


class WebhookMessageListResponse(BaseView):
    """消息列表响应"""

    total: int = Field(0, description="总数")
    items: list[WebhookMessageResponse] = Field(
        default_factory=list, description="列表"
    )


class WebhookMessagePageRequest(BaseView):
    """消息分页请求"""

    before_id: Optional[int] = Field(None, description="游标ID（返回此ID之前的消息）")
    limit: int = Field(20, description="每页条数")

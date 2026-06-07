"""
Agent相关的Pydantic Schema
"""

from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict
from app.schemas.base_schema import ChinaDateTime


class AgentSessionBase(BaseModel):
    """Agent会话基础Schema"""

    title: str = Field(default="新对话", description="会话标题")


class AgentSessionCreate(AgentSessionBase):
    """创建Agent会话"""

    pass


class AgentSessionUpdate(BaseModel):
    """更新Agent会话"""

    title: Optional[str] = Field(None, description="会话标题")


class AgentSessionResponse(AgentSessionBase):
    """Agent会话响应"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="会话ID")
    flow_id: int = Field(..., description="关联的Agent Flow ID")
    status: int = Field(..., description="状态：1=活跃，0=已归档")
    created_at: Optional[ChinaDateTime] = Field(None, description="创建时间")
    updated_at: Optional[ChinaDateTime] = Field(None, description="更新时间")


class AgentMessageBase(BaseModel):
    """Agent消息基础Schema"""

    role: str = Field(..., description="system/user/assistant/tool")
    content: str = Field(..., description="消息内容")
    thinking: Optional[str] = Field(None, description="思考内容")
    tool_calls: Optional[List[dict]] = Field(None, description="工具调用列表")
    tool_call_id: Optional[str] = Field(None, description="工具调用ID")
    status: Optional[str] = Field(None, description="工具执行状态：success/error")
    prompt_tokens: Optional[int] = Field(None, description="输入token数")
    completion_tokens: Optional[int] = Field(None, description="输出token数")
    total_tokens: Optional[int] = Field(None, description="总token数")
    files: Optional[List[dict]] = Field(None, description="附件文件列表")
    """创建Agent消息"""

    pass


class AgentMessageResponse(AgentMessageBase):
    """Agent消息响应"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="消息ID")
    session_id: int = Field(..., description="会话ID")
    sequence: int = Field(..., description="排序序号")
    created_at: Optional[ChinaDateTime] = Field(None, description="创建时间")


class AgentChatRequest(BaseModel):
    """Agent聊天请求"""

    content: str = Field(..., description="用户消息内容")
    params: dict = Field(default_factory=dict, description="扩展参数（含文件字段）")


class AgentResumeRequest(BaseModel):
    """Agent恢复执行请求"""

    human_input: str = Field(..., description="人工输入内容")


class AgentSessionListResponse(BaseModel):
    """Agent会话列表响应"""

    total: int = Field(..., description="总数")
    list: List[AgentSessionResponse] = Field(..., description="会话列表")


class AgentMessageListResponse(BaseModel):
    """Agent消息列表响应"""

    total: int = Field(..., description="总数")
    list: List[AgentMessageResponse] = Field(..., description="消息列表")


class AgentFlowResponse(BaseModel):
    """Agent Flow响应（简化版）"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Flow ID")
    name: str = Field(..., description="名称")
    description: Optional[str] = Field(None, description="描述")
    flow_type: str = Field(..., description="类型")
    status: int = Field(..., description="状态")
    is_builtin: Optional[int] = Field(0, description="是否内置")
    input_schema: Optional[dict] = Field(None, description="输入参数定义")
    created_at: Optional[ChinaDateTime] = Field(None, description="创建时间")
    updated_at: Optional[ChinaDateTime] = Field(None, description="更新时间")


class AgentCompressResponse(BaseModel):
    """上下文压缩响应"""

    summary: Optional[str] = Field(None, description="压缩摘要内容")
    kept_count: int = Field(0, description="保留的消息数")
    removed_count: int = Field(0, description="被压缩的消息数")


class AgentFlowListResponse(BaseModel):
    """Agent Flow列表响应"""

    total: int = Field(..., description="总数")
    list: List[AgentFlowResponse] = Field(..., description="Agent列表")

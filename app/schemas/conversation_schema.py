"""
对话消息相关数据模型
"""

from typing import Optional, Any
from pydantic import Field, field_validator
from fastapi.exceptions import RequestValidationError
from app.schemas.base_schema import BaseView


class ConversationMessageBase(BaseView):
    """对话消息基础模型"""

    execution_id: Optional[int] = Field(None, description="执行记录ID")
    node_key: Optional[str] = Field(None, description="节点标识")
    role: Optional[str] = Field(None, description="消息角色：system/human/ai/tool")
    content: Optional[str] = Field(None, description="消息内容")
    tool_calls: Optional[list[dict[str, Any]]] = Field(None, description="工具调用请求")
    tool_call_id: Optional[str] = Field(None, description="工具调用ID")
    name: Optional[str] = Field(None, description="工具名称")
    status: Optional[str] = Field(None, description="工具执行状态：success/error")
    prompt_tokens: Optional[int] = Field(None, description="输入token数")
    completion_tokens: Optional[int] = Field(None, description="输出token数")
    total_tokens: Optional[int] = Field(None, description="总token数")
    sequence: Optional[int] = Field(0, description="消息序号")

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: Optional[str]) -> Optional[str]:
        """校验消息角色"""
        if v is None:
            return v
        valid_roles = ["system", "human", "ai", "tool"]
        if v not in valid_roles:
            raise RequestValidationError(f"消息角色必须是: {', '.join(valid_roles)}")
        return v


class ConversationMessageCreate(ConversationMessageBase):
    """创建对话消息"""

    execution_id: int = Field(..., description="执行记录ID")
    node_key: str = Field(..., description="节点标识")
    role: str = Field(..., description="消息角色")


class ConversationMessageUpdate(ConversationMessageBase):
    """更新对话消息"""

    pass


class HumanInputRequest(BaseView):
    """人工输入请求"""

    execution_id: Optional[int] = Field(None, description="执行记录ID")
    prompt: Optional[str] = Field(None, description="提示信息")
    input_variables: Optional[list[dict[str, Any]]] = Field(
        None, description="输入变量"
    )
    output_variable: Optional[str] = Field("human_feedback", description="输出变量名")
    timeout: Optional[int] = Field(600, description="超时秒数")


class HumanInputSubmit(BaseView):
    """提交人工输入"""

    execution_id: Optional[int] = Field(None, description="执行记录ID")
    input: str = Field(..., description="用户输入内容")

    @field_validator("input")
    @classmethod
    def validate_input(cls, v: str) -> str:
        """校验用户输入"""
        if not v or not v.strip():
            raise RequestValidationError("用户输入不能为空")
        if len(v) > 10000:
            raise RequestValidationError("用户输入不能超过10000个字符")
        return v

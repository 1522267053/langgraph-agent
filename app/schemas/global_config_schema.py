"""
全局配置 Schema
"""

from typing import Optional
from pydantic import BaseModel, Field


class InitConfigRequest(BaseModel):
    """初始化配置请求"""

    provider: str = Field(..., description="供应商标识")
    api_key: str = Field(..., description="API Key")
    model: str = Field(..., description="模型名称")
    base_url: Optional[str] = Field(
        None, description="自定义 Base URL（为空则使用供应商默认）"
    )
    context_length: Optional[int] = Field(
        None, description="模型上下文窗口大小（token数）"
    )
    embedding_api_key: Optional[str] = Field(None, description="向量模型 API Key")
    embedding_base_url: Optional[str] = Field(None, description="向量模型 Base URL")
    embedding_model: Optional[str] = Field(None, description="向量模型名称")
    login_password: str = Field(..., description="登录密码哈希（SHA-256）")
    login_username: str = Field(..., description="登录用户名（明文）")


class UpdateConfigRequest(BaseModel):
    """更新配置请求"""

    provider: Optional[str] = Field(None, description="供应商标识")
    api_key: Optional[str] = Field(None, description="API Key")
    model: Optional[str] = Field(None, description="模型名称")
    base_url: Optional[str] = Field(None, description="自定义 Base URL")
    context_length: Optional[int] = Field(
        None, description="模型上下文窗口大小（token数）"
    )
    embedding_api_key: Optional[str] = Field(None, description="向量模型 API Key")
    embedding_base_url: Optional[str] = Field(None, description="向量模型 Base URL")
    embedding_model: Optional[str] = Field(None, description="向量模型名称")
    login_password: Optional[str] = Field(None, description="新登录密码哈希（SHA-256）")
    login_username: Optional[str] = Field(None, description="新登录用户名（明文）")
    current_password: Optional[str] = Field(
        None, description="当前密码哈希（修改密码时必填，用于验证身份）"
    )
    execution_notification_enabled: Optional[bool] = Field(
        None, description="是否启用执行完成通知"
    )


class GlobalConfigResponse(BaseModel):
    """全局配置响应"""

    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    api_key_masked: Optional[str] = None
    context_length: Optional[int] = None
    embedding_model: Optional[str] = None
    embedding_api_key_masked: Optional[str] = None
    embedding_base_url: Optional[str] = None
    has_password: bool = False
    has_username: bool = False
    username: Optional[str] = None
    execution_notification_enabled: bool = True


class CheckInitResponse(BaseModel):
    """初始化状态检查响应"""

    initialized: bool = False


class LoginRequest(BaseModel):
    """登录请求"""

    username: str = Field(..., description="用户名（明文）")
    password: str = Field(..., description="登录密码哈希（SHA-256）")


class AuthCheckResponse(BaseModel):
    """认证状态检查响应"""

    need_login: bool = False
    authenticated: bool = False
    has_username: bool = False
    username: Optional[str] = None

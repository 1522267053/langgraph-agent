"""
AI 供应商连接 Schema
"""

from typing import Optional

from pydantic import Field

from app.schemas.base_schema import BaseView


def mask_api_key(value: Optional[str]) -> Optional[str]:
    """API Key 脱敏：保留前 4 后 4 位，短 key 全遮盖"""
    if not value:
        return None
    if len(value) > 8:
        return value[:4] + "****" + value[-4:]
    return "****"


class AIProviderConnectionBase(BaseView):
    """供应商连接基础模型"""

    provider_id: Optional[str] = Field(default=None, description="供应商标识")
    provider_name: Optional[str] = Field(default=None, description="供应商名称")
    api_key: Optional[str] = Field(
        default=None, description="API Key（写入用明文，响应中脱敏）"
    )
    base_url: Optional[str] = Field(
        default=None, description="API 地址覆盖（空则回退供应商默认）"
    )
    default_model: Optional[str] = Field(default=None, description="该连接的默认模型")
    context_length: Optional[int] = Field(
        default=None, description="默认模型上下文窗口（token 数）"
    )
    is_default: Optional[int] = Field(
        default=0, description="是否全局默认连接：0=否，1=是"
    )
    is_enabled: Optional[int] = Field(default=1, description="是否启用：0=禁用，1=启用")
    remark: Optional[str] = Field(default=None, description="备注")

    @classmethod
    def model_to_view(cls, model_instance):
        """模型转视图：api_key 原地脱敏，明文不外泄"""
        view = super().model_to_view(model_instance)
        view.api_key = mask_api_key(view.api_key)
        return view


class AIProviderConnectionCreate(AIProviderConnectionBase):
    """创建供应商连接"""

    provider_id: str = Field(..., description="供应商标识")
    provider_name: str = Field(default="", description="供应商名称（空则自动补全）")


class AIProviderConnectionUpdate(AIProviderConnectionBase):
    """更新供应商连接（api_key 为空/None 时不覆盖原值）"""

    pass


class AIProviderConnectionQuery(BaseView):
    """供应商连接查询条件"""

    provider_id: Optional[str] = Field(default=None, description="供应商标识")
    is_default: Optional[int] = Field(default=None, description="是否默认连接")
    is_enabled: Optional[int] = Field(default=None, description="是否启用")

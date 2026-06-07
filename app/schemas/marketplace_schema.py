"""
市场相关 Schema
"""

from typing import Optional

from pydantic import BaseModel, Field


class MarketplaceConfigRequest(BaseModel):
    """市场服务器配置请求"""

    server_url: str = Field("", description="市场服务器地址")


class MarketplaceResourceListRequest(BaseModel):
    """市场资源列表查询请求"""

    resource_type: Optional[str] = Field(None, description="资源类型")
    category: Optional[str] = Field(None, description="分类")
    keyword: Optional[str] = Field(None, description="搜索关键词")
    page: Optional[int] = Field(None, description="页码")
    page_size: Optional[int] = Field(None, description="每页数量")

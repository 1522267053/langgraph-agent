"""
记忆 Schema

三层记忆架构：hot（热）/ warm（温）/ cold（冷）
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.base_schema import BaseView


class MemoryExportItem(BaseModel):
    """导出/导入的记忆项（不含服务端自生成字段）"""

    title: str = Field(..., description="标题")
    content: str = Field(..., description="内容")
    memory_type: str = Field("cold", description="记忆层级")
    category: str = Field("other", description="分类")
    importance: int = Field(3, description="重要程度")
    keywords: Optional[str] = Field(None, description="关键词")


class MemoryExportRequest(BaseModel):
    agent_id: int = Field(..., description="Agent ID")
    ids: Optional[list[int]] = Field(None, description="指定ID导出，空=全部")
    tier: Optional[str] = Field(None, description="按层级过滤")


class MemoryImportRequest(BaseModel):
    agent_id: int = Field(..., description="目标 Agent ID")
    memories: list[MemoryExportItem] = Field(..., description="导入的记忆列表")


class MemoryImportResponse(BaseModel):
    total: int = Field(0, description="导入总数")
    imported: int = Field(0, description="成功数")
    failed: int = Field(0, description="失败数")
    errors: list[dict] = Field(default_factory=list, description="失败详情")


class MemoryView(BaseView):
    agent_id: Optional[int] = Field(None, description="所属Agent ID")
    memory_type: Optional[str] = Field(None, description="记忆层级：hot/warm/cold")
    category: Optional[str] = Field(None, description="分类")
    title: Optional[str] = Field(None, description="标题")
    content: Optional[str] = Field(None, description="内容")
    keywords: Optional[str] = Field(None, description="关键词")
    metadata_: Optional[dict] = Field(None, alias="metadata", description="扩展元数据")
    source_session_id: Optional[str] = Field(None, description="来源会话ID")
    importance: Optional[int] = Field(None, description="重要程度")
    access_count: Optional[int] = Field(None, description="访问次数")
    peak_tier: Optional[str] = Field(None, description="记忆达到过的最高层级")
    last_access_time: Optional[datetime] = Field(None, description="最后访问时间")

    model_config = BaseView.model_config


class MemoryCreate(BaseView):
    agent_id: int = Field(..., description="所属Agent ID")
    memory_type: str = Field("cold", description="记忆层级：hot/warm/cold")
    category: str = Field("other", description="分类")
    title: str = Field(..., max_length=50, description="标题")
    content: str = Field(..., max_length=500, description="内容")
    keywords: Optional[str] = Field(None, description="关键词")
    metadata_: Optional[dict] = Field(None, alias="metadata", description="扩展元数据")
    source_session_id: Optional[str] = Field(None, description="来源会话ID")
    importance: int = Field(3, description="重要程度")


class MemoryUpdate(BaseView):
    id: int = Field(..., description="ID")
    memory_type: Optional[str] = Field(None, description="记忆层级")
    category: Optional[str] = Field(None, description="分类")
    title: Optional[str] = Field(None, max_length=50, description="标题")
    content: Optional[str] = Field(None, max_length=500, description="内容")
    keywords: Optional[str] = Field(None, description="关键词")
    metadata_: Optional[dict] = Field(None, alias="metadata", description="扩展元数据")
    importance: Optional[int] = Field(None, description="重要程度")


class MemoryCondition(BaseView):
    agent_id: Optional[int] = Field(None, description="所属Agent ID")
    memory_type: Optional[str] = Field(None, description="记忆层级")
    category: Optional[str] = Field(None, description="分类")
    title: Optional[str] = Field(None, description="标题(模糊搜索)")


class MemorySearchRequest(BaseModel):
    """记忆语义搜索请求（向量优先，降级为关键词匹配）"""

    agent_id: int = Field(..., description="Agent ID")
    query: str = Field(..., min_length=1, description="搜索关键词")
    tier: Optional[str] = Field(None, description="按层级过滤")
    max_results: int = Field(20, ge=1, le=100, description="最大返回数")

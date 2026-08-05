"""
知识沉淀 Schema
"""

from typing import Optional, List
from pydantic import Field

from app.schemas.base_schema import BaseView


class KnowledgeInsightView(BaseView):
    knowledge_base_id: Optional[int] = Field(default=None, description="所属知识库ID")
    question: Optional[str] = Field(default=None, description="触发问题/查询")
    answer: Optional[str] = Field(default=None, description="AI生成的知识沉淀内容")
    keywords: Optional[str] = Field(default=None, description="关键词")
    source_segment_ids: Optional[List[int]] = Field(
        default=None, description="关联段落ID列表"
    )

    model_config = BaseView.model_config


class KnowledgeInsightCreate(BaseView):
    knowledge_base_id: int = Field(..., description="所属知识库ID")
    question: str = Field(..., description="触发问题/查询")
    answer: str = Field(..., description="AI生成的知识沉淀内容")
    keywords: Optional[str] = Field(default=None, description="关键词")
    source_segment_ids: Optional[List[int]] = Field(
        default=None, description="关联段落ID列表"
    )


class KnowledgeInsightUpdate(BaseView):
    id: int = Field(..., description="ID")
    knowledge_base_id: Optional[int] = Field(default=None, description="所属知识库ID")
    question: Optional[str] = Field(default=None, description="触发问题/查询")
    answer: Optional[str] = Field(default=None, description="AI生成的知识沉淀内容")
    keywords: Optional[str] = Field(default=None, description="关键词")
    source_segment_ids: Optional[List[int]] = Field(
        default=None, description="关联段落ID列表"
    )


class KnowledgeInsightCondition(BaseView):
    knowledge_base_id: Optional[int] = Field(default=None, description="所属知识库ID")
    question: Optional[str] = Field(default=None, description="触发问题(模糊搜索)")
    keywords: Optional[str] = Field(default=None, description="关键词(模糊搜索)")

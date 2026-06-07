"""
知识库 API 路由
处理知识库和文档相关的路由定义
"""

from fastapi import APIRouter

from app.api.base_api import BaseApi
from app.models.knowledge_base import KnowledgeBase
from app.services.knowledge_base_service import knowledge_base_service
from app.schemas.knowledge_schema import (
    KnowledgeBaseBase,
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
)


class KnowledgeBaseApi(
    BaseApi[
        KnowledgeBase,
        KnowledgeBaseBase,
        KnowledgeBaseBase,
        KnowledgeBaseCreate,
        KnowledgeBaseUpdate,
    ]
):
    """知识库 API"""

    def __init__(self):
        super().__init__(
            service=knowledge_base_service,
            router_prefix="/api/knowledge/base",
            router_tags=["知识库管理"],
        )


knowledge_base_api = KnowledgeBaseApi()
router = APIRouter()
router.include_router(knowledge_base_api.router)

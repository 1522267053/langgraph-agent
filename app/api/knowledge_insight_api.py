"""
知识沉淀 API 路由
"""

from app.api.base_api import BaseApi
from app.models.knowledge_insight import KnowledgeInsight
from app.services.knowledge_insight_service import knowledge_insight_service
from app.schemas.knowledge_insight_schema import (
    KnowledgeInsightView,
    KnowledgeInsightCondition,
    KnowledgeInsightCreate,
    KnowledgeInsightUpdate,
)


class KnowledgeInsightApi(
    BaseApi[
        KnowledgeInsight,
        KnowledgeInsightView,
        KnowledgeInsightCondition,
        KnowledgeInsightCreate,
        KnowledgeInsightUpdate,
    ]
):
    def __init__(self):
        super().__init__(
            service=knowledge_insight_service,
            router_prefix="/api/knowledge/insight",
            router_tags=["知识沉淀"],
        )


knowledge_insight_api = KnowledgeInsightApi()
router = knowledge_insight_api.router

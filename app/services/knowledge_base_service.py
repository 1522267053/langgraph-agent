"""
知识库服务
"""

from sqlalchemy import or_
from app.models.knowledge_base import KnowledgeBase
from app.schemas.knowledge_schema import KnowledgeBaseCreate, KnowledgeBaseUpdate
from app.services.base_service import BaseService


class KnowledgeBaseService(
    BaseService[KnowledgeBase, KnowledgeBaseCreate, KnowledgeBaseUpdate]
):
    """
    知识库服务类
    """

    def __init__(self):
        super().__init__(KnowledgeBase)

    def _apply_filters(self, query, count_query, condition):
        """
        应用查询条件
        支持名称模糊搜索
        """
        query, count_query = super()._apply_filters(query, count_query, condition)

        if condition and hasattr(condition, "name") and condition.name:
            keyword = f"%{condition.name}%"
            if query is not None:
                query = query.where(
                    or_(
                        KnowledgeBase.name.like(keyword),
                        KnowledgeBase.description.like(keyword),
                    )
                )
            if count_query is not None:
                count_query = count_query.where(
                    or_(
                        KnowledgeBase.name.like(keyword),
                        KnowledgeBase.description.like(keyword),
                    )
                )

        return query, count_query


knowledge_base_service = KnowledgeBaseService()

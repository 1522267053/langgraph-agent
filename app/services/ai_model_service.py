"""
AI 模型服务
"""

from typing import List, Optional, Tuple

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_model import AIModel
from app.models.ai_provider import AIProvider
from app.services.base_service import BaseService
from app.schemas.ai_model_provider_schema import AIModelCreate, AIModelUpdate


class AIModelService(BaseService[AIModel, AIModelCreate, AIModelUpdate]):
    def __init__(self):
        super().__init__(AIModel)

    async def get_by_model_id(
        self, db: AsyncSession, model_id: str
    ) -> Optional[AIModel]:
        query = select(AIModel).where(
            AIModel.model_id == model_id,
            AIModel.is_delete == 0,
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_by_provider(
        self, db: AsyncSession, provider_id: str
    ) -> List[AIModel]:
        query = (
            select(AIModel)
            .where(
                AIModel.provider_id == provider_id,
                AIModel.is_delete == 0,
            )
            .order_by(AIModel.name)
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_by_provider_with_name(
        self, db: AsyncSession, provider_id: str
    ) -> List[dict]:
        models = await self.get_by_provider(db, provider_id)
        provider = None
        if models:
            provider_query = select(AIProvider.name).where(
                AIProvider.provider_id == provider_id,
                AIProvider.is_delete == 0,
            )
            provider_result = await db.execute(provider_query)
            provider = provider_result.scalar_one_or_none()
        provider_name = provider or provider_id

        result: List[dict] = []
        for m in models:
            try:
                from app.schemas.ai_model_provider_schema import AIModelBase

                view = AIModelBase.model_to_view(m)
                item = view.model_dump()
            except Exception:
                item = {
                    "model_id": m.model_id,
                    "name": m.name,
                    "description": m.description,
                    "provider_id": m.provider_id,
                    "modalities": m.modalities,
                    "limits": m.limits,
                    "cost": m.cost,
                    "reasoning": m.reasoning,
                    "tool_call": m.tool_call,
                    "temperature": m.temperature,
                    "attachment": m.attachment,
                    "open_weights": m.open_weights,
                    "is_experimental": m.is_experimental,
                    "structured_output": m.structured_output,
                    "reasoning_options": m.reasoning_options,
                    "knowledge": m.knowledge,
                    "release_date": m.release_date,
                    "last_updated": m.last_updated,
                    "family": m.family,
                    "status": m.status,
                }
            item["provider_name"] = provider_name
            result.append(item)
        return result

    def _apply_filters(
        self,
        query: Optional[Select],
        count_query: Optional[Select],
        condition: Optional[AIModel],
    ) -> Tuple[Optional[Select], Optional[Select]]:
        query, count_query = super()._apply_filters(query, count_query, condition)
        if not condition:
            return query, count_query
        if hasattr(condition, "provider_id") and condition.provider_id:
            query, count_query = self._apply_like_filter(
                query, count_query, "provider_id", condition.provider_id
            )
        if hasattr(condition, "name") and condition.name:
            query, count_query = self._apply_like_filter(
                query, count_query, "name", condition.name
            )
        if hasattr(condition, "model_id") and condition.model_id:
            query, count_query = self._apply_like_filter(
                query, count_query, "model_id", condition.model_id
            )
        return query, count_query


ai_model_service = AIModelService()

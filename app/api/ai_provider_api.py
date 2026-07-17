"""
AI 供应商 API 路由

提供供应商列表、模型列表和同步触发接口
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.schemas.base_schema import ApiResponse
from app.services.ai_provider_service import ai_provider_service, _get_virtual_provider_dicts
from app.services.ai_model_service import ai_model_service


class AiProviderApi:
    def __init__(self):
        self.router = APIRouter(prefix="/api/ai-provider", tags=["AI供应商"])
        self._register_routes()

    def _register_routes(self):
        self.router.add_api_route("/list", self.list_providers, methods=["GET"])
        self.router.add_api_route(
            "/models/{provider_id}", self.get_models, methods=["GET"]
        )
        self.router.add_api_route("/sync", self.sync, methods=["POST"])

    async def list_providers(self, db: AsyncSession = Depends(get_db)):
        providers = await ai_provider_service.list_providers(db)
        data = _get_virtual_provider_dicts() + [
            {
                "name": p.provider_id,
                "label": p.name,
                "default_base_url": p.api_url or "",
                "api_url": p.api_url or "",
                "adapter_type": p.adapter_type,
                "env_vars": p.env_vars,
            }
            for p in providers
        ]
        return ApiResponse.success(data=data)

    async def get_models(self, provider_id: str, db: AsyncSession = Depends(get_db)):
        models = await ai_model_service.get_by_provider_with_name(db, provider_id)
        return ApiResponse.success(data=models)

    async def sync(self):
        await ai_provider_service.sync_from_url()
        return ApiResponse.success(msg="同步成功")


ai_provider_api = AiProviderApi()
router = ai_provider_api.router

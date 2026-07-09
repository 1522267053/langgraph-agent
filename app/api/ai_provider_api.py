"""
AI 提供商 API 路由
"""

from fastapi import APIRouter

from app.agent_flow.ai_provider.base import AIProviderRegistry
from app.schemas.base_schema import ApiResponse


class AiProviderApi:
    """AI 提供商 API"""

    def __init__(self):
        self.router = APIRouter(prefix="/api/ai-provider", tags=["AI供应商"])
        self._register_routes()

    def _register_routes(self):
        """注册所有路由"""
        self.router.add_api_route("/list", self.list_providers, methods=["GET"])

    async def list_providers(self):
        """获取所有已注册的 AI 提供商列表"""
        providers = AIProviderRegistry.list_provider_info()
        return ApiResponse.success(data=providers)


ai_provider_api = AiProviderApi()
router = ai_provider_api.router

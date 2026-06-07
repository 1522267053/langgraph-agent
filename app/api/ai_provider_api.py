"""
AI 提供商 API 路由
"""

from fastapi import APIRouter, Query

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
        self.router.add_api_route(
            "/media-fields", self.get_media_fields, methods=["GET"]
        )

    async def list_providers(self):
        """获取所有已注册的 AI 提供商列表"""
        providers = AIProviderRegistry.list_provider_info()
        return ApiResponse.success(data=providers)

    async def get_media_fields(
        self,
        provider: str = Query(..., description="提供商名称"),
        media_type: str = Query(..., description="媒体类型: image/audio/video"),
    ):
        """获取指定 Provider 对指定媒体类型的参数字段定义"""
        cls = AIProviderRegistry.get(provider)
        if not cls:
            return ApiResponse.error(msg=f"不支持的 AI 提供商: {provider}")

        supports_map = {
            "image": getattr(cls, "supports_image", False),
            "audio": getattr(cls, "supports_audio", False),
            "video": getattr(cls, "supports_video", False),
        }
        if not supports_map.get(media_type, False):
            return ApiResponse.error(msg=f"[{provider}] 不支持 {media_type} 生成")

        try:
            fields = cls.get_media_gen_fields(media_type)
            return ApiResponse.success(
                data=[f.to_dict() if hasattr(f, "to_dict") else f for f in fields]
            )
        except Exception as e:
            return ApiResponse.error(msg=f"获取参数定义失败: {e}")


ai_provider_api = AiProviderApi()
router = ai_provider_api.router

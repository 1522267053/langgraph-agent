"""
AI 模型 API 路由

提供模型分页查询、详情和删除接口
"""

from app.api.base_api import BaseApi, RouteConfig
from app.models.ai_model import AIModel
from app.services.ai_model_service import ai_model_service
from app.schemas.ai_model_provider_schema import (
    AIModelBase,
    AIModelCreate,
    AIModelUpdate,
)


class AIModelApi(
    BaseApi[AIModel, AIModelBase, AIModelBase, AIModelCreate, AIModelUpdate]
):
    def __init__(self):
        super().__init__(
            service=ai_model_service,
            router_prefix="/api/ai-model",
            router_tags=["AI模型"],
            route_config=RouteConfig(
                enable_create=False,
                enable_update=False,
                enable_batch_create=False,
                enable_batch_update=False,
            ),
        )


ai_model_api = AIModelApi()
router = ai_model_api.router

"""
AI 供应商连接 API 路由

提供供应商连接 CRUD、默认连接切换和跨供应商模型分组查询。
ValueError 由全局异常中间件统一转换为错误响应。
"""

import logging

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.api.base_api import BaseApi, RouteConfig
from app.models.ai_provider_connection import AIProviderConnection
from app.services.ai_provider_connection_service import ai_provider_connection_service
from app.schemas.ai_provider_connection_schema import (
    AIProviderConnectionBase,
    AIProviderConnectionCreate,
    AIProviderConnectionQuery,
    AIProviderConnectionUpdate,
)
from app.schemas.base_schema import ApiResponse

logger = logging.getLogger(__name__)


class AIProviderConnectionApi(
    BaseApi[
        AIProviderConnection,
        AIProviderConnectionBase,
        AIProviderConnectionQuery,
        AIProviderConnectionCreate,
        AIProviderConnectionUpdate,
    ]
):
    """AI 供应商连接 API"""

    def __init__(self):
        super().__init__(
            service=ai_provider_connection_service,
            router_prefix="/api/ai-provider-connection",
            router_tags=["AI供应商连接"],
            route_config=RouteConfig(enable_get=False, enable_batch_delete=False),
        )
        self._register_custom_routes()

    async def create(
        self, db: AsyncSession, data: AIProviderConnectionCreate
    ) -> AIProviderConnection:
        """创建连接，默认连接变更后同步内置 Agent"""
        conn = await ai_provider_connection_service.create_connection(db, data)
        await self._sync_builtin_agent(db)
        return conn

    async def update(
        self, db: AsyncSession, data: AIProviderConnectionUpdate
    ) -> AIProviderConnection:
        """更新连接，默认连接变更后同步内置 Agent"""
        conn = await ai_provider_connection_service.update_connection(db, data)
        await self._sync_builtin_agent(db)
        return conn

    async def delete(self, db: AsyncSession, id: int) -> None:
        """删除连接（默认连接自动转移），同步内置 Agent"""
        await ai_provider_connection_service.delete_connection(db, id)
        await self._sync_builtin_agent(db)

    async def _sync_builtin_agent(self, db: AsyncSession) -> None:
        """默认连接变化会影响全局默认 LLM 配置，同步内置 Agent（失败不阻断）"""
        try:
            from app.services.builtin_agent_service import builtin_agent_service

            await builtin_agent_service.sync_llm_config(db)
        except Exception as e:
            logger.warning("同步内置 Agent LLM 配置失败: %s", e)

    def _register_custom_routes(self):
        """注册自定义路由"""

        @self.router.put(
            "/set-default/{id}",
            response_model=ApiResponse,
            summary="设为全局默认连接",
        )
        async def set_default(id: int, db: AsyncSession = Depends(get_db)):
            await ai_provider_connection_service.set_default(db, id)
            await self._sync_builtin_agent(db)
            return ApiResponse.success(msg="已设为默认连接")

        @self.router.get(
            "/model-groups",
            response_model=ApiResponse[list[dict]],
            summary="聚合启用连接的供应商模型分组",
        )
        async def model_groups(db: AsyncSession = Depends(get_db)):
            groups = await ai_provider_connection_service.list_model_groups(db)
            return ApiResponse.success(data=groups, msg="查询成功")


ai_provider_connection_api = AIProviderConnectionApi()
router = ai_provider_connection_api.router

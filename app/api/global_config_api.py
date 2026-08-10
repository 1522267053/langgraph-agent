"""
全局配置 API 路由
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.schemas.base_schema import ApiResponse
from app.schemas.global_config_schema import (
    InitConfigRequest,
    UpdateConfigRequest,
    CheckInitResponse,
)
from app.services.global_config_service import global_config_service
from app.services.builtin_agent_service import builtin_agent_service
from app.middleware.auth_middleware import (
    invalidate_password_cache,
    invalidate_init_cache,
)

logger = logging.getLogger(__name__)


class GlobalConfigApi:
    """全局配置 API"""

    def __init__(self):
        self.router = APIRouter(prefix="/api/config", tags=["全局配置"])
        self._register_routes()

    def _register_routes(self):
        """注册所有路由"""

        @self.router.get(
            "/check",
            response_model=ApiResponse[CheckInitResponse],
            summary="检查初始化状态",
        )
        async def check_init(db: AsyncSession = Depends(get_db)):
            """检查是否已完成初始化配置"""
            initialized = await global_config_service.is_initialized(db)
            return ApiResponse.success(
                data=CheckInitResponse(initialized=initialized), msg="查询成功"
            )

        @self.router.post("/init", response_model=ApiResponse, summary="首次初始化配置")
        async def init_config(
            request: InitConfigRequest, db: AsyncSession = Depends(get_db)
        ):
            """首次初始化全局配置，并创建内置 Agent"""
            is_init = await global_config_service.is_initialized(db)
            if is_init:
                return ApiResponse.error(msg="已初始化，请使用更新接口")

            await global_config_service.init_config(db, request)

            if (
                request.embedding_api_key
                or request.embedding_base_url
                or request.embedding_model
            ):
                from app.services.embedding_service import reset_embedding_service

                reset_embedding_service()

            await builtin_agent_service.ensure(db)

            invalidate_init_cache()

            return ApiResponse.success(msg="初始化成功")

        @self.router.get(
            "/providers",
            response_model=ApiResponse,
            summary="获取供应商列表",
        )
        async def list_providers(db: AsyncSession = Depends(get_db)):
            """获取所有已注册的 AI 供应商（从数据库读取）"""
            from app.services.ai_provider_service import (
                ai_provider_service,
                _get_virtual_provider_dicts,
            )

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

        @self.router.get(
            "/",
            response_model=ApiResponse,
            summary="获取当前配置",
        )
        async def get_config(db: AsyncSession = Depends(get_db)):
            """获取当前全局配置（API Key 脱敏）"""
            config = await global_config_service.get_config(db)
            return ApiResponse.success(
                data=config.model_dump(exclude={"api_key"}), msg="查询成功"
            )

        @self.router.post("/update", response_model=ApiResponse, summary="更新配置")
        async def update_config(
            request: UpdateConfigRequest, db: AsyncSession = Depends(get_db)
        ):
            """更新全局配置，并同步内置 Agent LLM 节点"""
            await global_config_service.update_config(db, request)
            await builtin_agent_service.sync_llm_config(db)
            if request.login_password is not None:
                invalidate_password_cache()
            if any(
                v is not None
                for v in [
                    request.embedding_api_key,
                    request.embedding_base_url,
                    request.embedding_model,
                ]
            ):
                from app.services.embedding_service import reset_embedding_service

                reset_embedding_service()
            if request.execution_notification_enabled is not None:
                from app.services.ws_manager import ws_manager

                ws_manager.set_notification_enabled(
                    request.execution_notification_enabled
                )
            return ApiResponse.success(msg="更新成功")


global_config_api = GlobalConfigApi()
router = global_config_api.router

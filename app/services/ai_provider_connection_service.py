"""
AI 供应商连接服务

管理多供应商连接（API Key、默认模型等），并作为全局默认 LLM 配置的数据源
（is_default=1 的连接替代原 global_config 的 default_* 键）。
"""

import logging
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_provider_connection import AIProviderConnection
from app.schemas.ai_provider_connection_schema import (
    AIProviderConnectionCreate,
    AIProviderConnectionUpdate,
)
from app.services.base_service import BaseService

logger = logging.getLogger(__name__)

MIGRATED_FLAG_KEY = "connection_migrated"

_EMPTY_LLM_CONFIG = {
    "provider": "",
    "model": "",
    "api_key": "",
    "base_url": "",
    "context_length": 0,
}


class AIProviderConnectionService(
    BaseService[
        AIProviderConnection, AIProviderConnectionCreate, AIProviderConnectionUpdate
    ]
):
    """AI 供应商连接服务"""

    def __init__(self):
        super().__init__(AIProviderConnection)

    # ---- 查询 ----

    async def get_default_connection(
        self, db: AsyncSession
    ) -> Optional[AIProviderConnection]:
        """全局默认连接（is_default=1）

        禁用开关仅影响对话页模型分组展示，不影响默认连接解析，
        避免误禁用导致内置 AI 助手静默失效。
        """
        query = (
            select(AIProviderConnection)
            .where(
                AIProviderConnection.is_default == 1,
                AIProviderConnection.is_delete == 0,
            )
            .order_by(AIProviderConnection.id.desc())
        )
        result = await db.execute(query)
        return result.scalars().first()

    async def get_by_provider_id(
        self, db: AsyncSession, provider_id: str
    ) -> Optional[AIProviderConnection]:
        """供应商的最新启用连接（默认连接优先），供聊天临时切换模型时解析 API Key"""
        query = (
            select(AIProviderConnection)
            .where(
                AIProviderConnection.provider_id == provider_id,
                AIProviderConnection.is_enabled == 1,
                AIProviderConnection.is_delete == 0,
            )
            .order_by(
                AIProviderConnection.is_default.desc(),
                AIProviderConnection.id.desc(),
            )
        )
        result = await db.execute(query)
        return result.scalars().first()

    async def list_enabled(self, db: AsyncSession) -> List[AIProviderConnection]:
        """所有启用的连接（默认连接在前）"""
        query = (
            select(AIProviderConnection)
            .where(
                AIProviderConnection.is_enabled == 1,
                AIProviderConnection.is_delete == 0,
            )
            .order_by(
                AIProviderConnection.is_default.desc(),
                AIProviderConnection.id.asc(),
            )
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def list_model_groups(self, db: AsyncSession) -> List[dict]:
        """聚合启用连接的供应商模型分组

        供 AgentChat 跨供应商选择模型。无模型元数据的供应商（如虚拟兼容供应商）
        跳过——其模型需手动输入，由节点自有供应商通道兜底。
        """
        from app.services.ai_model_service import ai_model_service

        groups: List[dict] = []
        for conn in await self.list_enabled(db):
            models = await ai_model_service.get_by_provider_with_name(
                db, conn.provider_id
            )
            if not models:
                continue
            groups.append(
                {
                    "provider_id": conn.provider_id,
                    "provider_label": conn.provider_name,
                    "models": models,
                }
            )
        return groups

    # ---- 全局默认 LLM 配置 ----

    async def get_default_llm_config(self, db: AsyncSession) -> dict:
        """全局默认 LLM 配置字典（与原 global_config 版本同构）

        base_url 空时回退 AIProvider.api_url。
        """
        conn = await self.get_default_connection(db)
        if not conn:
            return dict(_EMPTY_LLM_CONFIG)
        base_url = conn.base_url or ""
        if not base_url:
            from app.services.ai_provider_service import ai_provider_service

            provider = await ai_provider_service.get_by_provider_id(
                db, conn.provider_id
            )
            base_url = provider.api_url if provider and provider.api_url else ""
        return {
            "provider": conn.provider_id,
            "model": conn.default_model or "",
            "api_key": conn.api_key or "",
            "base_url": base_url,
            "context_length": conn.context_length or 0,
        }

    async def set_default(self, db: AsyncSession, id: int) -> None:
        """排他切换全局默认连接"""
        conn = await self.get_by_id(db, id, raise_not_found=True)
        await self._clear_default(db)
        conn.is_default = 1
        self._set_modifier_fields(conn)
        await db.commit()
        logger.info(
            "全局默认供应商连接已切换: id=%d provider=%s", conn.id, conn.provider_id
        )

    async def _clear_default(self, db: AsyncSession) -> None:
        """清空全部默认标记"""
        query = select(AIProviderConnection).where(
            AIProviderConnection.is_default == 1,
            AIProviderConnection.is_delete == 0,
        )
        result = await db.execute(query)
        for row in result.scalars().all():
            row.is_default = 0

    async def _transfer_default(self, db: AsyncSession) -> None:
        """默认连接被删除后，自动转移默认标记到最近一条启用连接"""
        query = (
            select(AIProviderConnection)
            .where(
                AIProviderConnection.is_enabled == 1,
                AIProviderConnection.is_delete == 0,
            )
            .order_by(AIProviderConnection.id.desc())
        )
        result = await db.execute(query)
        next_conn = result.scalars().first()
        if next_conn:
            next_conn.is_default = 1
            await db.commit()
            logger.info(
                "默认供应商连接已自动转移: id=%d provider=%s",
                next_conn.id,
                next_conn.provider_id,
            )

    # ---- CRUD 扩展 ----

    async def create_connection(
        self, db: AsyncSession, data: AIProviderConnectionCreate
    ) -> AIProviderConnection:
        """创建连接：校验供应商、补全名称；无默认连接时自动设为默认"""
        provider_id, provider_name = await self.validate_provider(db, data.provider_id)
        data.provider_id = provider_id
        data.provider_name = provider_name
        data.api_key = (data.api_key or "").strip()
        if not data.is_default and (await self.get_default_connection(db)) is None:
            # 首条连接自动设为默认，保证全局默认配置开箱可用
            data.is_default = 1
        return await self.create(db, data)

    async def update_connection(
        self, db: AsyncSession, data: AIProviderConnectionUpdate
    ) -> Optional[AIProviderConnection]:
        """更新连接：api_key 空值不覆盖；provider_id 变更时联动名称"""
        if data.id is None:
            raise ValueError("更新供应商连接必须包含 id")
        conn = await self.get_by_id(db, data.id, raise_not_found=True)

        update_data = data.model_dump(exclude={"id"}, exclude_unset=True)
        api_key = update_data.pop("api_key", None)
        if api_key and api_key.strip():
            conn.api_key = api_key.strip()

        update_data.pop("provider_name", None)
        if update_data.get("provider_id"):
            provider_id, provider_name = await self.validate_provider(
                db, update_data["provider_id"]
            )
            update_data["provider_id"] = provider_id
            update_data["provider_name"] = provider_name

        become_default = update_data.get("is_default") == 1
        for field, value in update_data.items():
            setattr(conn, field, value)

        if become_default:
            await self._clear_default(db)
            conn.is_default = 1

        self._set_modifier_fields(conn)
        await db.commit()
        await db.refresh(conn)
        return conn

    async def delete_connection(self, db: AsyncSession, id: int) -> None:
        """删除连接；删除的是默认连接时自动转移默认标记"""
        conn = await self.get_by_id(db, id, raise_not_found=False)
        if not conn:
            return
        was_default = conn.is_default == 1
        await super().delete(db, id)
        if was_default:
            await self._transfer_default(db)

    async def validate_provider(
        self, db: AsyncSession, provider_id: str
    ) -> Tuple[str, str]:
        """校验供应商标识合法（含虚拟供应商），返回 (provider_id, provider_name)"""
        from app.services.ai_provider_service import (
            ai_provider_service,
            VIRTUAL_PROVIDERS,
        )

        for vp in VIRTUAL_PROVIDERS:
            if vp["name"] == provider_id:
                return provider_id, vp["label"]
        provider = await ai_provider_service.get_by_provider_id(db, provider_id)
        if not provider:
            raise ValueError(f"供应商不存在: {provider_id}")
        return provider_id, provider.name

    # ---- 启动迁移 ----

    async def seed_from_global_config(self) -> None:
        """启动时一次性迁移：global_config 默认 LLM 配置 → 连接表

        标志位 connection_migrated 保证只执行一次；无默认 API Key 时同样置位，
        避免每次启动重复检查。旧键保留在 global_config 表中不再读写。
        """
        from app.config.database import AsyncSessionLocal
        from app.services.global_config_service import global_config_service

        async with AsyncSessionLocal() as db:
            try:
                flag = await global_config_service.get_value(db, MIGRATED_FLAG_KEY)
                if flag == "true":
                    return

                provider = (
                    await global_config_service.get_value(db, "default_provider") or ""
                )
                api_key = (
                    await global_config_service.get_value(db, "default_api_key") or ""
                )
                if provider and api_key:
                    exists = await self.get_one(
                        db, filters=AIProviderConnection(provider_id=provider)
                    )
                    if not exists:
                        base_url = (
                            await global_config_service.get_value(
                                db, "default_base_url"
                            )
                            or ""
                        )
                        model = (
                            await global_config_service.get_value(db, "default_model")
                            or ""
                        )
                        ctx = (
                            await global_config_service.get_value(db, "context_length")
                            or ""
                        )
                        provider_name = provider
                        from app.services.ai_provider_service import ai_provider_service

                        ai_provider = await ai_provider_service.get_by_provider_id(
                            db, provider
                        )
                        if ai_provider:
                            provider_name = ai_provider.name
                            if not base_url:
                                base_url = ai_provider.api_url or ""
                        db.add(
                            AIProviderConnection(
                                provider_id=provider,
                                provider_name=provider_name,
                                api_key=api_key,
                                base_url=base_url or None,
                                default_model=model or None,
                                context_length=int(ctx) if ctx.isdigit() else None,
                                is_default=1,
                                is_enabled=1,
                                remark="迁移自全局默认配置",
                            )
                        )
                        logger.info(
                            "已从全局默认配置迁移供应商连接: provider=%s", provider
                        )

                await global_config_service.set_value(
                    db, MIGRATED_FLAG_KEY, "true", "供应商连接迁移标志"
                )
                await db.commit()
            except Exception as e:
                await db.rollback()
                logger.error("供应商连接迁移失败: %s", e, exc_info=True)


ai_provider_connection_service = AIProviderConnectionService()

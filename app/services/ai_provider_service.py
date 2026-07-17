"""
AI 供应商服务

含供应商 CRUD + 从 models.dev 同步数据
"""

import logging
from typing import List, Optional

import httpx
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_provider import AIProvider
from app.models.ai_model import AIModel
from app.services.base_service import BaseService
from app.schemas.ai_model_provider_schema import AIProviderCreate, AIProviderUpdate

logger = logging.getLogger(__name__)

SYNC_URL = "https://models.dev/api.json"
SYNC_TIMEOUT = 120

_adapter_cache: dict[str, str] = {}

VIRTUAL_PROVIDERS = [
    {
        "name": "openai-compatible",
        "label": "OpenAI 兼容",
        "adapter_type": "openai_compatible",
    },
    {
        "name": "anthropic-compatible",
        "label": "Anthropic 兼容",
        "adapter_type": "anthropic",
    },
]


def _get_virtual_provider_dicts():
    return [
        dict(p, default_base_url="", api_url="", env_vars=None) for p in VIRTUAL_PROVIDERS
    ]


def _npm_to_adapter_type(npm: str) -> str:
    if not npm:
        return "openai_compatible"
    npm_lower = npm.lower()
    if "anthropic" in npm_lower:
        return "anthropic"
    return "openai_compatible"


def get_adapter_type(provider_id: str) -> str:
    return _adapter_cache.get(provider_id, "openai_compatible")


def invalidate_adapter_cache() -> None:
    _adapter_cache.clear()


async def _load_adapter_cache(db: AsyncSession) -> None:
    result = await db.execute(select(AIProvider.provider_id, AIProvider.adapter_type))
    for row in result.all():
        _adapter_cache[row[0]] = row[1]
    for vp in VIRTUAL_PROVIDERS:
        _adapter_cache[vp["name"]] = vp["adapter_type"]


class AIProviderService(BaseService[AIProvider, AIProviderCreate, AIProviderUpdate]):
    def __init__(self):
        super().__init__(AIProvider)

    async def get_by_provider_id(
        self, db: AsyncSession, provider_id: str
    ) -> Optional[AIProvider]:
        from sqlalchemy import select

        query = select(AIProvider).where(
            AIProvider.provider_id == provider_id,
            AIProvider.is_delete == 0,
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def list_providers(self, db: AsyncSession) -> List[AIProvider]:
        from sqlalchemy import select, and_

        query = (
            select(AIProvider)
            .where(
                and_(
                    AIProvider.is_delete == 0,
                    AIProvider.api_url.isnot(None),
                    AIProvider.api_url != "",
                )
            )
            .order_by(AIProvider.name)
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def sync_from_url(self) -> None:
        from app.config.database import AsyncSessionLocal

        def _clean_null(v):
            return v if v is not None and v != "null" else None

        logger.info("开始从 models.dev 同步 AI 供应商和模型数据...")
        try:
            async with httpx.AsyncClient(timeout=SYNC_TIMEOUT) as client:
                response = await client.get(SYNC_URL)
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            logger.error(f"拉取 models.dev 数据失败: {e}", exc_info=True)
            raise

        providers: List[dict] = []
        models: List[dict] = []

        for provider_id, provider_data in data.items():
            if not isinstance(provider_data, dict):
                continue
            npm = provider_data.get("npm", "")
            api_url = provider_data.get("api")
            if not api_url:
                continue
            env_vars = provider_data.get("env")
            adapter_type = _npm_to_adapter_type(npm)
            providers.append(
                {
                    "provider_id": provider_id,
                    "name": provider_data.get("name", provider_id),
                    "api_url": api_url,
                    "doc_url": provider_data.get("doc"),
                    "env_vars": env_vars
                    if isinstance(env_vars, list)
                    else [env_vars]
                    if env_vars
                    else None,
                    "npm_package": npm or None,
                    "adapter_type": adapter_type,
                }
            )
            for model_id, model_data in provider_data.get("models", {}).items():
                if not isinstance(model_data, dict):
                    continue
                modalities = model_data.get("modalities") or {}
                outputs = modalities.get("output", []) if isinstance(modalities, dict) else []
                if outputs and "text" not in outputs:
                    continue
                models.append(
                    {
                        "model_id": model_id,
                        "name": model_data.get("name", model_id),
                        "description": model_data.get("description"),
                        "provider_id": provider_id,
                        "modalities": model_data.get("modalities"),
                        "limits": model_data.get("limit"),
                        "cost": _clean_null(model_data.get("cost")),
                        "reasoning": 1 if model_data.get("reasoning") else 0,
                        "tool_call": 1 if model_data.get("tool_call") else 0,
                        "temperature": 1 if model_data.get("temperature") else 0,
                        "attachment": 1 if model_data.get("attachment") else 0,
                        "open_weights": 1 if model_data.get("open_weights") else 0,
                        "is_experimental": 1 if model_data.get("experimental") else 0,
                        "structured_output": 1
                        if model_data.get("structured_output")
                        else 0,
                        "reasoning_options": _clean_null(
                            model_data.get("reasoning_options")
                        ),
                        "knowledge": model_data.get("knowledge"),
                        "release_date": model_data.get("release_date"),
                        "last_updated": model_data.get("last_updated"),
                        "family": model_data.get("family"),
                        "status": model_data.get("status"),
                    }
                )

        logger.info(
            "拉取完成: %d 个供应商, %d 个模型",
            len(providers),
            len(models),
        )

        async with AsyncSessionLocal() as db:
            try:
                await db.execute(delete(AIModel))
                await db.execute(delete(AIProvider))
                if providers:
                    for p in providers:
                        db.add(AIProvider(**p))
                await db.flush()
                if models:
                    batch_size = 500
                    for i in range(0, len(models), batch_size):
                        for m in models[i : i + batch_size]:
                            db.add(AIModel(**m))
                        await db.flush()
                await db.commit()
                logger.info(
                    "同步完成: 写入 %d 个供应商, %d 个模型",
                    len(providers),
                    len(models),
                )
            except Exception:
                await db.rollback()
                raise

        invalidate_adapter_cache()
        async with AsyncSessionLocal() as db:
            await _load_adapter_cache(db)

        logger.info("适配器缓存已刷新")


ai_provider_service = AIProviderService()

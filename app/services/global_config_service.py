"""
全局配置服务

管理用户全局配置（API Key、模型、供应商等）
"""

import hashlib
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.global_config import GlobalConfig
from app.schemas.global_config_schema import (
    InitConfigRequest,
    UpdateConfigRequest,
    GlobalConfigResponse,
)
from app.config.settings import settings

logger = logging.getLogger(__name__)

MARKETPLACE_SERVER_URL_KEY = "marketplace_server_url"
MARKETPLACE_TOKEN_KEY = "marketplace_token"
MARKETPLACE_TOKEN_EXPIRES_KEY = "marketplace_token_expires_at"
MARKETPLACE_PASSWORD_HASH_KEY = "marketplace_password_hash"

_CACHE_TTL = 300

_AI_CONFIG_KEYS = {
    "default_provider",
    "default_api_key",
    "default_model",
    "default_base_url",
    "context_length",
    "embedding_api_key",
    "embedding_base_url",
    "embedding_model",
}

CONFIG_KEYS = {
    "default_provider": "默认 AI 供应商",
    "default_api_key": "默认 API Key",
    "default_model": "默认模型",
    "default_base_url": "默认 Base URL",
    "context_length": "模型上下文窗口大小",
    "initialized": "是否完成初始化",
    "embedding_api_key": "向量模型 API Key",
    "embedding_base_url": "向量模型 Base URL",
    "embedding_model": "向量模型名称",
    "login_password_hash": "登录密码哈希",
    "login_username": "登录用户名",
    "execution_notification_enabled": "执行完成通知开关",
    MARKETPLACE_SERVER_URL_KEY: "资源市场服务器地址",
    MARKETPLACE_TOKEN_KEY: "市场 JWT Token",
    MARKETPLACE_TOKEN_EXPIRES_KEY: "市场 Token 过期时间",
    MARKETPLACE_PASSWORD_HASH_KEY: "市场端已同步的密码哈希",
}


class GlobalConfigService:
    """全局配置服务，以 key-value 形式管理配置"""

    def __init__(self):
        self._ai_config: dict[str, Optional[str]] = {}
        self._marketplace_server_url: Optional[str] = None
        self._marketplace_token: Optional[str] = None
        self._marketplace_token_expires: Optional[datetime] = None
        self._ai_last_refresh: Optional[datetime] = None
        self._marketplace_last_refresh: Optional[datetime] = None

    async def ensure_ai_cache(self, db: AsyncSession) -> None:
        """刷新 AI 配置内存缓存（首次或超过 5 分钟）"""
        now = datetime.now()
        if (
            self._ai_last_refresh
            and (now - self._ai_last_refresh).total_seconds() < _CACHE_TTL
        ):
            return
        self._ai_last_refresh = now
        for key in _AI_CONFIG_KEYS:
            self._ai_config[key] = await self.get_value(db, key)

    def invalidate_ai_cache(self) -> None:
        """清空 AI 配置内存缓存"""
        self._ai_last_refresh = None
        self._ai_config.clear()

    def _update_ai_cache_fields(self, **kwargs: str) -> None:
        """更新 AI 缓存中的指定字段"""
        for k, v in kwargs.items():
            self._ai_config[k] = v

    async def ensure_marketplace_cache(self, db: AsyncSession) -> None:
        """刷新市场配置内存缓存（首次或超过 5 分钟）"""
        now = datetime.now()
        if (
            self._marketplace_last_refresh
            and (now - self._marketplace_last_refresh).total_seconds() < _CACHE_TTL
        ):
            return
        self._marketplace_last_refresh = now
        self._marketplace_server_url = await self.get_value(
            db, MARKETPLACE_SERVER_URL_KEY
        )
        self._marketplace_token = await self.get_value(db, MARKETPLACE_TOKEN_KEY)
        expires_str = await self.get_value(db, MARKETPLACE_TOKEN_EXPIRES_KEY)
        if expires_str:
            try:
                self._marketplace_token_expires = datetime.fromisoformat(expires_str)
            except (ValueError, TypeError):
                self._marketplace_token_expires = None
        else:
            self._marketplace_token_expires = None

    def invalidate_marketplace_cache(self) -> None:
        """清空市场配置内存缓存"""
        self._marketplace_last_refresh = None
        self._marketplace_server_url = None
        self._marketplace_token = None
        self._marketplace_token_expires = None

    @property
    def marketplace_token(self) -> Optional[str]:
        """获取当前缓存的 marketplace JWT token"""
        return self._marketplace_token

    @staticmethod
    def _hash_password(password: str) -> str:
        """对密码做 sha256 哈希"""
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    async def get_value(self, db: AsyncSession, key: str) -> Optional[str]:
        """获取单个配置值"""
        query = select(GlobalConfig.value).where(
            GlobalConfig.key == key, GlobalConfig.is_delete == 0
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def set_value(
        self, db: AsyncSession, key: str, value: str, description: str = ""
    ) -> None:
        """设置单个配置值（upsert）"""
        query = select(GlobalConfig).where(
            GlobalConfig.key == key, GlobalConfig.is_delete == 0
        )
        result = await db.execute(query)
        config = result.scalar_one_or_none()

        if config:
            config.value = value
        else:
            config = GlobalConfig(
                key=key,
                value=value,
                description=description or CONFIG_KEYS.get(key, ""),
            )
            db.add(config)
        await db.commit()

    async def is_initialized(self, db: AsyncSession) -> bool:
        """检查是否已完成初始化"""
        val = await self.get_value(db, "initialized")
        return val == "true"

    async def init_config(self, db: AsyncSession, request: InitConfigRequest) -> None:
        """首次初始化配置"""
        base_url = request.base_url
        if not base_url:
            from app.services.ai_provider_service import ai_provider_service

            provider = await ai_provider_service.get_by_provider_id(
                db, request.provider
            )
            base_url = provider.api_url if provider and provider.api_url else ""

        configs = {
            "default_provider": request.provider,
            "default_api_key": request.api_key,
            "default_model": request.model,
            "default_base_url": base_url or "",
            "initialized": "true",
        }
        if request.context_length is not None:
            configs["context_length"] = str(request.context_length)
        if request.embedding_api_key:
            configs["embedding_api_key"] = request.embedding_api_key
        if request.embedding_base_url:
            configs["embedding_base_url"] = request.embedding_base_url
        if request.embedding_model:
            configs["embedding_model"] = request.embedding_model

        has_pwd = bool(request.login_password)
        has_user = bool(request.login_username)
        if has_pwd != has_user:
            raise ValueError("用户名和密码必须同时设置")
        configs["login_password_hash"] = request.login_password
        configs["login_username"] = request.login_username

        if settings.marketplace_server_url:
            configs["marketplace_server_url"] = settings.marketplace_server_url

        for key, value in configs.items():
            await self.set_value(db, key, value, CONFIG_KEYS.get(key, ""))

        ai_updates = {k: v for k, v in configs.items() if k in _AI_CONFIG_KEYS}
        self._update_ai_cache_fields(**ai_updates)
        if MARKETPLACE_SERVER_URL_KEY in configs:
            self._marketplace_server_url = configs[MARKETPLACE_SERVER_URL_KEY]

        logger.info("全局配置初始化完成")

    async def update_config(
        self, db: AsyncSession, request: UpdateConfigRequest
    ) -> None:
        """更新配置（仅更新非 None 字段）"""
        updates = {}
        if request.provider is not None:
            updates["default_provider"] = request.provider
            from app.services.ai_provider_service import ai_provider_service

            provider = await ai_provider_service.get_by_provider_id(
                db, request.provider
            )
            if provider and provider.api_url:
                updates["default_base_url"] = request.base_url or provider.api_url
        if request.api_key is not None:
            updates["default_api_key"] = request.api_key
        if request.model is not None:
            updates["default_model"] = request.model
        if request.context_length is not None:
            updates["context_length"] = str(request.context_length)
        if request.base_url is not None and "default_base_url" not in updates:
            updates["default_base_url"] = request.base_url

        if request.embedding_api_key is not None:
            updates["embedding_api_key"] = request.embedding_api_key
        if request.embedding_base_url is not None:
            updates["embedding_base_url"] = request.embedding_base_url
        if request.embedding_model is not None:
            updates["embedding_model"] = request.embedding_model

        if request.execution_notification_enabled is not None:
            updates["execution_notification_enabled"] = str(
                request.execution_notification_enabled
            )

        if request.login_password is not None or request.login_username is not None:
            # 修改密码时必须验证当前密码
            if request.login_password:
                current_hash = await self.get_password_hash(db)
                if current_hash:
                    if not request.current_password:
                        raise ValueError("修改密码需要输入当前密码")
                    if request.current_password != current_hash:
                        raise ValueError("当前密码不正确")

            pwd = request.login_password
            user = request.login_username
            existing_pwd = await self.get_value(db, "login_password_hash")
            existing_user = await self.get_value(db, "login_username")

            if pwd == "" or user == "":
                raise ValueError("登录保护不可关闭")
            elif pwd and user:
                if existing_pwd and pwd == existing_pwd:
                    raise ValueError("新密码不能与原密码相同")
                updates["login_password_hash"] = pwd
                updates["login_username"] = user
            elif pwd and not user:
                if not existing_user:
                    raise ValueError("用户名和密码必须同时设置")
                if pwd == existing_pwd:
                    raise ValueError("新密码不能与原密码相同")
                updates["login_password_hash"] = pwd
            elif user and not pwd:
                if not existing_pwd:
                    raise ValueError("用户名和密码必须同时设置")
                updates["login_username"] = user

        old_password_hash = await self.get_value(db, "login_password_hash")

        for key, value in updates.items():
            await self.set_value(db, key, value, CONFIG_KEYS.get(key, ""))

        if (
            "login_password_hash" in updates
            and old_password_hash
            and updates["login_password_hash"]
        ):
            from app.services.marketplace_service import marketplace_service

            await marketplace_service.sync_password(
                db, old_password_hash, updates["login_password_hash"]
            )

        ai_updates = {k: v for k, v in updates.items() if k in _AI_CONFIG_KEYS}
        self._update_ai_cache_fields(**ai_updates)

        logger.info("全局配置已更新: %s", list(updates.keys()))

    async def get_config(self, db: AsyncSession) -> GlobalConfigResponse:
        """获取当前配置（API Key 脱敏）"""
        await self.ensure_ai_cache(db)
        provider = self._ai_config.get("default_provider")
        model = self._ai_config.get("default_model")
        api_key = self._ai_config.get("default_api_key")
        base_url = self._ai_config.get("default_base_url")
        embedding_model = self._ai_config.get("embedding_model")
        embedding_api_key = self._ai_config.get("embedding_api_key")
        embedding_base_url = self._ai_config.get("embedding_base_url")
        has_password = await self.has_password(db)
        has_username = await self.has_username(db)

        def _mask(val: Optional[str]) -> Optional[str]:
            if not val:
                return None
            if len(val) > 8:
                return val[:4] + "****" + val[-4:]
            return "****"

        eb_api_key = embedding_api_key or settings.embedding_api_key
        eb_base_url = embedding_base_url or settings.embedding_base_url
        eb_model = embedding_model or settings.embedding_model

        username = await self.get_username(db)

        ctx_str = self._ai_config.get("context_length") or ""
        ctx_length = int(ctx_str) if ctx_str.isdigit() else None

        notif_str = await self.get_value(db, "execution_notification_enabled")
        notif_enabled = notif_str.lower() != "false" if notif_str else True

        return GlobalConfigResponse(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            api_key_masked=_mask(api_key),
            context_length=ctx_length,
            embedding_model=eb_model,
            embedding_api_key_masked=_mask(eb_api_key),
            embedding_base_url=eb_base_url or "",
            has_password=has_password,
            has_username=has_username,
            username=username,
            execution_notification_enabled=notif_enabled,
        )

    async def get_password_hash(self, db: AsyncSession) -> Optional[str]:
        """获取密码哈希（DB 优先，回退 .env）"""
        val = await self.get_value(db, "login_password_hash")
        if val:
            return val
        if settings.login_password:
            return self._hash_password(settings.login_password)
        return None

    async def has_password(self, db: AsyncSession) -> bool:
        """是否已配置登录密码"""
        return await self.get_password_hash(db) is not None

    async def verify_password(self, db: AsyncSession, password: str) -> bool:
        """验证密码"""
        expected = await self.get_password_hash(db)
        if not expected:
            return False
        return self._hash_password(password) == expected

    async def verify_password_hash(self, db: AsyncSession, password_hash: str) -> bool:
        """直接比对密码哈希（前端已 SHA-256 哈希）"""
        expected = await self.get_password_hash(db)
        if not expected:
            return False
        return password_hash == expected

    async def get_username(self, db: AsyncSession) -> Optional[str]:
        """获取登录用户名（明文）"""
        return await self.get_value(db, "login_username")

    async def has_username(self, db: AsyncSession) -> bool:
        """是否已配置登录用户名"""
        return (await self.get_username(db)) is not None

    async def verify_username(self, db: AsyncSession, username: str) -> bool:
        """验证用户名（明文比对）"""
        expected = await self.get_username(db)
        if not expected:
            return True
        return username == expected

    async def get_marketplace_password_hash(self, db: AsyncSession) -> Optional[str]:
        """获取市场端已同步的密码哈希"""
        return await self.get_value(db, MARKETPLACE_PASSWORD_HASH_KEY)

    async def set_marketplace_password_hash(
        self, db: AsyncSession, pwd_hash: str
    ) -> None:
        """保存市场端已同步的密码哈希"""
        await self.set_value(
            db, MARKETPLACE_PASSWORD_HASH_KEY, pwd_hash, "市场端已同步的密码哈希"
        )

    async def get_default_llm_config(self, db: AsyncSession) -> dict:
        """获取默认 LLM 配置字典"""
        await self.ensure_ai_cache(db)
        ctx_str = self._ai_config.get("context_length") or ""
        ctx_length = int(ctx_str) if ctx_str.isdigit() else 0
        return {
            "provider": self._ai_config.get("default_provider") or "",
            "model": self._ai_config.get("default_model") or "",
            "api_key": self._ai_config.get("default_api_key") or "",
            "base_url": self._ai_config.get("default_base_url") or "",
            "context_length": ctx_length,
        }

    async def get_embedding_config(self, db: AsyncSession) -> dict:
        """获取 Embedding 配置字典（DB 优先，回退 .env）"""
        await self.ensure_ai_cache(db)
        return {
            "api_key": self._ai_config.get("embedding_api_key")
            or settings.embedding_api_key
            or "",
            "base_url": self._ai_config.get("embedding_base_url")
            or settings.embedding_base_url
            or "",
            "model": self._ai_config.get("embedding_model")
            or settings.embedding_model
            or "",
        }


global_config_service = GlobalConfigService()

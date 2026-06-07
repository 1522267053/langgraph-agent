"""
市场服务

管理市场服务器连接配置，代理资源请求
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.services.global_config_service import (
    global_config_service,
    MARKETPLACE_SERVER_URL_KEY,
    MARKETPLACE_TOKEN_KEY,
    MARKETPLACE_TOKEN_EXPIRES_KEY,
    MARKETPLACE_PASSWORD_HASH_KEY,
)
from app.config.settings import settings

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 5
_DOWNLOAD_TIMEOUT = 30


class MarketplaceService:
    """市场服务"""

    def __init__(self):
        self._connect_error_msg: str = ""

    async def get_server_url(self, db) -> Optional[str]:
        """获取市场服务器地址（内存优先，5分钟刷新一次，回退 .env）"""
        await global_config_service.ensure_marketplace_cache(db)
        url = global_config_service._marketplace_server_url
        if url:
            return url
        if settings.marketplace_server_url:
            return settings.marketplace_server_url.rstrip("/")
        return None

    async def get_token(self, db) -> Optional[str]:
        """获取当前 token（内存缓存）"""
        await global_config_service.ensure_marketplace_cache(db)
        return global_config_service._marketplace_token

    async def get_token_expires(self, db) -> Optional[datetime]:
        """获取 token 过期时间（内存缓存）"""
        await global_config_service.ensure_marketplace_cache(db)
        return global_config_service._marketplace_token_expires

    async def save_server_url(self, db, server_url: str) -> None:
        """保存市场服务器地址（写 DB + 更新内存）"""
        await global_config_service.set_value(
            db, MARKETPLACE_SERVER_URL_KEY, server_url.rstrip("/"), "资源市场服务器地址"
        )
        global_config_service._marketplace_server_url = server_url.rstrip("/")

    async def save_token(self, db, token: str, expires_at: datetime) -> None:
        """保存 token（写 DB + 更新内存）"""
        await global_config_service.set_value(
            db, MARKETPLACE_TOKEN_KEY, token, "市场 JWT Token"
        )
        await global_config_service.set_value(
            db,
            MARKETPLACE_TOKEN_EXPIRES_KEY,
            expires_at.isoformat(),
            "市场 Token 过期时间",
        )
        global_config_service._marketplace_token = token
        global_config_service._marketplace_token_expires = expires_at

    async def clear_token(self, db) -> None:
        """清除 token（写 DB + 清空内存）"""
        await global_config_service.set_value(db, MARKETPLACE_TOKEN_KEY, "", "")
        await global_config_service.set_value(db, MARKETPLACE_TOKEN_EXPIRES_KEY, "", "")
        global_config_service._marketplace_token = None
        global_config_service._marketplace_token_expires = None

    async def is_connected(self, db) -> bool:
        """检查是否已连接市场（有服务器地址 + 有效 token + 服务器可达）"""
        await global_config_service.ensure_marketplace_cache(db)
        server_url = global_config_service._marketplace_server_url
        token = global_config_service._marketplace_token
        if not server_url or not token:
            return False
        expires = global_config_service._marketplace_token_expires
        if not expires or datetime.now() >= expires:
            return False
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                resp = await client.get(f"{server_url}/api/health")
                return resp.status_code == 200
        except Exception:
            return False

    async def ensure_token(self, db) -> Optional[str]:
        """获取有效 token，过期自动尝试刷新"""
        await global_config_service.ensure_marketplace_cache(db)
        token = global_config_service._marketplace_token
        server_url = global_config_service._marketplace_server_url
        if not token or not server_url:
            return None
        expires = global_config_service._marketplace_token_expires
        if expires and datetime.now() < expires:
            return token
        await self.clear_token(db)
        return await self.connect(db)

    async def _do_login(
        self, db, server_url: str, username: str, password_hash: str
    ) -> Optional[str]:
        """执行一次登录请求，成功返回 token，否则返回 None"""
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            try:
                resp = await client.post(
                    f"{server_url}/api/auth/login",
                    json={"username": username, "password_hash": password_hash},
                )
                if resp.status_code != 200:
                    self._connect_error_msg = f"登录请求失败 (HTTP {resp.status_code})"
                    return None
                data = resp.json()
                if data.get("code") != 1:
                    msg = data.get("msg", "未知错误")
                    if not self._connect_error_msg:
                        self._connect_error_msg = f"登录失败: {msg}"
                    return None
                result = data.get("data", {})
                token = result.get("access_token")
                expires_in = result.get("expires_in", 86400)
                if token:
                    expires_at = datetime.now() + timedelta(seconds=expires_in)
                    await self.save_token(db, token, expires_at)
                    return token
                return None
            except Exception:
                logger.exception("市场登录请求异常")
                self._connect_error_msg = "登录请求异常"
                return None

    async def _do_register(
        self, db, server_url: str, username: str, password_hash: str
    ) -> bool:
        """执行一次注册请求，返回是否成功，失败时记录错误信息"""
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            try:
                resp = await client.post(
                    f"{server_url}/api/auth/register",
                    json={"username": username, "password_hash": password_hash},
                )
                if resp.status_code != 200:
                    self._connect_error_msg = f"注册请求失败 (HTTP {resp.status_code})"
                    return False
                data = resp.json()
                if data.get("code") != 1:
                    msg = data.get("msg", "未知错误")
                    self._connect_error_msg = f"注册失败: {msg}"
                    logger.warning("市场注册返回: %s", msg)
                    return False
                return True
            except Exception:
                logger.exception("市场注册请求异常")
                self._connect_error_msg = "注册请求异常"
                return False

    async def connect(self, db) -> Optional[str]:
        """登录市场服务器，失败时自动注册再登录；重连时自动补同步本地密码变更"""
        self._connect_error_msg = ""
        server_url = await self.get_server_url(db)
        if not server_url:
            raise ValueError("未配置市场服务器地址")

        current_password_hash = await global_config_service.get_password_hash(db)
        username = await global_config_service.get_username(db)
        if not current_password_hash or not username:
            self._connect_error_msg = "未配置本地用户名或密码"
            return None

        marketplace_pwd = await global_config_service.get_marketplace_password_hash(db)
        login_pwd = marketplace_pwd or current_password_hash

        token = await self._do_login(db, server_url, username, login_pwd)
        if token:
            if not marketplace_pwd:
                await global_config_service.set_marketplace_password_hash(
                    db, current_password_hash
                )
            elif marketplace_pwd != current_password_hash:
                synced = await self.sync_password(
                    db, marketplace_pwd, current_password_hash
                )
                if not synced:
                    logger.warning("市场重连后补同步密码失败，不影响当前会话")
            return token

        registered = await self._do_register(
            db, server_url, username, current_password_hash
        )
        if registered:
            token = await self._do_login(
                db, server_url, username, current_password_hash
            )
            if token:
                await global_config_service.set_marketplace_password_hash(
                    db, current_password_hash
                )
                return token
            self._connect_error_msg = "注册成功但登录失败"
            return None
        # 注册失败（如用户名已存在），再尝试用当前本地密码登录
        token = await self._do_login(db, server_url, username, current_password_hash)
        if token:
            await global_config_service.set_marketplace_password_hash(
                db, current_password_hash
            )
            return token
        self._connect_error_msg = self._connect_error_msg or "登录失败"
        return None

    def get_connect_error_msg(self) -> str:
        """获取最近一次连接失败的错误信息"""
        return self._connect_error_msg

    async def disconnect(self, db) -> None:
        """断开市场连接，清除 token 和市场端密码记录"""
        await self.clear_token(db)
        await global_config_service.set_value(db, MARKETPLACE_PASSWORD_HASH_KEY, "", "")

    async def sync_password(
        self, db, old_password_hash: str, new_password_hash: str
    ) -> bool:
        """同步密码到资源市场（修改本地密码后调用，保持市场端密码一致）"""
        server_url = await self.get_server_url(db)
        username = await global_config_service.get_username(db)
        if (
            not server_url
            or not username
            or not old_password_hash
            or not new_password_hash
        ):
            return False
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            try:
                resp = await client.put(
                    f"{server_url}/api/auth/password",
                    json={
                        "username": username,
                        "old_password_hash": old_password_hash,
                        "new_password_hash": new_password_hash,
                    },
                )
                if resp.status_code != 200:
                    logger.warning("同步市场密码失败 (HTTP %s)", resp.status_code)
                    return False
                data = resp.json()
                if data.get("code") != 1:
                    logger.warning("同步市场密码失败: %s", data.get("msg"))
                    return False
                await global_config_service.set_marketplace_password_hash(
                    db, new_password_hash
                )
                logger.info("市场密码同步成功")
                return True
            except Exception:
                logger.exception("同步市场密码异常")
                return False

    async def check_server_available(self, db) -> bool:
        """检测市场服务器是否可达"""
        server_url = await self.get_server_url(db)
        if not server_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                resp = await client.get(f"{server_url}/api/health")
                return resp.status_code == 200
        except Exception:
            return False

    def _build_headers(self, token: str) -> dict:
        """构建请求头"""
        return {"Authorization": f"Bearer {token}"}

    async def list_resources(
        self,
        db,
        resource_type: str = "",
        category: str = "",
        keyword: str = "",
        page: int = 1,
        page_size: int = 10,
    ) -> Optional[dict]:
        """获取市场资源列表"""
        token = await self.ensure_token(db)
        server_url = await self.get_server_url(db)
        if not token or not server_url:
            return None
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            try:
                resp = await client.post(
                    f"{server_url}/api/resources",
                    headers=self._build_headers(token),
                    json={
                        "page": page,
                        "page_size": page_size,
                        "order_by": "create_time",
                        "is_asc": False,
                        "condition": {
                            "resource_type": resource_type or None,
                            "category": category or None,
                            "name": keyword or None,
                            "is_published": 1,
                        },
                    },
                )
                if resp.status_code != 200:
                    return None
                data = resp.json()
                if data.get("code") != 1:
                    return None
                return data.get("data")
            except Exception:
                logger.exception("获取市场资源列表失败")
                return None

    async def get_resource_detail(self, db, resource_id: int) -> Optional[dict]:
        """获取市场资源详情"""
        token = await self.ensure_token(db)
        server_url = await self.get_server_url(db)
        if not token or not server_url:
            return None
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            try:
                resp = await client.get(
                    f"{server_url}/api/resources/{resource_id}",
                    headers=self._build_headers(token),
                )
                if resp.status_code != 200:
                    return None
                data = resp.json()
                if data.get("code") != 1:
                    return None
                return data.get("data")
            except Exception:
                logger.exception("获取市场资源详情失败")
                return None

    async def download_resource(self, db, resource_id: int) -> Optional[bytes]:
        """下载市场资源文件"""
        token = await self.ensure_token(db)
        server_url = await self.get_server_url(db)
        if not token or not server_url:
            return None
        async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT) as client:
            try:
                resp = await client.get(
                    f"{server_url}/api/resources/{resource_id}/download",
                    headers=self._build_headers(token),
                    follow_redirects=True,
                )
                if resp.status_code != 200:
                    return None
                return resp.content
            except Exception:
                logger.exception("下载市场资源失败")
                return None

    async def list_categories(self, db, resource_type: str = "") -> Optional[list]:
        """获取市场分类列表"""
        token = await self.ensure_token(db)
        server_url = await self.get_server_url(db)
        if not token or not server_url:
            return None
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            try:
                resp = await client.get(
                    f"{server_url}/api/categories",
                    headers=self._build_headers(token),
                    params={"resource_type": resource_type},
                )
                if resp.status_code != 200:
                    return None
                data = resp.json()
                if data.get("code") != 1:
                    return None
                return data.get("data")
            except Exception:
                logger.exception("获取市场分类列表失败")
                return None


marketplace_service = MarketplaceService()

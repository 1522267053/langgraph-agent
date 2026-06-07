"""
MCP服务器管理服务
"""

import ast
from datetime import datetime
from typing import Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp_server import McpServer, McpServerConfig, McpToolCache
from app.services.base_service import BaseService
from app.schemas.mcp_server_schema import McpServerCreate, McpServerUpdate


class McpServerService(BaseService[McpServer, McpServerCreate, McpServerUpdate]):
    """MCP服务器管理服务"""

    def __init__(self):
        super().__init__(McpServer)

    async def get_configs(self, db: AsyncSession, server_id: int) -> dict:
        """获取服务器配置"""
        query = select(McpServerConfig).where(
            McpServerConfig.server_id == server_id, McpServerConfig.is_delete == 0
        )
        result = await db.execute(query)
        configs = result.scalars().all()
        return {c.config_key: c.config_value for c in configs}

    async def get_parsed_config(self, db: AsyncSession, server_id: int) -> dict:
        """获取服务器配置并将字符串值解析为原始类型"""
        raw = await self.get_configs(db, server_id)
        if not raw:
            return {}

        parsed = {}
        for key, value in raw.items():
            if value is None:
                continue
            if key in ("args", "env", "headers", "timeout"):
                try:
                    parsed[key] = ast.literal_eval(value)
                except (ValueError, SyntaxError):
                    parsed[key] = value
            else:
                parsed[key] = value
        return parsed

    async def save_configs(
        self, db: AsyncSession, server_id: int, configs: dict
    ) -> None:
        """保存服务器配置"""
        await db.execute(
            delete(McpServerConfig).where(McpServerConfig.server_id == server_id)
        )

        for key, value in configs.items():
            if value is not None:
                config = McpServerConfig(
                    server_id=server_id,
                    config_key=key,
                    config_value=str(value) if not isinstance(value, str) else value,
                )
                db.add(config)
        await db.commit()

    async def get_tools_cache(
        self, db: AsyncSession, server_id: int
    ) -> list[McpToolCache]:
        """获取工具缓存"""
        query = (
            select(McpToolCache)
            .where(McpToolCache.server_id == server_id, McpToolCache.is_delete == 0)
            .order_by(McpToolCache.tool_name)
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def save_tools_cache(
        self, db: AsyncSession, server_id: int, tools: list[dict]
    ) -> None:
        """保存工具缓存"""
        await db.execute(
            delete(McpToolCache).where(McpToolCache.server_id == server_id)
        )

        for tool in tools:
            cache = McpToolCache(
                server_id=server_id,
                tool_name=tool.get("name", ""),
                description=tool.get("description", ""),
                tool_schema=tool.get("schema"),
                cached_at=datetime.now(),
            )
            db.add(cache)
        await db.commit()

    async def clear_tools_cache(self, db: AsyncSession, server_id: int) -> None:
        """清除工具缓存"""
        await db.execute(
            delete(McpToolCache).where(McpToolCache.server_id == server_id)
        )
        await db.commit()

    async def update_last_connected(
        self, db: AsyncSession, server_id: int, error: Optional[str] = None
    ) -> None:
        """更新最后连接时间"""
        server = await self.get_by_id(db, server_id)
        if server:
            server.last_connected_at = datetime.now()
            server.last_error = error
            await db.commit()

    async def update_tool_status(
        self, db: AsyncSession, server_id: int, tool_name: str, is_enabled: int
    ) -> bool:
        """更新工具启用状态，返回是否更新成功"""
        query = select(McpToolCache).where(
            McpToolCache.server_id == server_id,
            McpToolCache.tool_name == tool_name,
            McpToolCache.is_delete == 0,
        )
        result = await db.execute(query)
        cache = result.scalar_one_or_none()
        if not cache:
            return False
        cache.is_enabled = is_enabled
        await db.commit()
        return True

    async def get_disabled_tool_names(
        self, db: AsyncSession, server_id: int
    ) -> set[str]:
        """获取服务器下已禁用的工具名称集合"""
        query = select(McpToolCache.tool_name).where(
            McpToolCache.server_id == server_id,
            McpToolCache.is_enabled == 0,
            McpToolCache.is_delete == 0,
        )
        result = await db.execute(query)
        return {row[0] for row in result.all()}

    async def get_tool_enabled_map(
        self, db: AsyncSession, server_id: int
    ) -> dict[str, int]:
        """获取服务器下工具启用状态映射 {tool_name: is_enabled}"""
        query = select(McpToolCache.tool_name, McpToolCache.is_enabled).where(
            McpToolCache.server_id == server_id,
            McpToolCache.is_delete == 0,
        )
        result = await db.execute(query)
        return {row[0]: row[1] for row in result.all()}

    def _apply_filters(self, query, count_query, condition):
        """应用查询条件"""
        query, count_query = super()._apply_filters(query, count_query, condition)

        if condition and hasattr(condition, "name") and condition.name:
            query, count_query = self._apply_like_filter(
                query, count_query, "name", condition.name
            )

        return query, count_query


mcp_server_service = McpServerService()

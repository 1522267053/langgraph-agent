"""
MCP服务器管理 API 路由
"""

from typing import Optional
from pydantic import BaseModel, Field
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.api.base_api import BaseApi, RouteConfig
from app.models.mcp_server import McpServer
from app.services.mcp_server_service import McpServerService
from app.agent_flow.mcp_manager import mcp_tool_manager
from app.schemas.mcp_server_schema import (
    McpServerBase,
    McpServerCreate,
    McpServerUpdate,
    McpServerQuery,
    McpServerTestResult,
)
from app.schemas.base_schema import ApiResponse


class McpServerApi(
    BaseApi[McpServer, McpServerBase, McpServerQuery, McpServerCreate, McpServerUpdate]
):
    """MCP服务器 API"""

    def __init__(self):
        self.mcp_server_service = McpServerService()
        super().__init__(
            service=self.mcp_server_service,
            router_prefix="/api/mcp-server",
            router_tags=["MCP服务器管理"],
            route_config=RouteConfig(enable_get=False),
        )
        self._register_custom_routes()

    async def create(self, db: AsyncSession, data: McpServerCreate) -> McpServer:
        """创建服务器并保存配置，成功后自动刷新连接"""
        server = await self.mcp_server_service.create(db, data)
        if data.config:
            config_dict = data.config.model_dump(exclude_none=True)
            await self.mcp_server_service.save_configs(db, server.id, config_dict)
        if server.is_enabled:
            await self._refresh_after_save(db, server.id)
        return server

    async def update(
        self, db: AsyncSession, data: McpServerUpdate
    ) -> Optional[McpServer]:
        """更新服务器并保存配置，成功后自动刷新连接"""
        server = await self.mcp_server_service.update(db, data)
        if server and data.config:
            config_dict = data.config.model_dump(exclude_none=True)
            await self.mcp_server_service.save_configs(db, server.id, config_dict)
        if server and server.is_enabled:
            await self._refresh_after_save(db, server.id)
        if server and data.keep_alive is not None and data.keep_alive == 0:
            await mcp_tool_manager.clear_cache(server.id)
        return server

    async def delete(self, db: AsyncSession, id: int) -> None:
        """删除服务器并清除缓存"""
        await mcp_tool_manager.clear_cache(id)
        await self.mcp_server_service.delete(db, id)

    async def _refresh_after_save(self, db: AsyncSession, server_id: int) -> None:
        """保存后自动刷新连接，失败不阻断保存操作"""
        try:
            await mcp_tool_manager.clear_cache(server_id)
            success, _tools, error = await mcp_tool_manager.test_connection(
                db, server_id
            )
            await self.mcp_server_service.update_last_connected(
                db, server_id, error if not success else None
            )
        except Exception:
            pass

    def _register_custom_routes(self):
        """注册自定义路由"""

        @self.router.get(
            "/get/{id}",
            response_model=ApiResponse[McpServerBase],
            summary="获取MCP服务器详情",
        )
        async def get_by_id(id: int, db: AsyncSession = Depends(get_db)):
            """获取详情（含配置信息）"""
            server = await self.mcp_server_service.get_by_id(
                db, id, raise_not_found=False
            )
            if server is None:
                return ApiResponse.error(msg="未找到数据")
            view = McpServerBase.model_to_view(server)
            config_data = await self.mcp_server_service.get_parsed_config(db, id)
            if config_data:
                from app.schemas.mcp_server_schema import McpServerConfigDetail

                view.config = McpServerConfigDetail(**config_data)
            return ApiResponse.success(data=view, msg="查询成功")

        @self.router.get(
            "/list",
            response_model=ApiResponse[list[McpServerBase]],
            summary="获取所有启用的MCP服务器",
        )
        async def get_enabled_servers(db: AsyncSession = Depends(get_db)):
            """获取所有启用的MCP服务器"""
            servers = await self.mcp_server_service.get_list(
                db, filters=McpServer(is_enabled=1), order_by="-create_time"
            )
            views = McpServerBase.model_to_view_batch(servers)
            return ApiResponse.success(data=views, msg="查询成功")

        @self.router.post(
            "/test/{id}",
            response_model=ApiResponse[McpServerTestResult],
            summary="测试MCP服务器连接",
        )
        async def test_connection(id: int, db: AsyncSession = Depends(get_db)):
            """测试MCP服务器连接并获取可用工具列表"""
            success, tools, error = await mcp_tool_manager.test_connection(db, id)
            await self.mcp_server_service.update_last_connected(
                db, id, error if not success else None
            )

            result = McpServerTestResult(success=success, tools=tools, error=error)
            if success:
                return ApiResponse.success(data=result, msg="连接成功")
            else:
                return ApiResponse.error(msg=f"连接失败: {error}", code=0)

        @self.router.post(
            "/refresh/{id}",
            response_model=ApiResponse[McpServerTestResult],
            summary="刷新MCP服务器工具缓存",
        )
        async def refresh_tools(id: int, db: AsyncSession = Depends(get_db)):
            """刷新MCP服务器工具缓存"""
            await mcp_tool_manager.clear_cache(id)
            success, tools, error = await mcp_tool_manager.test_connection(db, id)
            await self.mcp_server_service.update_last_connected(
                db, id, error if not success else None
            )

            result = McpServerTestResult(success=success, tools=tools, error=error)
            return ApiResponse.success(data=result, msg="刷新完成")

        @self.router.put(
            "/tools/status",
            summary="更新MCP工具启用状态",
        )
        async def update_tool_status(
            body: "ToolStatusRequest", db: AsyncSession = Depends(get_db)
        ):
            """切换单个工具的启用/禁用状态"""
            ok = await self.mcp_server_service.update_tool_status(
                db, body.server_id, body.tool_name, body.is_enabled
            )
            if ok:
                return ApiResponse.success(msg="更新成功")
            return ApiResponse.error(msg="工具不存在")


class ToolStatusRequest(BaseModel):
    """工具状态更新请求"""

    server_id: int = Field(..., description="MCP服务器ID")
    tool_name: str = Field(..., description="工具名称")
    is_enabled: int = Field(..., ge=0, le=1, description="是否启用：0=禁用，1=启用")


mcp_server_api = McpServerApi()
router = mcp_server_api.router

"""
Agent 文件变更 API（侧栏 Diff 面板）

- POST /page：标准分页查询会话未回退的文件变更（condition.session_id 过滤，
  id/create_time 倒序，最新在前）
- GET /{change_id}/diff：单条变更的 backup/current 内容
- POST /{change_id}/revert：撤销单条变更（恢复备份）

变更记录必然归属某个 Agent 会话（非 Agent 模式不记录），会话经
AgentSession.flow_id 关联 Agent，因此路由不再携带冗余的 agent_id。
只读 + 单条撤销，无标准 CRUD 路由（变更由工具执行埋点写入）。
"""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.base_api import BaseApi, RouteConfig
from app.config.database import get_db
from app.models.agent_file_change import AgentFileChange
from app.schemas.agent_file_change_schema import (
    AgentFileChangeBase,
    AgentFileChangeDiffResponse,
    AgentFileChangeQuery,
    AgentFileChangeRevertItem,
)
from app.schemas.base_schema import ApiResponse
from app.services.agent_file_change_service import agent_file_change_service


class AgentFileChangeApi(
    BaseApi[
        AgentFileChange,
        AgentFileChangeBase,
        AgentFileChangeQuery,
        AgentFileChangeBase,
        AgentFileChangeBase,
    ]
):
    """文件变更 API（只读分页 + 单条撤销，query_class 承载 session_id 过滤）"""

    def __init__(self):
        super().__init__(
            service=agent_file_change_service,
            router_prefix="/api/agent_file_change",
            router_tags=["文件变更"],
            route_config=RouteConfig(
                enable_page=True,
                enable_get=False,
                enable_create=False,
                enable_update=False,
                enable_delete=False,
                enable_batch_delete=False,
            ),
        )
        self._register_custom_routes()

    def _register_custom_routes(self):
        """注册 diff / 撤销路由"""

        @self.router.get(
            "/{change_id}/diff",
            response_model=ApiResponse[AgentFileChangeDiffResponse],
            summary="获取单条变更的 diff 内容",
        )
        async def get_file_change_diff(
            change_id: int, db: AsyncSession = Depends(get_db)
        ):
            """返回 backup_content / current_content（前端自行渲染 diff）"""
            data = await agent_file_change_service.get_diff_content(db, change_id)
            if not data:
                return ApiResponse.error(msg="变更记录不存在")
            return ApiResponse.success(data=AgentFileChangeDiffResponse(**data))

        @self.router.post(
            "/{change_id}/revert",
            response_model=ApiResponse[AgentFileChangeRevertItem],
            summary="恢复单条文件变更",
        )
        async def revert_file_change(
            change_id: int, db: AsyncSession = Depends(get_db)
        ):
            """侧栏「撤销此变更」按钮：仅恢复该条记录，不影响其他变更"""
            result = await agent_file_change_service.revert_single_change(db, change_id)
            if not result:
                return ApiResponse.error(msg="变更记录不存在")
            return ApiResponse.success(
                data=AgentFileChangeRevertItem(**result), msg="已撤销"
            )


agent_file_change_api = AgentFileChangeApi()
router = agent_file_change_api.router

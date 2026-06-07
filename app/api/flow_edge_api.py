"""
流程边 API 路由
处理流程边相关的路由定义
"""

from typing import Union

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.base_api import BaseApi, RouteConfig
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.models.flow_edge import FlowEdge
from app.services.flow_service import flow_service
from app.schemas.flow_edge_schema import FlowEdgeBase, FlowEdgeCreate, FlowEdgeUpdate


class FlowEdgeApi(
    BaseApi[FlowEdge, FlowEdgeBase, FlowEdgeBase, FlowEdgeCreate, FlowEdgeUpdate]
):
    """流程边 API"""

    def __init__(self):
        super().__init__(
            service=flow_service,
            router_prefix="/api/flow-edge",
            router_tags=["流程边"],
            route_config=RouteConfig(
                enable_page=False,
                enable_get=False,
                enable_create=True,
                enable_update=True,
                enable_delete=True,
                enable_batch_delete=False,
                enable_batch_create=True,
                enable_batch_update=True,
            ),
        )

    @staticmethod
    def _validate_basic_edges(
        edges: list[Union[FlowEdgeCreate, FlowEdgeUpdate]],
    ) -> None:
        """校验通用边规则（自连接、工具边匹配），不通过时抛出 HTTPException。"""
        for edge in edges:
            source_key = edge.source_node_key
            target_key = edge.target_node_key
            source_handle = edge.source_handle
            target_handle = edge.target_handle

            if source_key == target_key:
                raise HTTPException(
                    status_code=400,
                    detail=f"不允许自连接：节点「{source_key}」不能连接自身",
                )

            is_source_tool = source_handle == "tools"
            is_target_tool = target_handle == "tools"
            if is_source_tool != is_target_tool:
                raise HTTPException(
                    status_code=400,
                    detail=f"工具连接只能与工具连接点相连："
                    f"「{source_key}({source_handle})」→「{target_key}({target_handle})」",
                )

    async def _validate_edges(
        self,
        db: AsyncSession,
        flow_id: int,
        edges: list[Union[FlowEdgeCreate, FlowEdgeUpdate]],
    ) -> None:
        """统一校验通用边规则、Agent 边规则、工具边目标、工具节点不可出现在普通边中、工具边唯一性和条件边完整性，不通过时抛出 HTTPException。"""
        self._validate_basic_edges(edges)
        error = await flow_service.validate_agent_edges(db, flow_id, edges)
        if error:
            raise HTTPException(status_code=400, detail=error)
        if any(getattr(e, "source_handle", None) == "tools" for e in edges):
            target_error = await flow_service.validate_tool_edge_targets(
                db, flow_id, edges
            )
            if target_error:
                raise HTTPException(status_code=400, detail=target_error)
            tool_error = await flow_service.validate_tool_edges(
                db,
                flow_id,
                edges,
                NodeHandlerRegistry.get_singleton_tool_types(),
            )
            if tool_error:
                raise HTTPException(status_code=400, detail=tool_error)
        no_tool_error = await flow_service.validate_no_tool_in_flow_edges(
            db, flow_id, edges
        )
        if no_tool_error:
            raise HTTPException(status_code=400, detail=no_tool_error)
        cond_error = await flow_service.validate_condition_edges(db, flow_id)
        if cond_error:
            raise HTTPException(status_code=400, detail=cond_error)

    async def create(self, db: AsyncSession, data: FlowEdgeCreate):
        """创建边"""
        await self._validate_edges(db, data.flow_id, [data])
        return await flow_service.create_edge(db, data)

    async def update(self, db: AsyncSession, data: FlowEdgeUpdate):
        """更新边"""
        await self._validate_edges(db, data.flow_id, [data])
        return await flow_service.update_edge(db, data)

    async def delete(self, db: AsyncSession, id: int) -> None:
        """删除边"""
        await flow_service.delete_edge(db, id)

    async def batch_create(
        self, db: AsyncSession, data_list: list[FlowEdgeCreate]
    ) -> None:
        """批量创建边"""
        if not data_list:
            return
        await self._validate_edges(db, data_list[0].flow_id, data_list)
        await flow_service.batch_create_edges(db, data_list[0].flow_id, data_list)

    async def batch_update(
        self, db: AsyncSession, data_list: list[FlowEdgeUpdate]
    ) -> None:
        """批量更新边"""
        if not data_list:
            return
        await self._validate_edges(db, data_list[0].flow_id, data_list)
        for data in data_list:
            await flow_service.update_edge(db, data)


flow_edge_api = FlowEdgeApi()
router = flow_edge_api.router

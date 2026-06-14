"""
流程 API 路由
处理流程相关的路由定义
"""

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.api.base_api import BaseApi, RouteConfig
from app.models.flow import Flow, FlowType
from app.services.flow_service import flow_service
from app.services.flow_transfer_service import flow_transfer_service
from app.schemas.flow_schema import FlowBase, FlowCreate, FlowUpdate, FlowDetail
from app.schemas.flow_node_schema import FlowNodeBase
from app.schemas.flow_edge_schema import FlowEdgeBase
from app.schemas.flow_schema import (
    VueFlowGraph,
    VueFlowNode,
    VueFlowEdge,
    VueFlowNodeData,
)
from app.schemas.base_schema import ApiResponse


class FlowExportRequest(BaseModel):
    ids: list[int] = Field(..., description="要导出的流程ID列表")


class FlowApi(BaseApi[Flow, FlowBase, FlowBase, FlowCreate, FlowUpdate]):
    """流程 API"""

    def __init__(self):
        super().__init__(
            service=flow_service,
            router_prefix="/api/flow",
            router_tags=["流程管理"],
            route_config=RouteConfig(enable_get=False),
        )
        self._register_custom_routes()

    async def create(self, db: AsyncSession, data: FlowCreate) -> Flow:
        """创建流程"""
        return await flow_service.create(db, data)

    async def delete(self, db: AsyncSession, id: int) -> None:
        """删除流程（级联删除关联数据）"""
        await flow_service.delete_with_cascade(db, id)

    def _register_custom_routes(self):
        """注册自定义路由"""

        @self.router.get(
            "/get/{id}/vue-flow",
            response_model=ApiResponse[VueFlowGraph],
            summary="获取Vue Flow格式数据",
        )
        async def get_vue_flow(id: int, db: AsyncSession = Depends(get_db)):
            """根据ID获取流程详情（Vue Flow格式）"""
            flow = await flow_service.get_with_nodes_and_edges(db, id)
            if flow is None:
                return ApiResponse.error(msg="未找到数据")

            nodes = []
            for node in flow.nodes:
                vue_node = VueFlowNode(
                    id=node.node_key,
                    type=node.node_type,
                    position={"x": node.position_x, "y": node.position_y},
                    data=VueFlowNodeData(label=node.node_name, config=node.base_config),
                )
                nodes.append(vue_node)

            edges = []
            for edge in flow.edges:
                vue_edge = VueFlowEdge(
                    id=f"edge_{edge.id}",
                    source=edge.source_node_key,
                    target=edge.target_node_key,
                    label=edge.label,
                    data=edge.condition,
                )
                edges.append(vue_edge)

            graph = VueFlowGraph(nodes=nodes, edges=edges)
            return ApiResponse.success(data=graph, msg="查询成功")

        @self.router.get(
            "/get/{id}", response_model=ApiResponse[FlowDetail], summary="获取流程详情"
        )
        async def get_flow_detail(id: int, db: AsyncSession = Depends(get_db)):
            """根据ID获取流程详情（含节点和边）"""
            flow = await flow_service.get_with_nodes_and_edges(db, id)
            if flow is None:
                return ApiResponse.error(msg="未找到数据")

            detail = FlowDetail.model_to_view(flow)
            if flow.nodes:
                detail.nodes = FlowNodeBase.model_to_view_batch(flow.nodes)
            if flow.edges:
                detail.edges = FlowEdgeBase.model_to_view_batch(flow.edges)
            return ApiResponse.success(data=detail, msg="查询成功")

        @self.router.post(
            "/save-as-card/{id}",
            response_model=ApiResponse[FlowBase],
            summary="保存为能力卡片",
        )
        async def save_as_card(id: int, db: AsyncSession = Depends(get_db)):
            """将流程保存为能力卡片"""
            try:
                flow = await flow_service.save_as_card(db, id)
                if flow is None:
                    return ApiResponse.error(msg="流程不存在")
                return ApiResponse.success(
                    data=FlowBase.model_to_view(flow), msg="保存成功"
                )
            except ValueError as e:
                return ApiResponse.error(msg=str(e))

        @self.router.get(
            "/list/cards",
            response_model=ApiResponse[list[FlowBase]],
            summary="获取已保存为卡片的流程列表",
        )
        async def get_flow_cards(db: AsyncSession = Depends(get_db)):
            """获取所有已保存为能力卡片的流程列表"""
            flows = await flow_service.get_flow_cards(db)
            views = FlowBase.model_to_view_batch(flows)
            return ApiResponse.success(data=views, msg="查询成功")

        @self.router.get(
            "/list/agents",
            response_model=ApiResponse[list[FlowBase]],
            summary="获取Agent列表",
        )
        async def get_agent_flows(db: AsyncSession = Depends(get_db)):
            """获取所有Agent（flow_type=agent的流程列表）"""
            flows, _ = await flow_service.get_by_flow_type(db, FlowType.AGENT.value)
            views = FlowBase.model_to_view_batch(flows)
            return ApiResponse.success(data=views, msg="查询成功")

        @self.router.post(
            "/create-agent", response_model=ApiResponse[FlowBase], summary="创建Agent"
        )
        async def create_agent_flow(
            flow_data: FlowCreate, db: AsyncSession = Depends(get_db)
        ):
            """创建Agent流程（flow_type=agent）"""
            from app.services.flow_service import DEFAULT_AGENT_INPUT_SCHEMA

            update_dict: dict = {"flow_type": FlowType.AGENT.value}
            # 新建 Agent 时如果没有 input_schema，补充默认 message 字段
            if not flow_data.input_schema:
                update_dict["input_schema"] = DEFAULT_AGENT_INPUT_SCHEMA
            agent_data = flow_data.model_copy(update=update_dict)
            try:
                new_flow = await flow_service.create(db, agent_data)
                return ApiResponse.success(
                    data=FlowBase.model_to_view(new_flow), msg="创建成功"
                )
            except ValueError as e:
                return ApiResponse.error(msg=str(e))

        @self.router.post(
            "/export", response_model=ApiResponse, summary="导出流程/智能体"
        )
        async def export_flows(
            body: FlowExportRequest, db: AsyncSession = Depends(get_db)
        ):
            """批量导出流程及其依赖（卡片引用、MCP、知识库、技能、记忆）"""
            try:
                data = await flow_transfer_service.export_flows(db, body.ids)
                return ApiResponse.success(
                    data=data,
                    msg=f"导出 {len(data['flows'])} 个流程",
                )
            except Exception as e:
                return ApiResponse.error(msg=f"导出失败: {e}")

        @self.router.post(
            "/import", response_model=ApiResponse, summary="导入流程/智能体"
        )
        async def import_flows(body: dict, db: AsyncSession = Depends(get_db)):
            """导入流程及其所有依赖"""
            try:
                created, warnings = await flow_transfer_service.import_flows(db, body)
                return ApiResponse.success(
                    data={"created": created, "warnings": warnings},
                    msg=f"导入 {len(created)} 个流程"
                    + ("，存在部分警告" if warnings else ""),
                )
            except ValueError as e:
                return ApiResponse.error(msg=str(e))
            except Exception as e:
                return ApiResponse.error(msg=f"导入失败: {e}")

        @self.router.post(
            "/duplicate/{id}",
            response_model=ApiResponse[FlowBase],
            summary="复制流程/智能体",
        )
        async def duplicate_flow(id: int, db: AsyncSession = Depends(get_db)):
            """复制流程或智能体（含全部节点和边）"""
            try:
                new_flow = await flow_service.duplicate_flow(db, id)
                return ApiResponse.success(
                    data=FlowBase.model_to_view(new_flow), msg="复制成功"
                )
            except ValueError as e:
                return ApiResponse.error(msg=str(e))


flow_api = FlowApi()
router = flow_api.router

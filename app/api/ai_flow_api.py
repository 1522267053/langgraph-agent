"""
AI 流程生成专用 API 路由

提供极简接口供 AI 模型创建/修改流程。
工作流：创建流程 → 批量添加节点（获取 node_key）→ 批量添加边。
"""

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.schemas.base_schema import ApiResponse
from app.schemas.flow_node_schema import FlowNodeBase
from app.schemas.flow_edge_schema import FlowEdgeBase
from app.schemas.flow_schema import FlowCreate
from app.schemas.flow_edge_schema import FlowEdgeCreate
from app.schemas.ai_flow_schema import (
    AiFlowCreateReq,
    AiFlowNodesBatchReq,
    AiFlowNodesConfigReq,
    AiFlowNodesDeleteReq,
    AiFlowEdgesBatchReq,
    AiFlowEdgesDeleteReq,
    AiFlowDetailResponse,
)
from app.services.flow_service import flow_service
from app.services.global_config_service import global_config_service
from app.agent_flow.handler_registry import NodeHandlerRegistry


def _get_default_config(node_type: str) -> dict:
    """从节点 handler 的 ConfigClass 获取默认配置。"""
    handler_cls = NodeHandlerRegistry.get_handler_class(node_type)
    if not handler_cls:
        handler_cls = NodeHandlerRegistry._get_factory_handler_class(node_type)
    if handler_cls:
        return handler_cls.get_default_config()
    return {}


def _fill_node_defaults(node_type: str, base_config: dict | None) -> dict:
    """用默认值补全缺失字段，不覆盖已有值。"""
    defaults = _get_default_config(node_type)
    if not defaults:
        return base_config or {}
    bc = dict(base_config or {})
    for key, default_val in defaults.items():
        if key not in bc:
            bc[key] = default_val
    return bc


class AiFlowApi:
    """AI 流程生成专用 API"""

    def __init__(self):
        self.router = APIRouter(prefix="/api/ai/flow", tags=["AI流程生成"])
        self._register_routes()

    def _register_routes(self):
        self.router.add_api_route(
            "/node-types",
            self.list_node_types,
            methods=["GET"],
            summary="获取所有节点类型",
        )
        self.router.add_api_route(
            "/node-types/{node_type}/config-schema",
            self.get_node_config_schema,
            methods=["GET"],
            summary="获取节点类型配置字段",
        )
        self.router.add_api_route(
            "/config-schemas",
            self.get_all_config_schemas,
            methods=["GET"],
            summary="批量获取所有节点类型配置字段",
        )
        self.router.add_api_route(
            "/list",
            self.list_flows,
            methods=["GET"],
            summary="AI获取流程列表",
        )
        self.router.add_api_route(
            "/create",
            self.create_flow,
            methods=["POST"],
            summary="AI创建空流程",
        )
        self.router.add_api_route(
            "/delete/{flow_id}",
            self.delete_flow,
            methods=["POST"],
            summary="AI删除流程",
        )
        self.router.add_api_route(
            "/{flow_id}/nodes/batch",
            self.batch_add_nodes,
            methods=["POST"],
            summary="AI批量创建节点",
        )
        self.router.add_api_route(
            "/{flow_id}/nodes/batch/delete",
            self.batch_delete_nodes,
            methods=["POST"],
            summary="AI批量删除节点",
        )
        self.router.add_api_route(
            "/{flow_id}/nodes/batch/config",
            self.batch_config_nodes,
            methods=["POST"],
            summary="AI批量配置节点",
        )
        self.router.add_api_route(
            "/{flow_id}/edges/batch",
            self.batch_add_edges,
            methods=["POST"],
            summary="AI批量创建边",
        )
        self.router.add_api_route(
            "/{flow_id}/edges/batch/delete",
            self.batch_delete_edges,
            methods=["POST"],
            summary="AI批量删除边",
        )
        self.router.add_api_route(
            "/{flow_id}/detail",
            self.get_flow_detail,
            methods=["GET"],
            summary="AI获取流程详情",
        )

    async def create_flow(
        self,
        data: AiFlowCreateReq,
        db: AsyncSession = Depends(get_db),
    ):
        """创建空流程，返回 id、名称、描述、类型。支持同时设置 input_schema/output_schema。"""
        flow_data = FlowCreate(
            name=data.name,
            description=data.description,
            flow_type=data.flow_type,
            input_schema=data.input_schema,
            output_schema=data.output_schema,
        )
        flow = await flow_service.create(db, flow_data)
        await db.commit()
        await db.refresh(flow)
        return ApiResponse.success(
            data={
                "id": flow.id,
                "name": flow.name,
                "description": flow.description,
                "flow_type": flow.flow_type,
            },
            msg="流程创建成功",
        )

    async def delete_flow(
        self,
        flow_id: int,
        db: AsyncSession = Depends(get_db),
    ):
        """删除流程及其所有关联数据。"""
        try:
            await flow_service.delete_with_cascade(db, flow_id)
            await db.commit()
            return ApiResponse.success(msg="流程删除成功")
        except ValueError as e:
            return ApiResponse.error(msg=str(e))

    async def batch_add_nodes(
        self,
        flow_id: int,
        data: AiFlowNodesBatchReq,
        db: AsyncSession = Depends(get_db),
    ):
        """批量创建节点。node_key 省略时自动生成，冲突时自动追加序号。返回 node_key 列表供创建边使用。"""
        try:
            nodes_data = [n.model_dump() for n in data.nodes]
            global_cfg = await global_config_service.get_default_llm_config(db)
            for nd in nodes_data:
                node_type = nd.get("node_type", "")
                bc = _fill_node_defaults(node_type, nd.get("base_config"))
                if node_type == "start" and not bc.get("input_variables"):
                    bc["input_variables"] = [
                        {
                            "name": "message",
                            "type": "string",
                            "description": "用户消息",
                            "required": True,
                        }
                    ]
                elif node_type == "llm":
                    needs_inject = not bc.get("model") or not bc.get("api_key")
                    if (
                        needs_inject
                        and global_cfg.get("model")
                        and global_cfg.get("api_key")
                    ):
                        if not bc.get("provider"):
                            bc["provider"] = global_cfg.get("provider", "deepseek")
                        if not bc.get("model"):
                            bc["model"] = global_cfg.get("model", "")
                        if not bc.get("api_key"):
                            bc["api_key"] = global_cfg.get("api_key", "")
                        if not bc.get("base_url") and global_cfg.get("base_url"):
                            bc["base_url"] = global_cfg["base_url"]
                        if not bc.get("context_length") and global_cfg.get(
                            "context_length"
                        ):
                            bc["context_length"] = global_cfg["context_length"]
                nd["base_config"] = bc
            created = await flow_service.batch_add_nodes(db, flow_id, nodes_data)
            await db.commit()
            return ApiResponse.success(
                data={"created_nodes": created},
                msg=f"成功创建 {len(created)} 个节点",
            )
        except ValueError as e:
            return ApiResponse.error(msg=str(e))

    async def batch_delete_nodes(
        self,
        flow_id: int,
        data: AiFlowNodesDeleteReq,
        db: AsyncSession = Depends(get_db),
    ):
        """批量删除节点，级联删除关联边。"""
        try:
            count = await flow_service.batch_delete_nodes_by_keys(
                db, flow_id, data.node_keys
            )
            await db.commit()
            return ApiResponse.success(msg=f"成功删除 {count} 个节点")
        except ValueError as e:
            return ApiResponse.error(msg=str(e))

    async def batch_config_nodes(
        self,
        flow_id: int,
        data: AiFlowNodesConfigReq,
        db: AsyncSession = Depends(get_db),
    ):
        """按 node_key 批量配置节点（node_name、base_config、position）。base_config 为整体替换。"""
        try:
            nodes_data = [n.model_dump() for n in data.nodes]
            existing_nodes = await flow_service._get_flow_nodes(db, flow_id)
            key_to_type = {n.node_key: n.node_type for n in existing_nodes}
            global_cfg = await global_config_service.get_default_llm_config(db)
            for nd in nodes_data:
                node_type = key_to_type.get(nd.get("node_key", ""))
                bc = _fill_node_defaults(node_type, nd.get("base_config"))
                if node_type == "llm":
                    needs_inject = not bc.get("model") or not bc.get("api_key")
                    if (
                        needs_inject
                        and global_cfg.get("model")
                        and global_cfg.get("api_key")
                    ):
                        if not bc.get("provider"):
                            bc["provider"] = global_cfg.get("provider", "deepseek")
                        if not bc.get("model"):
                            bc["model"] = global_cfg.get("model", "")
                        if not bc.get("api_key"):
                            bc["api_key"] = global_cfg.get("api_key", "")
                        if not bc.get("base_url") and global_cfg.get("base_url"):
                            bc["base_url"] = global_cfg["base_url"]
                        if not bc.get("context_length") and global_cfg.get(
                            "context_length"
                        ):
                            bc["context_length"] = global_cfg["context_length"]
                nd["base_config"] = bc
            count = await flow_service.batch_update_nodes_by_keys(
                db, flow_id, nodes_data
            )
            await db.commit()
            return ApiResponse.success(msg=f"成功配置 {count} 个节点")
        except ValueError as e:
            return ApiResponse.error(msg=str(e))

    async def batch_add_edges(
        self,
        flow_id: int,
        data: AiFlowEdgesBatchReq,
        db: AsyncSession = Depends(get_db),
    ):
        """批量创建边。自动校验节点存在性、handle 配对、工具边目标、工具节点限制、条件分支完整性。"""
        try:
            existing_nodes = await flow_service._get_flow_nodes(db, flow_id)
            existing_keys = {n.node_key for n in existing_nodes}

            for e in data.edges:
                if e.source_node_key not in existing_keys:
                    return ApiResponse.error(
                        msg=f"边的源节点「{e.source_node_key}」不存在"
                    )
                if e.target_node_key not in existing_keys:
                    return ApiResponse.error(
                        msg=f"边的目标节点「{e.target_node_key}」不存在"
                    )

            edges_create = [
                FlowEdgeCreate(
                    flow_id=flow_id,
                    source_node_key=e.source_node_key,
                    target_node_key=e.target_node_key,
                    source_handle=e.source_handle,
                    target_handle=e.target_handle,
                    condition=e.condition,
                    label=e.label,
                )
                for e in data.edges
            ]

            handle_error = await flow_service.validate_handle_existence(
                db, flow_id, edges_create
            )
            if handle_error:
                return ApiResponse.error(msg=handle_error)

            error = await flow_service.validate_no_tool_in_flow_edges(
                db, flow_id, edges_create
            )
            if error:
                return ApiResponse.error(msg=error)

            if any(getattr(e, "source_handle", None) == "tools" for e in edges_create):
                target_error = await flow_service.validate_tool_edge_targets(
                    db, flow_id, edges_create
                )
                if target_error:
                    return ApiResponse.error(msg=target_error)

                tool_error = await flow_service.validate_tool_edges(
                    db,
                    flow_id,
                    edges_create,
                    NodeHandlerRegistry.get_singleton_tool_types(),
                    NodeHandlerRegistry.get_config_singleton_types(),
                )
                if tool_error:
                    return ApiResponse.error(msg=tool_error)

            agent_error = await flow_service.validate_agent_edges(
                db, flow_id, edges_create
            )
            if agent_error:
                return ApiResponse.error(msg=agent_error)

            created_edges = await flow_service.batch_save_edges(
                db, flow_id, edges_create, []
            )

            cond_error = await flow_service.validate_condition_edges(db, flow_id)
            if cond_error:
                await db.rollback()
                return ApiResponse.error(msg=cond_error)

            result = [
                {
                    "source_node_key": e.source_node_key,
                    "target_node_key": e.target_node_key,
                }
                for e in created_edges
            ]
            return ApiResponse.success(
                data={"created_edges": result},
                msg=f"成功创建 {len(created_edges)} 条边",
            )
        except ValueError as e:
            return ApiResponse.error(msg=str(e))

    async def batch_delete_edges(
        self,
        flow_id: int,
        data: AiFlowEdgesDeleteReq,
        db: AsyncSession = Depends(get_db),
    ):
        """按边标识批量删除边。"""
        try:
            edges_data = [e.model_dump() for e in data.edges]
            count = await flow_service.batch_delete_edges_by_identifiers(
                db, flow_id, edges_data
            )
            await db.commit()
            return ApiResponse.success(msg=f"成功删除 {count} 条边")
        except ValueError as e:
            return ApiResponse.error(msg=str(e))

    async def get_flow_detail(
        self,
        flow_id: int,
        db: AsyncSession = Depends(get_db),
    ):
        """获取流程详情（含 Mermaid 流程图），供 AI 读取现有流程。"""
        flow = await flow_service.get_with_nodes_and_edges(db, flow_id)
        if flow is None:
            return ApiResponse.error(msg="流程不存在")
        nodes_views = FlowNodeBase.model_to_view_batch(flow.nodes) if flow.nodes else []
        edges_views = FlowEdgeBase.model_to_view_batch(flow.edges) if flow.edges else []
        mermaid = self._build_mermaid_from_flow(flow)
        detail = AiFlowDetailResponse(
            id=flow.id,
            name=flow.name,
            description=flow.description,
            flow_type=flow.flow_type,
            status=flow.status,
            saved_as_card=flow.saved_as_card,
            input_schema=flow.input_schema,
            output_schema=flow.output_schema,
            nodes=nodes_views,
            edges=edges_views,
            mermaid=mermaid,
        )
        return ApiResponse.success(data=detail, msg="查询成功")

    async def list_flows(
        self,
        flow_type: Optional[str] = None,
        keyword: Optional[str] = None,
        db: AsyncSession = Depends(get_db),
    ):
        """获取流程/智能体列表"""
        if flow_type:
            flows, _ = await flow_service.get_by_flow_type(db, flow_type)
        else:
            from sqlalchemy import select
            from app.models.flow import Flow

            query = select(Flow).where(Flow.is_delete == 0).order_by(Flow.id.desc())
            result = await db.execute(query)
            flows = list(result.scalars().all())

        items = [
            {
                "id": f.id,
                "name": f.name,
                "description": f.description or "",
                "flow_type": f.flow_type,
                "status": f.status,
                "is_builtin": getattr(f, "is_builtin", 0),
            }
            for f in flows
        ]
        if keyword:
            items = [i for i in items if keyword.lower() in i["name"].lower()]

        return ApiResponse.success(data={"total": len(items), "list": items})

    async def list_node_types(self):
        """返回所有可用的节点类型及中文标签"""
        from app.constants.node_types import NODE_TYPE_LABELS
        from app.models.flow_node import AGENT_ALLOWED_NODE_TYPES, AGENT_TOOL_NODE_TYPES

        all_types = []
        for type_key, label in NODE_TYPE_LABELS.items():
            all_types.append(
                {
                    "type": type_key,
                    "label": label,
                    "agent_allowed": type_key in AGENT_ALLOWED_NODE_TYPES,
                    "is_tool": type_key in AGENT_TOOL_NODE_TYPES,
                }
            )
        return ApiResponse.success(data=all_types)

    async def get_node_config_schema(self, node_type: str):
        """返回指定节点类型的配置字段描述"""
        from app.agent_flow.handler_registry import NodeHandlerRegistry
        from app.constants.node_types import NODE_TYPE_LABELS

        handler_cls = NodeHandlerRegistry.get_handler_class(node_type)
        if not handler_cls:
            handler_cls = NodeHandlerRegistry._get_factory_handler_class(node_type)
        if not handler_cls:
            return ApiResponse.error(msg=f"未知节点类型: {node_type}")

        schema = handler_cls.get_config_schema()
        return ApiResponse.success(
            data={
                "node_type": node_type,
                "label": NODE_TYPE_LABELS.get(node_type, node_type),
                "config_fields": schema,
            }
        )

    async def get_all_config_schemas(self):
        """批量返回所有已注册节点类型的配置字段描述"""
        from app.agent_flow.handler_registry import NodeHandlerRegistry
        from app.constants.node_types import NODE_TYPE_LABELS

        result: dict = {}
        for node_type in NodeHandlerRegistry.list_handlers():
            handler_cls = NodeHandlerRegistry.get_handler_class(node_type)
            if not handler_cls:
                handler_cls = NodeHandlerRegistry._get_factory_handler_class(node_type)
            if not handler_cls:
                continue
            schema = handler_cls.get_config_schema()
            result[node_type] = {
                "label": NODE_TYPE_LABELS.get(node_type, node_type),
                "config_fields": schema,
            }
        return ApiResponse.success(data=result)

    @staticmethod
    def _build_mermaid_from_flow(flow) -> str:
        """通过 GraphBuilder 构建图并生成 Mermaid，仅展示执行流程。"""
        from app.agent_flow.graph_builder import GraphBuilder
        from app.agent_flow.node_handlers.base_handler import BaseNodeHandler
        from app.agent_flow.handler_registry import NodeHandlerRegistry

        class _PlaceholderHandler(BaseNodeHandler):
            async def execute(self, node, state, config=None, *, writer=None):
                return {"visited_nodes": [node.node_key]}

        builder = GraphBuilder(flow)

        for node_key, node in builder.nodes.items():
            if node.node_type == "mcp":
                continue
            if not NodeHandlerRegistry.is_registered(node.node_type):
                builder.register_handler(node.node_type, _PlaceholderHandler())

        try:
            builder.build()
            mermaid = builder.get_graph_mermaid()
        except ValueError as e:
            return f'graph TD\n    error["流程结构验证失败: {e}"]'

        return mermaid


ai_flow_api = AiFlowApi()
router = ai_flow_api.router

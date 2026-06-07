"""
执行 API 路由
处理流程执行相关的路由定义
"""

from typing import Optional
from fastapi import Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.api.base_api import BaseApi, RouteConfig
from app.models.flow_execution import FlowExecution, ExecutionStatus
from app.models.conversation_message import ConversationMessage
from app.models.flow import Flow
from app.services.flow_executor_service import flow_executor_service
from app.services.flow_execution_service import flow_execution_service
from app.services.interrupt_service import interrupt_service
from app.utils.sse import create_sse_response
from app.schemas.execution_schema import (
    FlowExecutionBase,
    FlowExecutionCreate,
    NodeExecutionBase,
    ExecutionInput,
)
from app.schemas.conversation_schema import HumanInputSubmit
from app.schemas.base_schema import ApiResponse, PaginationParams, PaginatedResponse


class ExecutionApi(
    BaseApi[
        FlowExecution,
        FlowExecutionBase,
        FlowExecutionBase,
        FlowExecutionCreate,
        FlowExecutionBase,
    ]
):
    """流程执行 API"""

    def __init__(self):
        super().__init__(
            service=flow_execution_service,
            router_prefix="/api/execution",
            router_tags=["流程执行"],
            route_config=RouteConfig(
                enable_page=False,
                enable_get=True,
                enable_create=False,
                enable_update=False,
                enable_delete=False,
                enable_batch_delete=False,
            ),
        )
        self._register_custom_routes()

    def _register_custom_routes(self):
        """注册自定义路由"""

        @self.router.post("/page", summary="分页查询执行记录")
        async def page_query(
            query_params: PaginationParams[FlowExecutionBase],
            db: AsyncSession = Depends(get_db),
        ):
            """分页查询执行记录（附带流程名称）"""
            items, total = await flow_execution_service.page_query(db, query_params)
            # 批量查询关联的流程名称
            flow_ids = list({item.flow_id for item in items if item.flow_id})
            flow_name_map: dict[int, str] = {}
            if flow_ids:
                flow_query = select(Flow.id, Flow.name).where(
                    Flow.id.in_(flow_ids), Flow.is_delete == 0
                )
                flow_result = await db.execute(flow_query)
                flow_name_map = {row[0]: row[1] for row in flow_result}
            # 转换为视图并填充流程名称
            views = []
            for item in items:
                view = FlowExecutionBase.model_to_view(item)
                view.flow_name = flow_name_map.get(item.flow_id)
                views.append(view)
            paginated_data = PaginatedResponse.create(
                items=views,
                total=total,
                page=query_params.page,
                page_size=query_params.page_size,
            )
            return ApiResponse.success(data=paginated_data, msg="查询成功")

        @self.router.get(
            "/nodes/{execution_id}",
            response_model=ApiResponse[list[NodeExecutionBase]],
            summary="获取节点执行详情",
        )
        async def get_node_executions(
            execution_id: int, db: AsyncSession = Depends(get_db)
        ):
            """获取节点执行详情"""
            nodes = await flow_executor_service.get_node_executions(db, execution_id)

            # 从 conversation_message 聚合各节点的 token 用量
            token_query = (
                select(
                    ConversationMessage.node_key,
                    func.coalesce(func.sum(ConversationMessage.prompt_tokens), 0),
                    func.coalesce(func.sum(ConversationMessage.completion_tokens), 0),
                    func.coalesce(func.sum(ConversationMessage.total_tokens), 0),
                )
                .where(
                    ConversationMessage.execution_id == execution_id,
                    ConversationMessage.role == "ai",
                    ConversationMessage.is_delete == 0,
                )
                .group_by(ConversationMessage.node_key)
            )
            token_result = await db.execute(token_query)
            token_map: dict[str, dict] = {
                row[0]: {
                    "prompt_tokens": row[1],
                    "completion_tokens": row[2],
                    "total_tokens": row[3],
                }
                for row in token_result
            }

            # 查询所有 conversation_message，按 node_key 分组构建 execution_steps
            msg_query = (
                select(ConversationMessage)
                .where(
                    ConversationMessage.execution_id == execution_id,
                    ConversationMessage.is_delete == 0,
                )
                .order_by(ConversationMessage.sequence.asc())
            )
            msg_result = await db.execute(msg_query)
            all_messages = list(msg_result.scalars().all())
            msg_by_node: dict[str, list] = {}
            for msg in all_messages:
                msg_by_node.setdefault(msg.node_key, []).append(msg)

            views: list[NodeExecutionBase] = []
            for node in nodes:
                view = NodeExecutionBase.model_to_view(node)
                tokens = token_map.get(node.node_key)
                if tokens and tokens["total_tokens"] > 0:
                    view.prompt_tokens = tokens["prompt_tokens"]
                    view.completion_tokens = tokens["completion_tokens"]
                    view.total_tokens = tokens["total_tokens"]
                # 从 conversation_message 实时构建 execution_steps
                node_msgs = msg_by_node.get(node.node_key, [])
                if node_msgs:
                    view.execution_steps = (
                        flow_executor_service._convert_messages_to_steps(node_msgs)
                    )
                views.append(view)
            return ApiResponse.success(data=views, msg="查询成功")

        @self.router.post(
            "/cancel/{id}",
            response_model=ApiResponse[FlowExecutionBase],
            summary="取消执行",
        )
        async def cancel_execution(id: int, db: AsyncSession = Depends(get_db)):
            """取消执行"""
            interrupt_service.set_flow_interrupted(id)
            execution = await flow_executor_service.cancel_execution(db, id)
            if execution is None:
                return ApiResponse.error(msg="未找到数据")
            return ApiResponse.success(
                data=FlowExecutionBase.model_to_view(execution), msg="取消成功"
            )

        @self.router.post("/stream/{flow_id}", summary="流式执行流程(SSE)")
        async def stream_execution(flow_id: int, input_data: ExecutionInput):
            """
            流式执行流程（Server-Sent Events）

            事件类型：
            - flow_start: 流程开始
            - node_start: 节点开始执行
            - node_thinking: 节点思考内容（LLM节点）
            - node_content: 节点响应内容（LLM节点）
            - node_done: 节点执行完成
            - flow_done: 流程执行完成
            - error: 执行错误
            """

            async def stream():
                data = input_data.input_data if input_data else None
                files = input_data.files if input_data else None
                async for event in flow_executor_service.execute_stream(
                    flow_id, data, files
                ):
                    yield event

            return await create_sse_response(stream())

        @self.router.post(
            "/human-input-stream/{execution_id}", summary="流式恢复执行(SSE)"
        )
        async def resume_execution_stream(execution_id: int, data: HumanInputSubmit):
            """提交人工输入并流式恢复执行"""
            from app.agent_flow.flow_event import FlowEventFactory

            async def stream():
                yield FlowEventFactory.resume_start(execution_id)

                async for event in flow_executor_service.resume_execution(
                    execution_id, data.input
                ):
                    yield event

            return await create_sse_response(stream())

        @self.router.get("/wait-status/{execution_id}", summary="获取等待状态详情")
        async def get_wait_status(
            execution_id: int, db: AsyncSession = Depends(get_db)
        ):
            """获取执行的等待状态详情"""
            execution = await flow_executor_service.get_execution(db, execution_id)
            if execution is None:
                return ApiResponse.error(msg="执行记录不存在")

            if execution.status != ExecutionStatus.WAITING_HUMAN.value:
                return ApiResponse.success(
                    data={"waiting": False, "status": execution.status},
                    msg="不在等待状态",
                )

            wait_data = execution.wait_data or {}
            return ApiResponse.success(
                data={
                    "waiting": True,
                    "execution_id": execution_id,
                    "prompt": wait_data.get("question", "请提供输入"),
                    "context": wait_data.get("context"),
                    "output_variable": wait_data.get(
                        "output_variable", "human_feedback"
                    ),
                    "timeout": wait_data.get("timeout", 600),
                },
                msg="查询成功",
            )

        @self.router.get("/conversation-history/{execution_id}", summary="获取对话历史")
        async def get_conversation_history(
            execution_id: int,
            node_key: Optional[str] = None,
            db: AsyncSession = Depends(get_db),
        ):
            """获取对话历史"""
            import json

            query = select(ConversationMessage).where(
                ConversationMessage.execution_id == execution_id,
                ConversationMessage.is_delete == 0,
            )

            if node_key:
                query = query.where(ConversationMessage.node_key == node_key)

            query = query.order_by(ConversationMessage.sequence.asc())
            result = await db.execute(query)
            messages = result.scalars().all()

            role_mapping = {
                "system": "system",
                "human": "user",
                "ai": "assistant",
                "tool": "tool",
            }

            formatted_messages = []
            for msg in messages:
                role = role_mapping.get(msg.role, msg.role)
                item = {"role": role, "content": msg.content or ""}
                if msg.name:
                    item["name"] = msg.name
                if msg.tool_calls:
                    item["tool_calls"] = msg.tool_calls
                    if not item["content"]:
                        tool_info = []
                        for tc in msg.tool_calls:
                            tool_name = tc.get("name", "unknown")
                            tool_args = tc.get("args", {})
                            args_str = (
                                json.dumps(tool_args, ensure_ascii=False)
                                if tool_args
                                else ""
                            )
                            tool_info.append(
                                f"{tool_name}({args_str})" if args_str else tool_name
                            )
                        item["content"] = f"[调用工具: {', '.join(tool_info)}]"
                formatted_messages.append(item)

            return ApiResponse.success(
                data={"messages": formatted_messages}, msg="查询成功"
            )


execution_api = ExecutionApi()
router = execution_api.router

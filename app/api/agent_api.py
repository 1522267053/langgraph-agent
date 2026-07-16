"""
Agent API 路由
处理Agent相关的路由定义
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.models.flow import FlowType
from app.services.agent_executor_service import agent_executor_service
from app.services.flow_service import flow_service
from app.services.interrupt_service import interrupt_service
from app.services.tool_approval_service import tool_approval_service

from app.utils.sse import create_sse_response
from app.schemas.agent_schema import (
    AgentSessionResponse,
    AgentSessionListResponse,
    AgentSessionPageRequest,
    AgentMessageResponse,
    AgentMessageListResponse,
    AgentMessagePageRequest,
    AgentChatRequest,
    AgentResumeRequest,
    AgentFlowResponse,
    AgentFlowListResponse,
)
from app.schemas.base_schema import ApiResponse

logger = logging.getLogger(__name__)


class ToolApprovalRequest(BaseModel):
    action: str = Field(..., description="approved 或 rejected")


class AgentSearchRequest(BaseModel):
    keyword: str = Field(..., description="搜索关键词")


class AgentApi:
    """Agent API"""

    def __init__(self):
        self.router = APIRouter(prefix="/api/agent", tags=["Agent管理"])
        self._register_routes()

    def _register_routes(self):
        """注册所有路由"""

        @self.router.get(
            "/builtin",
            response_model=ApiResponse[AgentFlowResponse],
            summary="获取内置Agent",
        )
        async def get_builtin_agent(db: AsyncSession = Depends(get_db)):
            """获取内置 Agent 信息"""
            from sqlalchemy import select
            from app.models.flow import Flow, FlowType

            query = select(Flow).where(
                Flow.is_builtin == 1,
                Flow.is_delete == 0,
                Flow.flow_type == FlowType.AGENT.value,
            )
            result = await db.execute(query)
            flow = result.scalar_one_or_none()
            if not flow:
                return ApiResponse.error(msg="内置Agent不存在")
            return ApiResponse.success(
                data=AgentFlowResponse.model_validate(flow), msg="查询成功"
            )

        @self.router.get(
            "/list",
            response_model=ApiResponse[AgentFlowListResponse],
            summary="获取Agent列表",
        )
        async def get_agent_list(
            exclude_id: Optional[int] = None, db: AsyncSession = Depends(get_db)
        ):
            """获取所有Agent（flow_type=agent的Flow），可排除指定ID"""
            flows, total = await flow_service.get_by_flow_type(
                db, FlowType.AGENT.value, exclude_id=exclude_id
            )
            agents = [AgentFlowResponse.model_validate(f) for f in flows]
            return ApiResponse.success(
                data=AgentFlowListResponse(total=total, list=agents), msg="查询成功"
            )

        @self.router.get(
            "/get/{id}",
            response_model=ApiResponse[AgentFlowResponse],
            summary="获取Agent详情",
        )
        async def get_agent(id: int, db: AsyncSession = Depends(get_db)):
            """获取Agent详情"""
            flow = await flow_service.get_by_id(db, id)
            if not flow or flow.flow_type != FlowType.AGENT.value:
                return ApiResponse.error(msg="Agent不存在")
            return ApiResponse.success(
                data=AgentFlowResponse.model_validate(flow), msg="查询成功"
            )

        @self.router.post(
            "/{id}/sessions/page",
            response_model=ApiResponse[AgentSessionListResponse],
            summary="获取会话列表",
        )
        async def get_sessions(
            id: int,
            req: AgentSessionPageRequest,
            db: AsyncSession = Depends(get_db),
        ):
            """获取Agent的会话列表"""
            sessions, total = await agent_executor_service.get_sessions(
                db, id, req.page, req.page_size
            )
            session_list = [AgentSessionResponse.model_validate(s) for s in sessions]
            return ApiResponse.success(
                data=AgentSessionListResponse(total=total, list=session_list),
                msg="查询成功",
            )

        @self.router.post(
            "/{id}/sessions",
            response_model=ApiResponse[AgentSessionResponse],
            summary="创建新会话",
        )
        async def create_session(id: int, db: AsyncSession = Depends(get_db)):
            """创建新会话"""
            flow = await flow_service.get_by_id(db, id)
            if not flow or flow.flow_type != FlowType.AGENT.value:
                return ApiResponse.error(msg="Agent不存在")

            session = await agent_executor_service.create_session(db, id)
            return ApiResponse.success(
                data=AgentSessionResponse.model_validate(session), msg="创建成功"
            )

        @self.router.get(
            "/{id}/deleteSession/{session_id}",
            response_model=ApiResponse,
            summary="删除会话",
        )
        async def delete_session(
            id: int, session_id: int, db: AsyncSession = Depends(get_db)
        ):
            """删除会话"""
            success = await agent_executor_service.delete_session(db, session_id)
            if not success:
                return ApiResponse.error(msg="会话不存在")
            return ApiResponse.success(msg="删除成功")

        @self.router.get(
            "/{id}/sessions/{session_id}/deleteMessages/{message_id}",
            response_model=ApiResponse,
            summary="删除消息及之后内容",
        )
        async def delete_messages_from(
            id: int,
            session_id: int,
            message_id: int,
            db: AsyncSession = Depends(get_db),
        ):
            """删除指定消息及之后的所有消息，返回被删除的用户消息内容用于重新编辑"""
            deleted_content = await agent_executor_service.delete_messages_from(
                db, session_id, message_id
            )
            if deleted_content is None:
                return ApiResponse.error(msg="消息不存在")
            return ApiResponse.success(
                data={"content": deleted_content}, msg="删除成功"
            )

        @self.router.post(
            "/{id}/sessions/{session_id}/messages/page",
            response_model=ApiResponse[AgentMessageListResponse],
            summary="获取消息历史",
        )
        async def get_messages(
            id: int,
            session_id: int,
            req: AgentMessagePageRequest,
            db: AsyncSession = Depends(get_db),
        ):
            """获取会话的消息历史，支持分页加载"""
            messages, total = await agent_executor_service.get_messages(
                db, session_id, limit=req.limit, before_id=req.before_id
            )
            message_list = []
            for m in messages:
                item = AgentMessageResponse.model_validate(m)
                item.content = m.content or ""
                message_list.append(item)
            return ApiResponse.success(
                data=AgentMessageListResponse(total=total, list=message_list),
                msg="查询成功",
            )

        @self.router.post("/{id}/sessions/{session_id}/chat", summary="发送消息(SSE)")
        async def chat(
            id: int,
            session_id: int,
            request: AgentChatRequest,
        ):
            """
            发送消息（Server-Sent Events）

            事件类型：
            - flow_start: 对话开始
            - node_start: 节点开始执行
            - node_thinking: 节点思考内容（LLM节点）
            - node_content: 节点响应内容（LLM节点）
            - node_done: 节点执行完成
            - flow_done: 对话完成
            - waiting_human: 等待人工输入
            - error: 执行错误
            """

            async def stream():
                async for event in agent_executor_service.chat_stream(
                    session_id, request.content, request.params
                ):
                    yield event

            return await create_sse_response(
                stream(),
                detach_on_disconnect=True,
                task_store=agent_executor_service._streaming_tasks,
                task_key=str(session_id),
            )

        @self.router.post("/{id}/sessions/{session_id}/resume", summary="恢复执行(SSE)")
        async def resume(
            id: int,
            session_id: int,
            request: AgentResumeRequest,
        ):
            """恢复执行（人工输入后）"""

            async def stream():
                async for event in agent_executor_service.resume_stream(
                    session_id, request.human_input
                ):
                    yield event

            return await create_sse_response(
                stream(),
                detach_on_disconnect=True,
                task_store=agent_executor_service._streaming_tasks,
                task_key=str(session_id),
            )

        @self.router.post("/{id}/sessions/{session_id}/cancel", summary="中断会话执行")
        async def cancel_session(
            id: int, session_id: int, db: AsyncSession = Depends(get_db)
        ):
            """中断Agent会话执行"""
            session = await agent_executor_service._get_session(db, session_id)
            if not session:
                return ApiResponse.error(msg="会话不存在")

            interrupt_service.set_agent_interrupted(session_id)
            tool_approval_service.cancel(session_id)
            agent_executor_service._pending_save_sessions.add(session_id)
            # 直接取消后台 streaming task，立即中断 LLM 请求
            task = agent_executor_service._streaming_tasks.get(str(session_id))
            if task and not task.done():
                task.cancel()
            # 同步清理 checkpoint，防止与 DB 消息不同步
            try:
                await agent_executor_service._cleanup_thread_checkpoint(session_id)
            except Exception as e:
                logger.warning(f"cancel清理checkpoint失败: {e}")
            # 立即释放运行锁，防止阻塞后续消息
            agent_executor_service._running_sessions.discard(session_id)
            return ApiResponse.success(msg="已发送中断信号")

        @self.router.post("/{id}/sessions/{session_id}/tool_approval")
        async def tool_approval(id: int, session_id: int, req: ToolApprovalRequest):
            """前端确认/拒绝工具执行"""
            if req.action not in ("approved", "rejected"):
                return ApiResponse.error(msg="action 必须为 approved 或 rejected")
            resolved = tool_approval_service.resolve(session_id, req.action)
            if not resolved:
                return ApiResponse.error(msg="没有待确认的工具")
            return ApiResponse.success(msg="操作成功")

        @self.router.post(
            "/{id}/sessions/{session_id}/compress",
            summary="压缩会话上下文",
        )
        async def compress_session(
            id: int, session_id: int, db: AsyncSession = Depends(get_db)
        ):
            """启动后台压缩任务，前端通过轮询 /compressing 检测完成"""
            session = await agent_executor_service._get_session(db, session_id)
            if not session:
                return ApiResponse.error(msg="会话不存在")
            if session_id in agent_executor_service._compressing_sessions:
                return ApiResponse.error(msg="正在压缩中，请稍后再试")
            if session_id in agent_executor_service._running_sessions:
                return ApiResponse.error(msg="会话正在执行中，请稍后再试")
            asyncio.create_task(
                agent_executor_service._run_compress_background(session_id)
            )
            return ApiResponse.success(msg="开始压缩")

        @self.router.get(
            "/{id}/sessions/{session_id}/saving",
            response_model=ApiResponse,
            summary="查询会话是否正在等待中断后的消息保存",
        )
        async def check_saving(id: int, session_id: int):
            """前端中断后轮询此接口，等待后端 save_to_db 完成后再刷新消息"""
            saving = agent_executor_service.is_pending_save(session_id)
            return ApiResponse.success(data={"saving": saving})

        @self.router.get(
            "/{id}/sessions/{session_id}/compressing",
            response_model=ApiResponse,
            summary="查询会话是否正在压缩上下文",
        )
        async def check_compressing(
            id: int, session_id: int, db: AsyncSession = Depends(get_db)
        ):
            """查询指定会话是否正在压缩上下文"""
            is_compressing = await agent_executor_service.is_compressing_session(
                db, session_id
            )
            if is_compressing:
                return ApiResponse.success(data={"status": "compressing"})
            result = agent_executor_service.pop_compress_result(session_id)
            if result:
                error = result.get("error")
                return ApiResponse.success(
                    data={
                        "status": "failed" if error else "done",
                        "error": error,
                        "removed_count": result.get("removed_count", 0),
                    }
                )
            return ApiResponse.success(data={"status": "done"})

        @self.router.post(
            "/{id}/search",
            response_model=ApiResponse,
            summary="搜索会话和消息内容",
        )
        async def search_history(
            id: int,
            req: AgentSearchRequest,
            db: AsyncSession = Depends(get_db),
        ):
            """搜索 Agent 的会话标题和消息内容"""
            if not req.keyword or not req.keyword.strip():
                return ApiResponse.success(
                    data={"sessions": [], "messages": []}, msg="查询成功"
                )
            result = await agent_executor_service.search_history(
                db, id, req.keyword.strip()
            )
            return ApiResponse.success(data=result, msg="查询成功")

        # ---- 后台工具任务管理 ----

        @self.router.get(
            "/tools/running",
            response_model=ApiResponse,
            summary="获取运行中的后台工具任务",
        )
        async def get_running_tools():
            """获取所有运行中和最近完成的后台 Shell 任务"""
            from app.agent_flow.node_handlers.shell_handler import get_running_tasks

            return ApiResponse.success(data=get_running_tasks())

        @self.router.get(
            "/tools/{task_id}/status",
            response_model=ApiResponse,
            summary="获取后台工具任务状态",
        )
        async def get_tool_status(task_id: str):
            """获取单个后台任务的详细状态和输出"""
            from app.agent_flow.node_handlers.shell_handler import get_task_by_id

            result = get_task_by_id(task_id)
            if not result:
                return ApiResponse.error(msg="任务不存在或已过期")
            return ApiResponse.success(data=result)

        @self.router.post(
            "/tools/{task_id}/cancel",
            response_model=ApiResponse,
            summary="取消后台工具任务",
        )
        async def cancel_tool(task_id: str):
            """取消正在运行的后台 Shell 任务"""
            from app.agent_flow.node_handlers.shell_handler import cancel_task_by_id

            result = await cancel_task_by_id(task_id)
            if result.get("success"):
                return ApiResponse.success(data=result, msg="任务已取消")
            return ApiResponse.error(msg=result.get("error", "取消失败"))


agent_api = AgentApi()
router = agent_api.router

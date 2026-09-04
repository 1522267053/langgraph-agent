"""
Agent API 路由
处理Agent相关的路由定义
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.models.flow import FlowType
from app.services.agent_executor_service import (
    agent_executor_service,
    normalize_work_dir,
)
from app.services.builtin_agent_service import builtin_agent_service
from app.services.flow_service import flow_service
from app.services.interrupt_service import interrupt_service
from app.services.tool_approval_service import tool_approval_service

from app.utils.sse import create_sse_response
from app.schemas.agent_schema import (
    AgentSessionResponse,
    AgentSessionListResponse,
    AgentSessionPageRequest,
    AgentSessionCreateRequest,
    AgentSessionWorkDirRequest,
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


class CompressRequest(BaseModel):
    prompt: str = Field(
        default="", description="自定义压缩提示词，追加到默认提示词后，空则仅使用默认"
    )


class AgentSearchRequest(BaseModel):
    keyword: str = Field(..., description="搜索关键词")


class AgentRunEventsRequest(BaseModel):
    run_id: str = Field(..., description="后台执行 ID")
    after_event_id: int = Field(
        default=0, ge=0, description="仅回放该事件 ID 之后的事件"
    )


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
        async def create_session(
            id: int,
            req: Optional[AgentSessionCreateRequest] = None,
            db: AsyncSession = Depends(get_db),
        ):
            """创建新会话，可选携带项目工作路径"""
            flow = await flow_service.get_by_id(db, id)
            if not flow or flow.flow_type != FlowType.AGENT.value:
                return ApiResponse.error(msg="Agent不存在")

            try:
                work_dir = normalize_work_dir(req.work_dir if req else None)
            except ValueError as exc:
                return ApiResponse.error(msg=str(exc))

            session = await agent_executor_service.create_session(
                db, id, work_dir=work_dir
            )
            return ApiResponse.success(
                data=AgentSessionResponse.model_validate(session), msg="创建成功"
            )

        @self.router.put(
            "/{id}/sessions/{session_id}/workdir",
            response_model=ApiResponse[AgentSessionResponse],
            summary="切换会话工作路径",
        )
        async def update_work_dir(
            id: int,
            session_id: int,
            req: AgentSessionWorkDirRequest,
            db: AsyncSession = Depends(get_db),
        ):
            """中途切换会话的项目工作路径；work_dir 为空表示清除（回退默认目录）"""
            try:
                session = await agent_executor_service.update_work_dir(
                    db, session_id, req.work_dir
                )
            except ValueError as exc:
                return ApiResponse.error(msg=str(exc))
            if not session:
                return ApiResponse.error(msg="会话不存在")
            return ApiResponse.success(
                data=AgentSessionResponse.model_validate(session), msg="更新成功"
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
            "/{id}/sessions/{session_id}/revertPreview/{message_id}",
            response_model=ApiResponse,
            summary="预览回退将恢复的文件清单",
        )
        async def revert_preview(
            id: int,
            session_id: int,
            message_id: int,
            db: AsyncSession = Depends(get_db),
        ):
            """查询回退到指定消息时将恢复/删除的文件列表（shell 命令变更不在追踪范围）"""
            preview = await agent_executor_service.get_revert_preview(
                db, session_id, message_id
            )
            if preview is None:
                return ApiResponse.error(msg="会话不存在")
            return ApiResponse.success(data=preview, msg="查询成功")

        @self.router.post(
            "/{id}/sessions/{session_id}/restoreFiles/{message_id}",
            response_model=ApiResponse,
            summary="仅恢复文件变更（保留对话）",
        )
        async def restore_files(
            id: int,
            session_id: int,
            message_id: int,
            db: AsyncSession = Depends(get_db),
        ):
            """将文件恢复到指定消息之前的状态，不删除任何消息"""
            results = await agent_executor_service.restore_files_only(
                db, session_id, message_id
            )
            if results is None:
                return ApiResponse.error(msg="会话不存在")
            ok_count = sum(1 for r in results if r.get("status") == "ok")
            return ApiResponse.success(
                data={"reverted_files": results, "ok_count": ok_count},
                msg=f"已恢复 {ok_count} 个文件",
            )

        @self.router.get(
            "/{id}/sessions/{session_id}/deleteMessages/{message_id}",
            response_model=ApiResponse,
            summary="删除消息及之后内容",
        )
        async def delete_messages_from(
            id: int,
            session_id: int,
            message_id: int,
            restore_files: bool = True,
            db: AsyncSession = Depends(get_db),
        ):
            """删除指定消息及之后的所有消息，返回被删除的用户消息内容用于重新编辑；

            restore_files=True 时同步恢复该范围内追踪到的文件变更
            """
            deleted = await agent_executor_service.delete_messages_from(
                db, session_id, message_id, restore_files=restore_files
            )
            if deleted is None:
                return ApiResponse.error(msg="消息不存在")
            return ApiResponse.success(
                data={
                    "content": deleted["content"],
                    "files": deleted["files"],
                    "input_data": deleted["input_data"],
                    "reverted_files": deleted["reverted_files"],
                },
                msg="删除成功",
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
                db,
                session_id,
                limit=req.limit,
                before_id=req.before_id,
                after_id=req.after_id,
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

        @self.router.post("/{id}/sessions/{session_id}/chat", summary="启动Agent对话")
        async def chat(
            id: int,
            session_id: int,
            request: AgentChatRequest,
        ):
            """启动后台执行，事件通过 /events 独立订阅。"""
            try:
                run_id = agent_executor_service.start_chat_run(
                    session_id, request.content, request.params, model=request.model
                )
            except ValueError as exc:
                return ApiResponse.error(msg=str(exc))
            return ApiResponse.success(data={"run_id": run_id}, msg="执行已启动")

        @self.router.post("/{id}/sessions/{session_id}/resume", summary="启动恢复执行")
        async def resume(
            id: int,
            session_id: int,
            request: AgentResumeRequest,
        ):
            """启动人工输入后的后台恢复执行。"""
            try:
                run_id = agent_executor_service.start_resume_run(
                    session_id, request.human_input
                )
            except ValueError as exc:
                return ApiResponse.error(msg=str(exc))
            return ApiResponse.success(data={"run_id": run_id}, msg="执行已恢复")

        @self.router.post(
            "/{id}/sessions/{session_id}/events", summary="订阅Agent执行事件(SSE)"
        )
        async def subscribe_events(
            id: int,
            session_id: int,
            request: AgentRunEventsRequest,
        ):
            """订阅后台执行，并按 after_event_id 回放断线期间的事件。"""
            return await create_sse_response(
                agent_executor_service.subscribe_run(
                    session_id,
                    request.run_id,
                    request.after_event_id,
                )
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
            # 后台 Runner 负责保存消息和清理 checkpoint，避免与 API 并发清理。
            managed_cancelled = await agent_executor_service.cancel_run(session_id)
            if not managed_cancelled and not agent_executor_service.is_running(
                session_id
            ):
                try:
                    await agent_executor_service._cleanup_thread_checkpoint(session_id)
                except Exception as e:
                    logger.warning(f"cancel清理checkpoint失败: {e}")
                agent_executor_service._running_sessions.discard(session_id)
                agent_executor_service._waiting_sessions.discard(session_id)
                agent_executor_service._waiting_events.pop(session_id, None)
                agent_executor_service._pending_save_sessions.discard(session_id)
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
            id: int,
            session_id: int,
            req: CompressRequest | None = None,
            db: AsyncSession = Depends(get_db),
        ):
            """启动后台压缩任务，前端通过轮询 /compressing 检测完成"""
            session = await agent_executor_service._get_session(db, session_id)
            if not session:
                return ApiResponse.error(msg="会话不存在")
            custom_prompt = (req.prompt if req else "").strip()
            try:
                agent_executor_service.start_compress_background(
                    session_id, custom_prompt
                )
            except ValueError as exc:
                return ApiResponse.error(msg=str(exc))
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
            "/{id}/sessions/{session_id}/running",
            response_model=ApiResponse,
            summary="查询会话是否正在执行",
        )
        async def check_running(id: int, session_id: int):
            """页面刷新后检测会话是否仍在后台执行，前端据此显示停止按钮"""
            return ApiResponse.success(
                data=agent_executor_service.get_run_status(session_id)
            )

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

        @self.router.post(
            "/reset-builtin",
            response_model=ApiResponse,
            summary="恢复内置 Agent 出厂设置",
        )
        async def reset_builtin_agent(db: AsyncSession = Depends(get_db)):
            """删除内置 Agent 全部节点和边，按最新模板重新构建"""
            try:
                flow_id = await builtin_agent_service.reset(db)
                return ApiResponse.success(data={"id": flow_id}, msg="已恢复出厂设置")
            except ValueError as e:
                return ApiResponse.error(msg=str(e))


agent_api = AgentApi()
router = agent_api.router

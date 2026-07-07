"""
Webhook 配置服务

提供 Webhook 的 CRUD 和触发执行功能。
触发执行采用异步模式：立即返回状态，后台执行完成后通过 WebSocket 通知 + callback_url 回调。
每次触发创建 WebhookCallRecord，外部系统可通过免认证查询接口回查调用历史和消息。
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook import WebhookConfig
from app.models.webhook_call_record import WebhookCallRecord
from app.models.flow import FlowType
from app.models.flow_execution import ExecutionStatus
from app.schemas.webhook_schema import (
    WebhookConfigCreate,
    WebhookConfigUpdate,
    WebhookConfigCondition,
)
from app.services.base_service import BaseService

logger = logging.getLogger(__name__)


class WebhookService(
    BaseService[WebhookConfig, WebhookConfigCreate, WebhookConfigUpdate]
):
    """Webhook 配置服务"""

    def __init__(self):
        super().__init__(WebhookConfig)

    def _apply_filters(self, query, count_query, condition: WebhookConfigCondition):
        """应用查询过滤条件"""
        query, count_query = super()._apply_filters(query, count_query, condition)
        if condition:
            if hasattr(condition, "name") and condition.name:
                query, count_query = self._apply_like_filter(
                    query, count_query, "name", condition.name
                )
            if hasattr(condition, "flow_id") and condition.flow_id:
                query = query.where(WebhookConfig.flow_id == condition.flow_id)
                count_query = count_query.where(
                    WebhookConfig.flow_id == condition.flow_id
                )
            if hasattr(condition, "is_enabled") and condition.is_enabled is not None:
                query = query.where(WebhookConfig.is_enabled == condition.is_enabled)
                count_query = count_query.where(
                    WebhookConfig.is_enabled == condition.is_enabled
                )
        return query, count_query

    async def create(
        self, db: AsyncSession, obj_in: WebhookConfigCreate
    ) -> WebhookConfig:
        """创建 Webhook（自动生成 token）"""
        import uuid

        model = obj_in.to_model(WebhookConfig)
        model.token = uuid.uuid4().hex
        model.call_count = 0
        db.add(model)
        await db.commit()
        await db.refresh(model)
        return model

    async def get_by_token(
        self, db: AsyncSession, token: str
    ) -> Optional[WebhookConfig]:
        """通过 token 查找 Webhook 配置"""
        stmt = select(WebhookConfig).where(
            WebhookConfig.token == token,
            WebhookConfig.is_delete == 0,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    # ---- Webhook 调用记录管理 ----

    async def create_call_record(
        self, db: AsyncSession, webhook: WebhookConfig, input_data: dict
    ) -> WebhookCallRecord:
        """创建调用记录（触发时写入，status=执行中），同时更新调用计数"""
        webhook.call_count = (webhook.call_count or 0) + 1
        webhook.last_call_time = datetime.now()

        record = WebhookCallRecord(
            webhook_id=webhook.id,
            flow_id=webhook.flow_id,
            input_data=input_data,
            status=ExecutionStatus.RUNNING.value,
            callback_status="pending" if webhook.callback_url else "skipped",
            started_at=datetime.now(),
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        return record

    async def update_call_record_ref(
        self, db: AsyncSession, record_id: int, ref_type: str, ref_id: int
    ) -> None:
        """回填调用记录的引用信息（会话/执行创建后调用）"""
        record = await db.get(WebhookCallRecord, record_id)
        if record:
            record.ref_type = ref_type
            record.ref_id = ref_id
            await db.commit()

    async def finish_call_record(
        self,
        record_id: int,
        status: str,
        output_data: Optional[dict] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """完成调用记录（后台执行完成后调用）"""
        from app.config.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            record = await db.get(WebhookCallRecord, record_id)
            if record:
                record.status = self._to_execution_status(status)
                if output_data is not None:
                    record.output_data = output_data
                if error_message is not None:
                    record.error_message = error_message
                record.finished_at = datetime.now()
                await db.commit()

    async def update_callback_status(
        self, record_id: int, callback_status: str
    ) -> None:
        """更新回调状态"""
        from app.config.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            record = await db.get(WebhookCallRecord, record_id)
            if record:
                record.callback_status = callback_status
                await db.commit()

    @staticmethod
    def _to_execution_status(status_str: str) -> int:
        """将状态字符串转为 ExecutionStatus 枚举值"""
        mapping = {
            "success": ExecutionStatus.SUCCESS.value,
            "failed": ExecutionStatus.FAILED.value,
            "cancelled": ExecutionStatus.CANCELLED.value,
        }
        return mapping.get(status_str, ExecutionStatus.FAILED.value)

    # ---- Webhook 调用记录查询（供免认证 API 使用） ----

    async def get_call_records_by_token(
        self, db: AsyncSession, token: str, page: int = 1, page_size: int = 20
    ) -> tuple[list[WebhookCallRecord], int]:
        """通过 token 查询调用记录列表（分页）"""
        webhook = await self.get_by_token(db, token)
        if not webhook:
            return [], 0

        stmt = (
            select(WebhookCallRecord)
            .where(
                WebhookCallRecord.webhook_id == webhook.id,
                WebhookCallRecord.is_delete == 0,
            )
            .order_by(desc(WebhookCallRecord.started_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        count_stmt = select(func.count(WebhookCallRecord.id)).where(
            WebhookCallRecord.webhook_id == webhook.id,
            WebhookCallRecord.is_delete == 0,
        )
        result = await db.execute(stmt)
        records = list(result.scalars().all())
        count_result = await db.execute(count_stmt)
        total = count_result.scalar() or 0
        return records, total

    async def get_call_record_by_token(
        self, db: AsyncSession, token: str, call_id: int
    ) -> Optional[WebhookCallRecord]:
        """通过 token + call_id 获取单条调用记录"""
        webhook = await self.get_by_token(db, token)
        if not webhook:
            return None
        stmt = select(WebhookCallRecord).where(
            WebhookCallRecord.id == call_id,
            WebhookCallRecord.webhook_id == webhook.id,
            WebhookCallRecord.is_delete == 0,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_call_record_messages(
        self,
        db: AsyncSession,
        record: WebhookCallRecord,
        before_id: Optional[int] = None,
        limit: int = 20,
    ) -> tuple[list, int]:
        """获取调用记录的消息列表（按 ref_type 自动分流）"""
        if not record.ref_type or not record.ref_id:
            return [], 0

        if record.ref_type == "session":
            return await self._get_agent_messages(db, record.ref_id, before_id, limit)
        elif record.ref_type == "execution":
            return await self._get_conversation_messages(
                db, record.ref_id, before_id, limit
            )
        return [], 0

    async def _get_agent_messages(
        self,
        db: AsyncSession,
        session_id: int,
        before_id: Optional[int] = None,
        limit: int = 20,
    ) -> tuple[list, int]:
        """获取 Agent 会话消息"""
        from app.models.agent_message import AgentMessage

        conditions = [
            AgentMessage.session_id == session_id,
            AgentMessage.is_delete == 0,
        ]
        if before_id:
            conditions.append(AgentMessage.id < before_id)

        stmt = (
            select(AgentMessage)
            .where(*conditions)
            .order_by(desc(AgentMessage.id))
            .limit(limit)
        )
        count_stmt = select(func.count(AgentMessage.id)).where(
            AgentMessage.session_id == session_id,
            AgentMessage.is_delete == 0,
        )
        result = await db.execute(stmt)
        messages = list(reversed(result.scalars().all()))
        count_result = await db.execute(count_stmt)
        total = count_result.scalar() or 0
        return messages, total

    async def _get_conversation_messages(
        self,
        db: AsyncSession,
        execution_id: int,
        before_id: Optional[int] = None,
        limit: int = 20,
    ) -> tuple[list, int]:
        """获取 Flow 执行消息"""
        from app.models.conversation_message import ConversationMessage

        conditions = [
            ConversationMessage.execution_id == execution_id,
        ]
        if before_id:
            conditions.append(ConversationMessage.id < before_id)

        stmt = (
            select(ConversationMessage)
            .where(*conditions)
            .order_by(desc(ConversationMessage.id))
            .limit(limit)
        )
        count_stmt = select(func.count(ConversationMessage.id)).where(
            ConversationMessage.execution_id == execution_id,
        )
        result = await db.execute(stmt)
        messages = list(reversed(result.scalars().all()))
        count_result = await db.execute(count_stmt)
        total = count_result.scalar() or 0
        return messages, total

    # ---- Webhook 会话/消息管理（供免认证 API 使用，仅 Agent 类型流程） ----

    async def get_sessions_by_token(
        self, db: AsyncSession, token: str, page: int = 1, page_size: int = 20
    ) -> tuple[list, int]:
        """通过 token 查询该 Webhook 创建的会话列表（分页）

        仅返回由该 Webhook 触发创建的会话（webhook_id 匹配），用户聊天会话不在此列。
        """
        from app.models.agent_session import AgentSession

        webhook = await self.get_by_token(db, token)
        if not webhook:
            return [], 0

        conditions = [
            AgentSession.webhook_id == webhook.id,
            AgentSession.is_delete == 0,
        ]
        count_stmt = select(func.count()).select_from(AgentSession).where(*conditions)
        count_result = await db.execute(count_stmt)
        total = count_result.scalar() or 0

        offset = (page - 1) * page_size
        stmt = (
            select(AgentSession)
            .where(*conditions)
            .order_by(desc(AgentSession.id))
            .offset(offset)
            .limit(page_size)
        )
        result = await db.execute(stmt)
        sessions = list(result.scalars().all())
        return sessions, total

    async def get_session_by_token(
        self, db: AsyncSession, token: str, session_id: int
    ) -> tuple[Optional[WebhookConfig], Optional[object]]:
        """通过 token + session_id 获取会话，并校验会话由该 Webhook 创建

        Returns:
            (webhook, session)：webhook 不存在返回 (None, None)；
            会话不存在或非该 Webhook 创建返回 (webhook, None)。
        """
        from app.services.agent_executor_service import agent_executor_service

        webhook = await self.get_by_token(db, token)
        if not webhook:
            return None, None
        session = await agent_executor_service._get_session(db, session_id)
        if not session or session.webhook_id != webhook.id:
            return webhook, None
        return webhook, session

    async def delete_session_by_token(
        self, db: AsyncSession, token: str, session_id: int
    ) -> tuple[bool, str]:
        """通过 token 删除会话（含消息和 checkpoint）

        Returns:
            (success, msg)：(False, "Webhook 不存在") / (False, "会话不存在或不属于该Webhook") / (True, "")
        """
        from app.services.agent_executor_service import agent_executor_service

        webhook, session = await self.get_session_by_token(db, token, session_id)
        if not webhook:
            return False, "Webhook 不存在"
        if not session:
            return False, "会话不存在或不属于该Webhook"
        await agent_executor_service.delete_session(db, session_id)
        return True, ""

    async def get_session_messages_by_token(
        self,
        db: AsyncSession,
        token: str,
        session_id: int,
        before_id: Optional[int] = None,
        limit: int = 20,
    ) -> tuple[list, int]:
        """通过 token 查询指定会话的消息列表（游标分页）

        会话归属校验失败返回 ([], 0)。
        """
        from app.services.agent_executor_service import agent_executor_service

        _, session = await self.get_session_by_token(db, token, session_id)
        if not session:
            return [], 0
        return await agent_executor_service.get_messages(
            db, session_id, limit, before_id
        )

    async def delete_session_message_by_token(
        self, db: AsyncSession, token: str, session_id: int, message_id: int
    ) -> tuple[bool, str]:
        """通过 token 删除指定会话的 message_id 及其后所有消息（含 checkpoint 清理）

        Returns:
            (success, msg)：校验失败 / 消息不存在均返回 (False, msg)。
        """
        from app.services.agent_executor_service import agent_executor_service

        webhook, session = await self.get_session_by_token(db, token, session_id)
        if not webhook:
            return False, "Webhook 不存在"
        if not session:
            return False, "会话不存在或不属于该Webhook"
        result = await agent_executor_service.delete_messages_from(
            db, session_id, message_id
        )
        if result is None:
            return False, "消息不存在"
        return True, ""

    # ---- 触发执行（含调用记录） ----

    async def trigger_flow(
        self,
        db: AsyncSession,
        webhook: WebhookConfig,
        input_data: dict,
        session_id: Optional[int] = None,
    ) -> dict:
        """异步触发流程执行（含调用记录）

        后台执行流程，完成后通过 WebSocket 通知应用用户。
        如果配置了 callback_url，还会 POST 回调通知外部系统。
        可指定 session_id 复用已有 Agent 会话（不传则新建，新建的 session_id 会同步返回）。

        Args:
            db: 数据库会话
            webhook: Webhook 配置
            input_data: 合并后的输入数据
            session_id: 可选，Agent 类型时复用指定会话

        Returns:
            {"status": "started", "webhook_id": webhook.id, "call_id": record.id, "session_id": ...}
        """
        from app.services.flow_service import flow_service

        flow_id = webhook.flow_id

        # 查询流程类型和名称（复用现有 db）
        flow = await flow_service.get_by_id(db, flow_id, raise_not_found=False)
        flow_type = flow.flow_type if flow else None
        flow_name = flow.name or ""

        # Agent 类型：同步解析 session_id（新建或校验外部传入）
        resolved_session_id = session_id
        if flow_type == FlowType.AGENT.value:
            if session_id is not None:
                # 校验外部传入的 session_id（fail fast）
                from app.models.agent_session import AgentSession

                stmt = select(AgentSession).where(
                    AgentSession.id == session_id,
                    AgentSession.flow_id == flow_id,
                    AgentSession.is_delete == 0,
                )
                result = await db.execute(stmt)
                if not result.scalar_one_or_none():
                    raise ValueError(f"会话 {session_id} 不存在或不属于该 Agent")
            else:
                # 新建会话
                from app.services.agent_executor_service import agent_executor_service

                session = await agent_executor_service.create_session(
                    db, flow_id, webhook_id=webhook.id
                )
                session.title = f"[Webhook] {webhook.name}"
                resolved_session_id = session.id

        # 创建调用记录（合并 call_count 更新）
        record = await self.create_call_record(db, webhook, input_data)

        # Agent 类型：回填 ref
        if resolved_session_id is not None:
            await self.update_call_record_ref(
                db, record.id, "session", resolved_session_id
            )

        # 后台异步执行（fire-and-forget）
        asyncio.create_task(
            self._execute_webhook_flow(
                flow_id=flow_id,
                input_data=input_data,
                flow_name=flow_name,
                flow_type=flow_type,
                callback_url=webhook.callback_url,
                webhook_name=webhook.name,
                call_record_id=record.id,
                session_id=resolved_session_id,
            )
        )

        result = {
            "status": "started",
            "webhook_id": webhook.id,
            "call_id": record.id,
        }
        if resolved_session_id is not None:
            result["session_id"] = resolved_session_id
        return result

    async def _execute_webhook_flow(
        self,
        flow_id: int,
        input_data: dict,
        flow_name: str,
        flow_type: Optional[str],
        callback_url: Optional[str],
        webhook_name: str,
        call_record_id: int,
        session_id: Optional[int] = None,
    ) -> None:
        """后台执行 Webhook 触发的流程/智能体

        消费执行生成器直到完成，完成后发送 callback 回调。
        WebSocket 通知由各 executor service 的完成点自动处理。
        flow_type 和 flow_name 由 trigger_flow 传入，避免重复查询。
        """
        from app.config.database import AsyncSessionLocal

        output_data = None
        error_message = None
        status = "unknown"

        try:
            if flow_type == FlowType.AGENT.value:
                event_stream = self._execute_agent_via_webhook(
                    flow_id, input_data, webhook_name, session_id
                )
            else:
                from app.services.flow_executor_service import (
                    flow_executor_service,
                )

                event_stream = flow_executor_service.execute_stream(
                    flow_id, input_data=input_data
                )

            async for event in event_stream:
                event_type = event.get("type")
                event_data = event.get("data", {})

                # Flow 类型：从 flow_start 事件捕获 execution_id
                if flow_type != FlowType.AGENT.value and event_type == "flow_start":
                    execution_id = event_data.get("execution_id")
                    if execution_id:
                        async with AsyncSessionLocal() as db:
                            await self.update_call_record_ref(
                                db, call_record_id, "execution", execution_id
                            )

                if event_type == "flow_done":
                    status = event_data.get("status", "success")
                    output_data = event_data.get("output_data")
                elif event_type == "error":
                    status = "failed"
                    error_message = event_data.get("message")
        except Exception as e:
            logger.exception(f"Webhook 流程执行异常: {e}")
            status = "failed"
            error_message = str(e)

        # 更新调用记录完成状态
        await self.finish_call_record(
            call_record_id, status, output_data, error_message
        )

        # 发送 callback 回调
        if callback_url:
            callback_status = "sent"
            try:
                async with AsyncSessionLocal() as db:
                    record = await db.get(WebhookCallRecord, call_record_id)
                    ref_session_id = (
                        record.ref_id
                        if record and record.ref_type == "session"
                        else None
                    )
                    ref_execution_id = (
                        record.ref_id
                        if record and record.ref_type == "execution"
                        else None
                    )

                async with httpx.AsyncClient(timeout=30) as client:
                    await client.post(
                        callback_url,
                        json={
                            "webhook_name": webhook_name,
                            "flow_id": flow_id,
                            "call_id": call_record_id,
                            "session_id": ref_session_id,
                            "execution_id": ref_execution_id,
                            "status": status,
                            "output_data": output_data,
                            "error": error_message,
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
            except Exception as e:
                logger.warning(f"Webhook callback 失败: {callback_url}, {e}")
                callback_status = "failed"

            await self.update_callback_status(call_record_id, callback_status)

    async def _execute_agent_via_webhook(
        self,
        flow_id: int,
        input_data: dict,
        webhook_name: str,
        session_id: int,
    ):
        """通过 Webhook 触发 Agent 执行

        session_id 由 trigger_flow 同步创建或校验后传入，此处直接使用。
        参照 scheduled_task_service._execute_agent_task 的模式。
        """
        from app.services.agent_executor_service import agent_executor_service

        # 提取 message 和额外参数
        message = (input_data or {}).get("message", "")
        params = {k: v for k, v in (input_data or {}).items() if k != "message"}
        if not params:
            params = None

        async for event in agent_executor_service.chat_stream(
            session_id, message, params
        ):
            yield event


webhook_service = WebhookService()

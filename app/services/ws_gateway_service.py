"""
WebSocket 网关服务

提供 网关的 CRUD、WS 流式执行和会话管理功能。
触发执行通过 WebSocket 实时流式返回事件（node_content/tool_call/flow_done 等）。
每次触发创建 WsGatewayCallRecord 用于记录调用历史。
"""

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ws_gateway import WsGatewayConfig
from app.models.ws_gateway_call_record import WsGatewayCallRecord
from app.models.flow import FlowType
from app.models.flow_execution import ExecutionStatus
from app.schemas.ws_gateway_schema import (
    WsGatewayConfigCreate,
    WsGatewayConfigUpdate,
    WsGatewayConfigCondition,
)
from app.services.base_service import BaseService

logger = logging.getLogger(__name__)


class WsGatewayService(
    BaseService[WsGatewayConfig, WsGatewayConfigCreate, WsGatewayConfigUpdate]
):
    """WebSocket 网关服务"""

    def __init__(self):
        super().__init__(WsGatewayConfig)

    def _apply_filters(self, query, count_query, condition: WsGatewayConfigCondition):
        """应用查询过滤条件"""
        query, count_query = super()._apply_filters(query, count_query, condition)
        if condition:
            if hasattr(condition, "name") and condition.name:
                query, count_query = self._apply_like_filter(
                    query, count_query, "name", condition.name
                )
            if hasattr(condition, "flow_id") and condition.flow_id:
                query = query.where(WsGatewayConfig.flow_id == condition.flow_id)
                count_query = count_query.where(
                    WsGatewayConfig.flow_id == condition.flow_id
                )
            if hasattr(condition, "is_enabled") and condition.is_enabled is not None:
                query = query.where(WsGatewayConfig.is_enabled == condition.is_enabled)
                count_query = count_query.where(
                    WsGatewayConfig.is_enabled == condition.is_enabled
                )
        return query, count_query

    async def create(
        self, db: AsyncSession, obj_in: WsGatewayConfigCreate
    ) -> WsGatewayConfig:
        """创建网关（自动生成 token）"""
        import uuid

        model = obj_in.to_model(WsGatewayConfig)
        model.token = uuid.uuid4().hex
        model.call_count = 0
        db.add(model)
        await db.commit()
        await db.refresh(model)
        return model

    async def get_by_token(
        self, db: AsyncSession, token: str
    ) -> Optional[WsGatewayConfig]:
        """通过 token 查找 网关配置"""
        stmt = select(WsGatewayConfig).where(
            WsGatewayConfig.token == token,
            WsGatewayConfig.is_delete == 0,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    # ---- 网关调用记录管理 ----

    async def create_call_record(
        self, db: AsyncSession, gateway: WsGatewayConfig, input_data: dict
    ) -> WsGatewayCallRecord:
        """创建调用记录（触发时写入，status=执行中），同时更新调用计数"""
        gateway.call_count = (gateway.call_count or 0) + 1
        gateway.last_call_time = datetime.now()

        record = WsGatewayCallRecord(
            gateway_id=gateway.id,
            flow_id=gateway.flow_id,
            input_data=input_data,
            status=ExecutionStatus.RUNNING.value,
            callback_status="skipped",
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
        record = await db.get(WsGatewayCallRecord, record_id)
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
            record = await db.get(WsGatewayCallRecord, record_id)
            if record:
                record.status = self._to_execution_status(status)
                if output_data is not None:
                    record.output_data = output_data
                if error_message is not None:
                    record.error_message = error_message
                record.finished_at = datetime.now()
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

    # ---- 网关调用记录查询（供免认证 API 使用） ----

    async def get_call_records_by_token(
        self, db: AsyncSession, token: str, page: int = 1, page_size: int = 20
    ) -> tuple[list[WsGatewayCallRecord], int]:
        """通过 token 查询调用记录列表（分页）"""
        gateway = await self.get_by_token(db, token)
        if not gateway:
            return [], 0

        stmt = (
            select(WsGatewayCallRecord)
            .where(
                WsGatewayCallRecord.gateway_id == gateway.id,
                WsGatewayCallRecord.is_delete == 0,
            )
            .order_by(desc(WsGatewayCallRecord.started_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        count_stmt = select(func.count(WsGatewayCallRecord.id)).where(
            WsGatewayCallRecord.gateway_id == gateway.id,
            WsGatewayCallRecord.is_delete == 0,
        )
        result = await db.execute(stmt)
        records = list(result.scalars().all())
        count_result = await db.execute(count_stmt)
        total = count_result.scalar() or 0
        return records, total

    async def get_call_record_by_token(
        self, db: AsyncSession, token: str, call_id: int
    ) -> Optional[WsGatewayCallRecord]:
        """通过 token + call_id 获取单条调用记录"""
        gateway = await self.get_by_token(db, token)
        if not gateway:
            return None
        stmt = select(WsGatewayCallRecord).where(
            WsGatewayCallRecord.id == call_id,
            WsGatewayCallRecord.gateway_id == gateway.id,
            WsGatewayCallRecord.is_delete == 0,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_call_record_messages(
        self,
        db: AsyncSession,
        record: WsGatewayCallRecord,
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

    # ---- 网关会话/消息管理（供免认证 API 使用，仅 Agent 类型流程） ----

    async def get_sessions_by_token(
        self, db: AsyncSession, token: str, page: int = 1, page_size: int = 20
    ) -> tuple[list, int]:
        """通过 token 查询该网关创建的会话列表（分页）

        仅返回由该网关触发创建的会话（gateway_id 匹配），用户聊天会话不在此列。
        """
        from app.models.agent_session import AgentSession

        gateway = await self.get_by_token(db, token)
        if not gateway:
            return [], 0

        conditions = [
            AgentSession.gateway_id == gateway.id,
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
    ) -> tuple[Optional[WsGatewayConfig], Optional[object]]:
        """通过 token + session_id 获取会话，并校验会话由该网关创建

        Returns:
            (gateway, session)：gateway 不存在返回 (None, None)；
            会话不存在或非该网关创建返回 (gateway, None)。
        """
        from app.services.agent_executor_service import agent_executor_service

        gateway = await self.get_by_token(db, token)
        if not gateway:
            return None, None
        session = await agent_executor_service._get_session(db, session_id)
        if not session or session.gateway_id != gateway.id:
            return gateway, None
        return gateway, session

    async def delete_session_by_token(
        self, db: AsyncSession, token: str, session_id: int
    ) -> tuple[bool, str]:
        """通过 token 删除会话（含消息和 checkpoint）

        Returns:
            (success, msg)：(False, "网关不存在") / (False, "会话不存在或不属于该网关") / (True, "")
        """
        from app.services.agent_executor_service import agent_executor_service

        gateway, session = await self.get_session_by_token(db, token, session_id)
        if not gateway:
            return False, "网关不存在"
        if not session:
            return False, "会话不存在或不属于该网关"
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

        gateway, session = await self.get_session_by_token(db, token, session_id)
        if not gateway:
            return False, "网关不存在"
        if not session:
            return False, "会话不存在或不属于该网关"
        result = await agent_executor_service.delete_messages_from(
            db, session_id, message_id
        )
        if result is None:
            return False, "消息不存在"
        return True, ""

    # ---- WS 流式执行 ----

    async def stream_execute(
        self,
        gateway_id: int,
        input_data: dict,
        session_id: Optional[int] = None,
    ):
        """WebSocket 流式执行 — async generator，yield 所有事件供 WS 端点转发

        流程：
        1. 解析 flow_type（agent / flow）
        2. Agent 类型：校验或新建 session
        3. 创建 WsGatewayCallRecord
        4. yield call_started
        5. 遍历 chat_stream / execute_stream，转发每个事件
        6. 拦截 flow_start / flow_done / error 更新调用记录
        7. finish_call_record
        """
        from app.config.database import AsyncSessionLocal
        from app.services.flow_service import flow_service

        async with AsyncSessionLocal() as db:
            gateway = await self.get_by_id(db, gateway_id)
            if not gateway:
                yield {"type": "error", "data": {"message": "网关不存在"}}
                return

            flow = await flow_service.get_by_id(
                db, gateway.flow_id, raise_not_found=False
            )
            flow_type = flow.flow_type if flow else None
            flow_id = gateway.flow_id

            # Agent 类型：校验或新建 session
            resolved_session_id = session_id
            if flow_type == FlowType.AGENT.value:
                if session_id is not None:
                    from app.models.agent_session import AgentSession

                    stmt = select(AgentSession).where(
                        AgentSession.id == session_id,
                        AgentSession.flow_id == flow_id,
                        AgentSession.is_delete == 0,
                    )
                    result = await db.execute(stmt)
                    if not result.scalar_one_or_none():
                        yield {
                            "type": "error",
                            "data": {
                                "message": f"会话 {session_id} 不存在或不属于该 Agent"
                            },
                        }
                        return
                else:
                    from app.services.agent_executor_service import (
                        agent_executor_service,
                    )

                    session = await agent_executor_service.create_session(
                        db, flow_id, gateway_id=gateway.id
                    )
                    session.title = f"[WS] {gateway.name}"
                    resolved_session_id = session.id

            record = await self.create_call_record(db, gateway, input_data)
            record_id = record.id

            if resolved_session_id is not None:
                await self.update_call_record_ref(
                    db, record_id, "session", resolved_session_id
                )

        # 通知执行开始
        yield {
            "type": "call_started",
            "data": {"call_id": record_id, "session_id": resolved_session_id},
        }

        # 执行并转发事件
        output_data = None
        error_message = None
        status = "unknown"

        try:
            if flow_type == FlowType.AGENT.value:
                message = (input_data or {}).get("message", "")
                params = {k: v for k, v in (input_data or {}).items() if k != "message"}
                if not params:
                    params = None

                from app.services.agent_executor_service import (
                    agent_executor_service,
                )

                event_stream = agent_executor_service.chat_stream(
                    resolved_session_id, message, params
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

                # Flow 类型：从 flow_start 捕获 execution_id
                if flow_type != FlowType.AGENT.value and event_type == "flow_start":
                    execution_id = event_data.get("execution_id")
                    if execution_id:
                        async with AsyncSessionLocal() as db:
                            await self.update_call_record_ref(
                                db, record_id, "execution", execution_id
                            )

                if event_type == "flow_done":
                    status = event_data.get("status", "success")
                    output_data = event_data.get("output_data")
                elif event_type == "error":
                    status = "failed"
                    error_message = event_data.get("message")

                yield event

        except Exception as e:
            logger.exception(f"WS 流程执行异常: {e}")
            status = "failed"
            error_message = str(e)
            yield {"type": "error", "data": {"message": str(e)}}

        await self.finish_call_record(record_id, status, output_data, error_message)

    async def create_session_for_ws(
        self, token: str, title: Optional[str] = None
    ) -> tuple[int, str]:
        """为 WS 连接创建新的 Agent 会话

        Returns:
            (session_id, session_title)
        """
        from app.config.database import AsyncSessionLocal
        from app.services.agent_executor_service import agent_executor_service

        async with AsyncSessionLocal() as db:
            gateway = await self.get_by_token(db, token)
            if not gateway:
                raise ValueError("网关不存在")

            session = await agent_executor_service.create_session(
                db, gateway.flow_id, gateway_id=gateway.id
            )
            session.title = title or f"[WS] {gateway.name}"
            await db.commit()
            return session.id, session.title


ws_gateway_service = WsGatewayService()

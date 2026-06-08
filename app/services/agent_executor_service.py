"""
Agent执行服务模块

与FlowExecutorService的区别：
1. 不创建Execution/NodeExecution记录
2. 使用session_id作为thread_id
3. 消息保存到AgentMessage表
4. 简化的流程验证（不需要input_schema/output_schema）
"""

import logging
from typing import Optional, AsyncGenerator, Dict, Any, List

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langgraph.types import Command
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flow import Flow, FlowType
from app.models.agent_session import AgentSession
from app.models.agent_message import AgentMessage
from app.agent_flow.flow_context import FlowContext
from app.agent_flow.flow_event import FlowEventFactory
from app.constants.node_types import NODE_TYPE_LABELS
from app.services.base_executor_service import BaseExecutorService
from app.services.agent_conversation_service import agent_conversation_service
from app.services.file_service import file_service
from app.services.interrupt_service import interrupt_service
from app.config.settings import settings
from app.utils.message_utils import extract_text_content, extract_token_usage

logger = logging.getLogger(__name__)


class AgentExecutorService(BaseExecutorService):
    """
    Agent执行服务

    复用流程编排能力，但不创建执行记录
    对话历史保存到AgentMessage表
    """

    def __init__(self):
        super().__init__()
        self._compressing_sessions: set[int] = set()
        self._running_sessions: set[int] = set()
        self._pending_save_sessions: set[int] = set()

    def _validate_agent_flow(self, flow: Flow) -> None:
        """
        验证Agent流程结构

        检查：
        1. 流程类型为 agent
        2. 节点类型白名单
        3. start/end/llm 唯一性
        4. 工具边连接规则（工具节点→llm via tools handle）

        Args:
            flow: Flow对象

        Raises:
            ValueError: 流程结构不合法时抛出
        """
        if flow.flow_type != FlowType.AGENT.value:
            raise ValueError(f"流程类型不是agent: {flow.flow_type}")

        from app.models.flow_node import (
            AGENT_ALLOWED_NODE_TYPES,
            AGENT_TOOL_NODE_TYPES,
            NodeType,
        )

        if not flow.nodes:
            raise ValueError("智能体流程没有节点")

        type_labels = NODE_TYPE_LABELS
        for node in flow.nodes:
            if node.node_type not in AGENT_ALLOWED_NODE_TYPES:
                label = type_labels.get(node.node_type, node.node_type)
                raise ValueError(f"智能体不支持「{label}」类型的节点")

        start_nodes = [n for n in flow.nodes if n.node_type == NodeType.START.value]
        end_nodes = [n for n in flow.nodes if n.node_type == NodeType.END.value]
        llm_nodes = [n for n in flow.nodes if n.node_type == NodeType.LLM.value]

        if not start_nodes:
            raise ValueError("智能体缺少开始节点")
        if len(start_nodes) > 1:
            raise ValueError("智能体只能有一个开始节点")
        if not end_nodes:
            raise ValueError("智能体缺少结束节点")
        if len(end_nodes) > 1:
            raise ValueError("智能体只能有一个结束节点")
        if not llm_nodes:
            raise ValueError("智能体缺少大模型调用节点")
        if len(llm_nodes) > 1:
            raise ValueError("智能体只能有一个大模型调用节点")

        if not flow.edges:
            raise ValueError("智能体流程没有连接")

        node_map = {n.node_key: n.node_type for n in flow.nodes}
        for edge in flow.edges:
            source_type = node_map.get(edge.source_node_key, "")
            target_type = node_map.get(edge.target_node_key, "")
            if edge.source_handle == "tools":
                if source_type not in AGENT_TOOL_NODE_TYPES:
                    label = type_labels.get(source_type, source_type)
                    raise ValueError(
                        f"智能体模式下「{label}」节点不能作为工具连接到 LLM"
                    )
                if target_type != NodeType.LLM.value:
                    raise ValueError("智能体模式下工具节点只能连接到大模型调用节点")

    async def _get_session(
        self, db: AsyncSession, session_id: int
    ) -> Optional[AgentSession]:
        """获取会话"""
        query = select(AgentSession).where(
            AgentSession.id == session_id, AgentSession.is_delete == 0
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_sessions(
        self, db: AsyncSession, flow_id: int, page: int = 1, page_size: int = 20
    ) -> tuple[List[AgentSession], int]:
        """
        获取会话列表（分页）

        Args:
            db: 数据库会话
            flow_id: Agent Flow ID
            page: 页码
            page_size: 每页数量

        Returns:
            tuple[List[AgentSession], int]: 会话列表和总数
        """
        # 计算总数
        count_query = (
            select(func.count())
            .select_from(AgentSession)
            .where(AgentSession.flow_id == flow_id, AgentSession.is_delete == 0)
        )
        count_result = await db.execute(count_query)
        total = count_result.scalar() or 0

        # 分页查询
        offset = (page - 1) * page_size
        query = (
            select(AgentSession)
            .where(AgentSession.flow_id == flow_id, AgentSession.is_delete == 0)
            .order_by(AgentSession.id.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await db.execute(query)
        sessions = list(result.scalars().all())

        return sessions, total

    async def create_session(self, db: AsyncSession, flow_id: int) -> AgentSession:
        """创建新会话（公开方法）"""
        return await self._create_session(db, flow_id)

    async def _cleanup_thread_checkpoint(self, session_id: int) -> None:
        """清理会话对应的 LangGraph checkpoint，确保下次执行从 DB 重建历史"""
        try:
            thread_id = f"agent_{session_id}"
            await self._checkpointer.adelete_thread(thread_id)
        except Exception as e:
            logger.warning(f"清理checkpoint失败: {e}")

    async def delete_session(self, db: AsyncSession, session_id: int) -> bool:
        session = await self._get_session(db, session_id)
        if not session:
            return False
        session.is_delete = 1
        await db.execute(
            update(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .values(is_delete=1)
        )
        await db.commit()

        await self._cleanup_thread_checkpoint(session_id)

        return True

    async def delete_messages_from(
        self, db: AsyncSession, session_id: int, message_id: int
    ) -> Optional[str]:
        """
        删除指定消息及之后的所有消息，返回被删除的用户消息内容

        Args:
            db: 数据库会话
            session_id: 会话ID
            message_id: 起始消息ID（该消息及之后的所有消息都会被删除）

        Returns:
            被删除的第一条用户消息内容，没有用户消息返回空字符串，消息不存在返回None
        """
        session = await self._get_session(db, session_id)
        if not session:
            return None

        query = (
            select(AgentMessage)
            .where(
                AgentMessage.session_id == session_id,
                AgentMessage.is_delete == 0,
                AgentMessage.id >= message_id,
            )
            .order_by(AgentMessage.id.asc())
        )
        result = await db.execute(query)
        messages_to_delete = list(result.scalars().all())

        if not messages_to_delete:
            return None

        user_message_content = ""
        for msg in messages_to_delete:
            if msg.role == "human" or msg.role == "user":
                user_message_content = msg.content
                break

        message_ids = [msg.id for msg in messages_to_delete]
        await db.execute(
            update(AgentMessage)
            .where(AgentMessage.id.in_(message_ids))
            .values(is_delete=1)
        )
        await db.commit()

        await self._cleanup_thread_checkpoint(session_id)

        return user_message_content

    async def get_messages(
        self,
        db: AsyncSession,
        session_id: int,
        limit: int = 0,
        before_id: Optional[int] = None,
    ) -> tuple[List[AgentMessage], int]:
        """
        获取会话消息历史（公开方法），支持分页

        Args:
            db: 数据库会话
            session_id: 会话ID
            limit: 最大消息数，0 表示不限制
            before_id: 分页游标，返回此 ID 之前的消息（不含 before_id 本身）

        Returns:
            tuple[List[AgentMessage], int]: 消息列表和总数
        """
        messages = await self._get_messages(db, session_id, limit, before_id)
        total = await self._get_messages_count(db, session_id)
        return messages, total

    async def _create_session(self, db: AsyncSession, flow_id: int) -> AgentSession:
        """创建新会话"""
        session = AgentSession(flow_id=flow_id, title="新对话", status=1)
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session

    async def _get_messages(
        self,
        db: AsyncSession,
        session_id: int,
        limit: int = 0,
        before_id: Optional[int] = None,
    ) -> List[AgentMessage]:
        """获取会话消息历史，支持 before_id 分页游标"""
        query = select(AgentMessage).where(
            AgentMessage.session_id == session_id, AgentMessage.is_delete == 0
        )
        if before_id is not None:
            query = query.where(AgentMessage.id < before_id)
        query = query.order_by(AgentMessage.id.desc())
        if limit > 0:
            query = query.limit(limit)
        result = await db.execute(query)
        items = list(result.scalars().all())
        items.reverse()
        return items

    async def _get_messages_count(self, db: AsyncSession, session_id: int) -> int:
        """获取会话消息总数"""
        query = select(func.count(AgentMessage.id)).where(
            AgentMessage.session_id == session_id, AgentMessage.is_delete == 0
        )
        result = await db.execute(query)
        return result.scalar() or 0

    async def _save_message(
        self,
        db: AsyncSession,
        session_id: int,
        role: str,
        content: str,
        thinking: Optional[str] = None,
        tool_calls: Optional[dict] = None,
        tool_call_id: Optional[str] = None,
    ) -> AgentMessage:
        """保存单条消息"""
        query = select(func.max(AgentMessage.sequence)).where(
            AgentMessage.session_id == session_id, AgentMessage.is_delete == 0
        )
        result = await db.execute(query)
        max_seq = result.scalar()
        next_seq = (max_seq or -1) + 1

        kwargs: dict = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "sequence": next_seq,
        }
        if thinking is not None:
            kwargs["thinking"] = thinking
        if tool_calls is not None:
            kwargs["tool_calls"] = tool_calls
        if tool_call_id is not None:
            kwargs["tool_call_id"] = tool_call_id

        message = AgentMessage(**kwargs)
        db.add(message)
        await db.commit()
        await db.refresh(message)
        return message

    async def _load_history_to_langchain(
        self, db: AsyncSession, session_id: int, limit: int = 50
    ) -> List:
        """
        加载对话历史并转换为LangChain消息格式

        Args:
            db: 数据库会话
            session_id: 会话ID
            limit: 最大消息数

        Returns:
            LangChain消息列表
        """
        messages = await self._get_messages(db, session_id, limit)
        result = []

        for msg in messages:
            if msg.role == "system":
                result.append(SystemMessage(content=msg.content))
            elif msg.role == "user":
                result.append(HumanMessage(content=msg.content))
            elif msg.role == "assistant":
                if msg.tool_calls:
                    result.append(
                        AIMessage(content=msg.content, tool_calls=msg.tool_calls)
                    )
                else:
                    result.append(AIMessage(content=msg.content))
            elif msg.role == "tool":
                from langchain_core.messages import ToolMessage

                result.append(
                    ToolMessage(
                        content=msg.content, tool_call_id=msg.tool_call_id or ""
                    )
                )

        return result

    async def _resolve_input_params(
        self,
        db: AsyncSession,
        params: dict,
        input_schema: dict,
        input_data: dict,
    ) -> list[dict]:
        """
        解析 input_schema 参数，将文件信息解析为文件路径，同时收集附件元信息

        支持两种输入格式：
        1. 完整文件信息数组 [{id, file_path, ...}] — 直接使用，补充绝对路径
        2. 纯 ID 数组 [1, 2, ...] — 查询 DB 补全文件信息

        直接在传入的 input_data 上添加字段，不会覆盖已有数据。

        Returns:
            pending_files: 附件元信息列表
        """
        pending_files: list[dict] = []
        fields = input_schema.get("fields", [])

        for field in fields:
            field_name = field.get("name")
            field_type = field.get("type")
            if not field_name or field_name not in params:
                continue
            if field_type == "file_list":
                raw_value = params[field_name]
                if not isinstance(raw_value, list) or len(raw_value) == 0:
                    continue

                # 判断是完整文件信息还是纯 ID 列表
                if isinstance(raw_value[0], dict):
                    resolved = []
                    for item in raw_value:
                        fid = item.get("id")
                        if not fid:
                            continue
                        file_path = item.get("file_path", "")
                        if file_path and not file_path.startswith("/"):
                            abs_path = settings.get_absolute_path(file_path)
                            item["file_path"] = str(abs_path) if abs_path else file_path
                        resolved.append(item)
                        pending_files.append(
                            {
                                "id": fid,
                                "original_name": item.get("original_name", ""),
                                "mime_type": item.get("mime_type", ""),
                            }
                        )
                    if resolved:
                        input_data[field_name] = resolved
                else:
                    # 纯 ID 列表，查 DB 补全（向后兼容）
                    resolved = []
                    for fid in raw_value:
                        try:
                            (
                                path,
                                original_name,
                                mime_type,
                            ) = await file_service.get_download_path(db, fid)
                            resolved.append(
                                {
                                    "id": fid,
                                    "file_path": str(path),
                                    "mime_type": mime_type,
                                    "original_name": original_name,
                                }
                            )
                            pending_files.append(
                                {
                                    "id": fid,
                                    "original_name": original_name,
                                    "mime_type": mime_type,
                                }
                            )
                        except FileNotFoundError:
                            pass
                    if resolved and len(resolved) > 0:
                        input_data[field_name] = resolved
            else:
                input_data[field_name] = params[field_name]

        return pending_files

    async def chat_stream(
        self,
        session_id: int,
        user_message: str,
        params: dict | None = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        执行Agent对话（流式）

        Args:
            session_id: 会话ID
            user_message: 用户消息
            params: 额外参数

        Yields:
            SSE事件字典
        """
        from app.config.database import AsyncSessionLocal

        db: AsyncSession | None = None
        try:
            db = AsyncSessionLocal()
        except Exception as e:
            yield FlowEventFactory.error(f"数据库连接失败: {e}")
            return
        # 获取会话
        session = await self._get_session(db, session_id)
        if not session:
            yield FlowEventFactory.error("会话不存在")
            return

        if session_id in self._compressing_sessions:
            yield FlowEventFactory.error("正在压缩上下文，请稍后再发送消息")
            return

        if session_id in self._running_sessions:
            yield FlowEventFactory.error("会话正在执行中，请稍后再发送消息")
            return
        self._running_sessions.add(session_id)

        # 获取Flow
        flow = await self._get_flow_with_details(db, session.flow_id, FlowType.AGENT)
        if not flow:
            yield FlowEventFactory.error("Agent不存在")
            return

        # 验证流程
        try:
            self._validate_agent_flow(flow)
        except ValueError as e:
            yield FlowEventFactory.error(str(e))
            logger.exception(e)
            return

        # 检查是否首次对话（用于自动生成标题）
        existing_messages = await self._get_messages(db, session_id, 1)
        is_first_message = len(existing_messages) == 0

        # 构建图
        graph = self._build_graph(
            flow, 0, agent_conversation_service, session_id=session_id
        )
        config = {
            "configurable": {"thread_id": f"agent_{session_id}", "scope_type": "agent"}
        }

        # 初始化上下文
        input_data: dict = {}

        # 统一通过 input_schema 解析所有参数（包括 message）
        pending_files = []
        if flow.input_schema:
            all_params = dict(params) if params else {}
            all_params["message"] = user_message
            pending_files = await self._resolve_input_params(
                db, all_params, flow.input_schema, input_data
            )
        else:
            input_data["message"] = user_message

        if pending_files:
            agent_conversation_service.set_pending_files(pending_files)

        context = FlowContext(
            flow_id=flow.id,
            execution_id=0,  # Agent不使用execution_id
            input_data=input_data,
        )
        context.start()

        # 清除可能残留的中断状态，发送流程开始事件
        interrupt_service.clear_agent_interrupted(session_id)
        yield FlowEventFactory.flow_start(flow_id=flow.id, execution_id=session_id)

        # 收集LLM响应内容
        llm_content = ""
        llm_thinking = ""

        try:
            # 执行图
            async for event in graph.astream(
                input=context.state.model_dump(),
                config=config,
                stream_mode=["updates", "custom"],
            ):
                if not isinstance(event, tuple) or len(event) != 2:
                    continue

                stream_mode_type, event_data = event

                # 检查用户是否主动中断
                if interrupt_service.is_agent_interrupted(session_id):
                    logger.info(f"Agent会话被中断: session_id={session_id}")
                    break

                # 处理 custom 事件（流式输出）
                if stream_mode_type == "custom":
                    if hasattr(event_data, "to_dict"):
                        event_dict = event_data.to_dict()
                        yield event_dict

                        # 收集LLM内容
                        if event_dict.get("type") == "node_content":
                            llm_content += event_dict.get("data", {}).get("content", "")
                        elif event_dict.get("type") == "node_thinking":
                            llm_thinking += event_dict.get("data", {}).get(
                                "content", ""
                            )
                    continue

                # 处理 updates 事件（节点更新）
                if stream_mode_type != "updates":
                    continue

                for node_key, result in event_data.items():
                    # 处理 interrupt
                    if node_key == "__interrupt__":
                        interrupt_data = result[0].value if result else {}
                        yield await self._handle_interrupt(
                            db, session_id, interrupt_data
                        )
                        return

                    node = next((n for n in flow.nodes if n.node_key == node_key), None)
                    if not node:
                        continue

                    # 更新上下文状态
                    if isinstance(result, dict):
                        if "variables" in result:
                            for k, v in result.get("variables", {}).items():
                                context.state.set_variable(k, v)
                        if "output_data" in result:
                            context.state.output_data.update(
                                result.get("output_data", {})
                            )
                        if "errors" in result:
                            context.state.errors.extend(result.get("errors", []))

                    # 发送节点事件
                    yield FlowEventFactory.node_start(
                        node_key=node.node_key,
                        node_type=node.node_type,
                        node_name=node.node_name,
                    )

                    node_error = None
                    for err in context.state.errors:
                        if err.get("node_key") == node.node_key:
                            node_error = err.get("message")
                            break

                    yield FlowEventFactory.node_done(
                        node_key=node.node_key,
                        node_type=node.node_type,
                        error=node_error,
                    )

            # 首次对话时自动生成标题
            if is_first_message and llm_content:
                title = user_message[:50]
                if len(user_message) > 50:
                    title += "..."
                await self._update_session_title(db, session_id, title)

            # 发送完成事件
            is_interrupted = interrupt_service.is_agent_interrupted(session_id)
            yield FlowEventFactory.flow_done(
                execution_id=session_id,
                output_data={"content": llm_content},
                status="cancelled" if is_interrupted else "success",
            )
            interrupt_service.clear_agent_interrupted(session_id)

        except Exception as e:
            error_msg = str(e)
            logger.exception(f"Agent执行失败: {e}")
            yield FlowEventFactory.error(f"执行失败: {error_msg}")
            interrupt_service.clear_agent_interrupted(session_id)
        finally:
            self._running_sessions.discard(session_id)
            self._pending_save_sessions.discard(session_id)
            from app.services.tool_approval_service import tool_approval_service

            tool_approval_service.cancel(session_id)
            try:
                await self._cleanup_thread_checkpoint(session_id)
            except Exception as cleanup_err:
                logger.warning(f"清理checkpoint失败: {cleanup_err}")
            if db is not None:
                try:
                    await db.close()
                except Exception:
                    pass

    async def _update_session_title(
        self, db: AsyncSession, session_id: int, title: str
    ) -> None:
        """更新会话标题"""
        query = select(AgentSession).where(AgentSession.id == session_id)
        result = await db.execute(query)
        session = result.scalar_one_or_none()
        if session:
            session.title = title
            await db.commit()

    async def _handle_interrupt(
        self, db: AsyncSession, session_id: int, interrupt_data: dict
    ) -> Dict[str, Any]:
        """
        处理interrupt事件

        Args:
            db: 数据库会话
            session_id: 会话ID
            interrupt_data: interrupt数据

        Returns:
            SSE事件字典
        """
        interrupt_type = interrupt_data.get("type", "unknown")

        if interrupt_type == "human_input_required":
            pass

        return FlowEventFactory.waiting_human(
            execution_id=0,
            node_key=interrupt_data.get("node_key", ""),
            question=interrupt_data.get("question", "需要您的输入"),
            context=interrupt_data.get("context"),
            wait_data={
                "type": interrupt_type,
                "question": interrupt_data.get("question", "需要您的输入"),
                "context": interrupt_data.get("context"),
                "tool_call_id": interrupt_data.get("tool_call_id"),
            },
        )

    async def resume_stream(
        self, session_id: int, human_input: str
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        恢复Agent执行（流式）

        Args:
            session_id: 会话ID
            human_input: 人工输入

        Yields:
            SSE事件字典
        """
        from app.config.database import AsyncSessionLocal

        db: AsyncSession | None = None
        try:
            db = AsyncSessionLocal()
        except Exception as e:
            yield FlowEventFactory.error(f"数据库连接失败: {e}")
            return
        # 获取会话
        session = await self._get_session(db, session_id)
        if not session:
            yield FlowEventFactory.error("会话不存在")
            return

        # 获取Flow
        flow = await self._get_flow_with_details(db, session.flow_id, FlowType.AGENT)
        if not flow:
            yield FlowEventFactory.error("Agent不存在")
            return

        # 构建图
        graph = self._build_graph(
            flow, 0, agent_conversation_service, session_id=session_id
        )
        config = {
            "configurable": {
                "thread_id": f"agent_{session_id}",
                "scope_type": "agent",
                "_human_resume_input": human_input,
            }
        }

        # 收集LLM响应内容
        llm_content = ""
        llm_thinking = ""

        try:
            # 使用Command(resume=...)恢复执行
            async for event in graph.astream(
                input=Command(resume=human_input),
                config=config,
                stream_mode=["updates", "custom"],
            ):
                if not isinstance(event, tuple) or len(event) != 2:
                    continue

                stream_mode_type, event_data = event

                # 检查用户是否主动中断
                if interrupt_service.is_agent_interrupted(session_id):
                    logger.info(f"Agent会话恢复被中断: session_id={session_id}")
                    break

                # 处理 custom 事件
                if stream_mode_type == "custom":
                    if hasattr(event_data, "to_dict"):
                        event_dict = event_data.to_dict()
                        yield event_dict
                        if event_dict.get("type") == "node_content":
                            llm_content += event_dict.get("data", {}).get("content", "")
                        elif event_dict.get("type") == "node_thinking":
                            llm_thinking += event_dict.get("data", {}).get(
                                "content", ""
                            )
                    continue

                # 处理 updates 事件
                if stream_mode_type != "updates":
                    continue

                for node_key, result in event_data.items():
                    # 处理再次interrupt
                    if node_key == "__interrupt__":
                        interrupt_data = result[0].value if result else {}
                        yield await self._handle_interrupt(
                            db, session_id, interrupt_data
                        )
                        return

                    node = next((n for n in flow.nodes if n.node_key == node_key), None)
                    if not node:
                        continue

                    yield FlowEventFactory.node_start(
                        node_key=node.node_key,
                        node_type=node.node_type,
                        node_name=node.node_name,
                    )

                    node_error = None
                    if isinstance(result, dict) and "errors" in result:
                        for err in result.get("errors", []):
                            if err.get("node_key") == node_key:
                                node_error = err.get("message")
                                break

                    yield FlowEventFactory.node_done(
                        node_key=node.node_key,
                        node_type=node.node_type,
                        error=node_error,
                    )

            # 发送完成事件
            is_interrupted = interrupt_service.is_agent_interrupted(session_id)
            yield FlowEventFactory.flow_done(
                execution_id=session_id,
                output_data={"content": llm_content},
                status="cancelled" if is_interrupted else "success",
            )
            interrupt_service.clear_agent_interrupted(session_id)

        except Exception as e:
            error_msg = str(e)
            logger.exception(f"Agent恢复执行失败: {e}")
            yield FlowEventFactory.error(f"执行失败: {error_msg}")
            interrupt_service.clear_agent_interrupted(session_id)
        finally:
            self._pending_save_sessions.discard(session_id)
            from app.services.tool_approval_service import tool_approval_service

            tool_approval_service.cancel(session_id)
            try:
                await self._cleanup_thread_checkpoint(session_id)
            except Exception as cleanup_err:
                logger.warning(f"清理checkpoint失败: {cleanup_err}")
            if db is not None:
                try:
                    await db.close()
                except Exception:
                    pass

    COMPRESS_MARKER = "[上下文压缩]"

    async def is_compressing_session(self, db: AsyncSession, session_id: int) -> bool:
        """检查指定会话是否正在压缩上下文"""
        return session_id in self._compressing_sessions

    def is_pending_save(self, session_id: int) -> bool:
        """检查指定会话是否正在等待中断后的消息保存完成"""
        return session_id in self._pending_save_sessions

    async def _run_compress_background(self, session_id: int) -> None:
        """后台压缩任务，独立于 HTTP 请求生命周期，前端通过轮询 /compressing 检测完成"""
        from app.config.database import AsyncSessionLocal

        try:
            async with AsyncSessionLocal() as db:
                await self.compress_session(db, session_id)
        except Exception as e:
            logger.error(f"后台压缩会话上下文失败: session_id={session_id}, error={e}")

    async def compress_session(
        self, db: AsyncSession, session_id: int
    ) -> dict[str, Any]:
        """
        压缩会话上下文（手动/自动统一入口）

        用 Agent 自身的 LLM 将全部对话总结为摘要。
        同时清理 LangGraph checkpoint，确保下次执行从压缩后的历史重建。

        Returns:
            {"summary": str|None, "kept_count": int, "removed_count": int, "token_usage": dict}
        """
        self._compressing_sessions.add(session_id)
        try:
            return await self._do_compress(db, session_id)
        finally:
            self._compressing_sessions.discard(session_id)

    async def _do_compress(self, db: AsyncSession, session_id: int) -> dict[str, Any]:
        """压缩会话上下文的实际执行逻辑"""
        session = await self._get_session(db, session_id)
        if not session:
            return {"summary": None, "kept_count": 0, "removed_count": 0}

        all_messages = await self._get_messages(db, session_id, limit=9999)
        total = len(all_messages)

        if total == 0:
            return {"summary": None, "kept_count": 0, "removed_count": 0}

        # 获取 flow 及 LLM 配置
        flow = await self._get_flow_with_details(db, session.flow_id, FlowType.AGENT)
        if not flow:
            return {"summary": None, "kept_count": total, "removed_count": 0}

        llm_config = self._extract_llm_config(flow)
        if not llm_config.get("model"):
            return {"summary": None, "kept_count": total, "removed_count": 0}

        removed_count = total

        # 构建压缩文本（跳过 tool 消息）
        conversation_lines = []
        for msg in all_messages:
            role_label = {"human": "用户", "user": "用户", "assistant": "AI"}.get(
                msg.role, msg.role
            )
            if msg.role == "tool":
                continue
            content = msg.content or ""
            conversation_lines.append(f"{role_label}: {content}")
        conversation_text = "\n".join(conversation_lines)

        # 调用 LLM 总结
        from langchain_core.messages import HumanMessage, SystemMessage

        try:
            provider_name = llm_config.get("provider", "deepseek")
            from app.agent_flow.ai_provider import create_provider

            provider = create_provider(
                provider_name,
                llm_config.get("api_key", ""),
                llm_config.get("base_url", ""),
            )
            llm = provider.create_chat_model(
                model=llm_config.get("model", ""),
                temperature=0.3,
                max_tokens=2048,
            )
            summary_prompt = (
                "你是一个对话上下文压缩助手。请将以下对话历史压缩为结构化摘要。\n\n"
                "要求：\n"
                "1. 按主题分段，用「## 主题名」标记每个段落\n"
                "2. 用简洁的要点列表（bullets）而非段落\n"
                "3. 保留所有关键决策、结论和重要上下文\n"
                "4. 保留用户明确的偏好和约束条件\n"
                "5. 精确保留文件路径、函数名、配置项、变量名等技术标识符\n"
                "6. 省略工具调用的中间过程和重复内容\n"
                "7. 保留未完成的任务和待跟进的事项\n"
                "8. 移除已过时或不再相关的信息，只保留对后续对话仍有价值的内容\n"
                "9. 保持简洁紧凑，用最少的文字传达最多的有效信息\n"
                "10. 直接输出摘要，不要添加前缀、标题或元说明（如「以下是摘要」等）\n"
                "11. 使用与对话相同的语言输出\n"
                "12. 不要回答对话本身的内容，只做压缩"
            )
            response = await llm.ainvoke(
                [
                    SystemMessage(content=summary_prompt),
                    HumanMessage(content=conversation_text),
                ]
            )
            summary = (
                extract_text_content(response.content).strip()
                if response.content
                else "（无摘要内容）"
            )
            # 提取压缩 LLM 调用的 token 用量
            compress_usage = extract_token_usage(response)
        except Exception as e:
            logger.exception(f"压缩上下文时LLM调用失败: {e}")
            return {
                "summary": None,
                "kept_count": total,
                "removed_count": 0,
                "error": f"LLM调用失败: {e}",
            }

        # 软删除全部消息
        all_ids = [msg.id for msg in all_messages]
        await db.execute(
            update(AgentMessage).where(AgentMessage.id.in_(all_ids)).values(is_delete=1)
        )

        # 插入摘要用户消息
        user_content = (
            f"{self.COMPRESS_MARKER} 共 {removed_count} 条历史对话已压缩为以下摘要："
        )
        summary_user = AgentMessage(
            session_id=session_id,
            role="user",
            content=user_content,
            sequence=0,
        )
        db.add(summary_user)
        await db.flush()

        # 插入摘要助手消息（保存压缩 LLM 的 token 用量）
        summary_kwargs: dict[str, Any] = {
            "session_id": session_id,
            "role": "assistant",
            "content": summary,
            "sequence": 1,
        }
        if compress_usage.get("prompt_tokens") is not None:
            summary_kwargs["prompt_tokens"] = compress_usage["prompt_tokens"]
        if compress_usage.get("completion_tokens") is not None:
            summary_kwargs["completion_tokens"] = compress_usage["completion_tokens"]
        if compress_usage.get("total_tokens") is not None:
            summary_kwargs["total_tokens"] = compress_usage["total_tokens"]
        summary_assistant = AgentMessage(**summary_kwargs)
        db.add(summary_assistant)
        await db.flush()

        await db.commit()

        await self._cleanup_thread_checkpoint(session_id)

        return {
            "summary": summary,
            "kept_count": 0,
            "removed_count": removed_count,
            "token_usage": compress_usage,
        }

    @staticmethod
    def _extract_llm_config(flow: Flow) -> dict[str, Any]:
        """从 Flow 节点中提取 LLM 配置（model/api_key/base_url/context_length）"""
        from app.models.flow_node import NodeType

        for node in flow.nodes:
            if node.node_type == NodeType.LLM.value and node.base_config:
                config = node.base_config if isinstance(node.base_config, dict) else {}
                return {
                    "provider": config.get("provider", "deepseek"),
                    "model": config.get("model", ""),
                    "api_key": config.get("api_key", ""),
                    "base_url": config.get("base_url", ""),
                    "context_length": config.get("context_length", 0) or 0,
                }
        return {}


# 单例
agent_executor_service = AgentExecutorService()

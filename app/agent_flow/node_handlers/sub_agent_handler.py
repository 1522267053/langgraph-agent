"""
子Agent节点处理器

将已发布的Agent作为工具提供给父Agent调用。
阻塞模式执行，执行期间通过 writer 发送心跳事件保持 SSE 连接。
"""

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from langchain_core.tools import StructuredTool
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter
from pydantic import BaseModel, Field

from app.config.build_utils import get_temp_dir
from app.config.database import AsyncSessionLocal
from app.models.flow import FlowStatus
from app.models.flow_node import FlowNode
from app.agent_flow.flow_context import FlowState
from app.agent_flow.node_handlers.base_handler import BaseNodeHandler, BaseNodeConfig
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.flow_event import NodeStartEvent, NodeDoneEvent
from app.services.flow_service import flow_service

if TYPE_CHECKING:
    from app.agent_flow.tool_resolver import LlmToolConfig

logger = logging.getLogger(__name__)

MAX_TOOL_OUTPUT_LINES = 500
MAX_TOOL_OUTPUT_BYTES = 10240
MAX_FILE_READ_LINES = 100
HEARTBEAT_INTERVAL = 20


class SubAgentNodeConfig(BaseNodeConfig):
    """子Agent节点配置"""

    agent_id: int = Field(..., description="引用的已发布Agent ID")


class AskSubAgentInput(BaseModel):
    """ask 工具输入参数"""

    task: str = Field(..., description="要委派给子Agent执行的任务描述")


class ReadAgentFileInput(BaseModel):
    """read_agent_file 工具输入参数"""

    file_path: str = Field(..., description="要读取的文件路径")
    offset: int = Field(default=1, description="起始行号（从1开始）")
    limit: int = Field(default=100, description="读取行数，最多100行")


def _truncate_output(text: str) -> dict:
    """截断过长的输出，超限时写入临时文件"""
    if not text:
        return {"text": "", "truncated": False, "saved_to": None, "total_lines": 0}

    lines = text.splitlines()
    total_lines = len(lines)
    text_bytes = len(text.encode("utf-8"))

    if total_lines <= MAX_TOOL_OUTPUT_LINES and text_bytes <= MAX_TOOL_OUTPUT_BYTES:
        return {
            "text": text,
            "truncated": False,
            "saved_to": None,
            "total_lines": total_lines,
        }

    temp_dir = get_temp_dir()
    temp_filename = f"sub_agent_output_{uuid.uuid4().hex[:8]}.log"
    temp_path = temp_dir / temp_filename
    temp_path.write_text(text, encoding="utf-8")

    head_count = MAX_TOOL_OUTPUT_LINES // 2
    head_lines = lines[:head_count]
    preview = "\n".join(head_lines)
    if (
        text_bytes > MAX_TOOL_OUTPUT_BYTES
        and len(preview.encode("utf-8")) > MAX_TOOL_OUTPUT_BYTES
    ):
        byte_head = preview.encode("utf-8")[: MAX_TOOL_OUTPUT_BYTES // 2]
        preview = byte_head.decode("utf-8", errors="ignore")

    preview += (
        f"\n\n[输出已截断，共 {total_lines} 行。"
        f"完整内容已保存到: {temp_path}，可用 read_agent_file 读取]"
    )

    return {
        "text": preview,
        "truncated": True,
        "saved_to": str(temp_path),
        "total_lines": total_lines,
    }


def _sanitize_tool_name(name: str) -> str:
    """将Agent名称转换为合法的工具名前缀"""
    sanitized = re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fff]", "_", name)
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    return sanitized[:30] if sanitized else "agent"


@NodeHandlerRegistry.register("sub_agent")
class SubAgentNodeHandler(BaseNodeHandler):
    """子Agent节点处理器

    将已发布的Agent作为工具提供给父Agent调用。
    阻塞模式执行子Agent，期间通过 writer 心跳保持 SSE 活跃。
    取消由父Agent的中断机制传播（CancelledError）。
    """

    ConfigClass = SubAgentNodeConfig

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        if writer:
            writer(
                NodeStartEvent(
                    node_key=node.node_key,
                    node_type=node.node_type,
                    node_name=node.node_name,
                    input_data={},
                )
            )
        if writer:
            writer(NodeDoneEvent(node_key=node.node_key, output={}))
        return state

    @classmethod
    def allow_multiple_tool_connections(cls) -> bool:
        return True

    async def get_tool(self, node: FlowNode) -> list[StructuredTool]:
        """返回子Agent相关的工具列表"""
        node_config = node.base_config or {}
        agent_id = node_config.get("agent_id")
        if not agent_id:
            return []

        async with AsyncSessionLocal() as db:
            agent = await flow_service.get_by_id(db, agent_id, raise_not_found=False)

        if not agent or agent.status != FlowStatus.PUBLISHED.value:
            return []

        agent_name = agent.name or f"agent_{agent_id}"
        tool_prefix = _sanitize_tool_name(agent_name)
        description = agent.description or ""

        tools: list[StructuredTool] = []

        # ---- ask 工具（阻塞 + 心跳） ----
        ask_desc = (
            f"将任务委派给子Agent「{agent_name}」执行。\n\n"
            f"{description}\n\n"
            f"调用后阻塞等待子Agent完成并返回结果。"
        )

        handler_ref = self
        _agent_id = agent_id
        _agent_name = agent_name

        async def ask_agent(task: str) -> str:
            from app.services.agent_executor_service import agent_executor_service

            session_id = 0
            try:
                # 创建子Agent session（标题标记来源）
                async with AsyncSessionLocal() as db:
                    session = await agent_executor_service.create_session(db, _agent_id)
                    session_id = session.id
                    title = f"[子Agent调用] {task[:40]}"
                    if len(task) > 40:
                        title += "..."
                    session.title = title
                    await db.commit()

                # 在后台执行子Agent
                agent_task = asyncio.create_task(_run_sub_agent(session_id, task))

                # 阻塞等待，定期发心跳保持 SSE
                while True:
                    try:
                        result = await asyncio.wait_for(
                            asyncio.shield(agent_task), timeout=HEARTBEAT_INTERVAL
                        )
                        return result
                    except asyncio.TimeoutError:
                        # 通过 writer 发心跳事件保持 SSE 连接
                        if hasattr(handler_ref, "_writer") and handler_ref._writer:
                            try:
                                handler_ref._writer(
                                    {
                                        "type": "sub_agent_progress",
                                        "data": {"agent": _agent_name},
                                    }
                                )
                            except Exception:
                                pass

            except asyncio.CancelledError:
                # 父Agent被取消 → 中断子Agent
                if session_id:
                    from app.services.interrupt_service import interrupt_service

                    interrupt_service.set_agent_interrupted(session_id)
                raise

            except Exception as e:
                logger.error(f"子Agent执行失败: {e}", exc_info=True)
                return json.dumps(
                    {"error": f"子Agent执行失败: {str(e)}"}, ensure_ascii=False
                )

        tools.append(
            StructuredTool(
                name=f"ask_{tool_prefix}",
                description=ask_desc,
                func=None,
                coroutine=ask_agent,
                args_schema=AskSubAgentInput,
            )
        )

        # ---- read_agent_file 通用工具 ----
        read_desc = (
            "读取子Agent被截断的输出文件。"
            "当子Agent返回结果提示「可用 read_agent_file 读取」时，使用此工具读取完整内容。"
            "每次最多读取100行。"
        )

        async def read_agent_file(
            file_path: str, offset: int = 1, limit: int = 100
        ) -> str:
            try:
                path = Path(file_path).resolve()

                if ".." in Path(file_path).parts:
                    return json.dumps(
                        {"error": "文件路径不允许包含 '..'"}, ensure_ascii=False
                    )

                temp_dir = get_temp_dir().resolve()
                if not str(path).startswith(str(temp_dir)):
                    return json.dumps(
                        {"error": "只能读取子Agent输出目录中的文件"}, ensure_ascii=False
                    )

                if not path.exists():
                    return json.dumps(
                        {"error": f"文件不存在: {file_path}"}, ensure_ascii=False
                    )

                content = path.read_text(encoding="utf-8")
                all_lines = content.splitlines()
                total_lines = len(all_lines)

                actual_offset = max(1, offset)
                actual_limit = min(limit, MAX_FILE_READ_LINES)

                start_idx = actual_offset - 1
                end_idx = min(start_idx + actual_limit, total_lines)

                selected_lines = all_lines[start_idx:end_idx]
                numbered = [
                    f"{i + actual_offset}: {line}"
                    for i, line in enumerate(selected_lines)
                ]
                has_more = end_idx < total_lines

                return json.dumps(
                    {
                        "file_path": file_path,
                        "total_lines": total_lines,
                        "offset": actual_offset,
                        "limit": actual_limit,
                        "content": "\n".join(numbered),
                        "has_more": has_more,
                    },
                    ensure_ascii=False,
                )
            except Exception as e:
                return json.dumps(
                    {"error": f"读取文件失败: {str(e)}"}, ensure_ascii=False
                )

        tools.append(
            StructuredTool(
                name="read_agent_file",
                description=read_desc,
                func=None,
                coroutine=read_agent_file,
                args_schema=ReadAgentFileInput,
            )
        )

        return tools

    @classmethod
    def get_tool_config(cls, node: FlowNode, config: "LlmToolConfig") -> bool:
        """将子Agent节点配置添加到工具配置"""
        node_config = node.base_config or {}
        agent_id = node_config.get("agent_id")
        if agent_id:
            config.sub_agent_node_keys.append(node.node_key)
            config.sub_agent_configs[node.node_key] = {
                "agent_id": agent_id,
                "name": node.node_name or "子Agent",
            }
            return True
        return False


async def _run_sub_agent(session_id: int, task: str) -> str:
    """执行子Agent并返回结果"""
    from app.services.agent_executor_service import agent_executor_service

    content = ""
    async for event in agent_executor_service.chat_stream(session_id, task, {}):
        event_type = event.get("type", "")
        if event_type == "flow_done":
            output = event.get("data", {}).get("output_data", {})
            content = output.get("content", "") if isinstance(output, dict) else ""
        elif event_type == "error":
            error_msg = event.get("data", {}).get("message", "未知错误")
            return json.dumps(
                {"error": f"子Agent执行出错: {error_msg}"}, ensure_ascii=False
            )

    truncated = _truncate_output(content)
    return truncated["text"]

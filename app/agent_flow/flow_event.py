"""
流程执行事件类

用于流程执行过程中的SSE事件封装，支持类型安全的事件创建
"""

from enum import Enum
from typing import Any, Optional, Union
from langchain_core.messages import ToolCall
from pydantic import BaseModel, Field


class FlowEventType(str, Enum):
    """流程事件类型枚举"""

    FLOW_START = "flow_start"
    RESUME_START = "resume_start"
    FLOW_DONE = "flow_done"
    NODE_START = "node_start"
    NODE_DONE = "node_done"
    NODE_THINKING = "node_thinking"
    NODE_CONTENT = "node_content"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    WAITING_HUMAN = "waiting_human"
    TOKEN_USAGE = "token_usage"
    TOOL_CALL_LIMIT = "tool_call_limit"
    TODO_UPDATE = "todo_update"
    TOOL_APPROVAL_REQUIRED = "tool_approval_required"
    SUB_AGENT_PROGRESS = "sub_agent_progress"
    ERROR = "error"
    LLM_RETRY = "llm_retry"
    CONTEXT_COMPRESSING = "context_compressing"
    FLOW_PREVIEW = "flow_preview"
    KNOWLEDGE_CITATIONS = "knowledge_citations"
    QUESTION_REQUEST = "question_request"
    FILE_CHANGED = "file_changed"


class FlowEvent(BaseModel):
    """流程事件基类"""

    def to_dict(self) -> dict:
        """转换为SSE输出格式"""
        event_type = self._get_event_type()
        return {"type": event_type.value, "data": self.model_dump(exclude_none=True)}

    def _get_event_type(self) -> FlowEventType:
        """获取事件类型（子类必须重写）"""
        raise NotImplementedError


class FlowStartEvent(FlowEvent):
    """流程开始事件"""

    flow_id: int = Field(..., description="流程ID")
    execution_id: int = Field(..., description="执行记录ID")

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.FLOW_START


class ResumeStartEvent(FlowEvent):
    """恢复执行开始事件"""

    execution_id: int = Field(..., description="执行记录ID")

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.RESUME_START


class FlowDoneEvent(FlowEvent):
    """流程完成事件"""

    execution_id: int = Field(..., description="执行记录ID")
    status: str = Field(..., description="执行状态: success/failed")
    output_data: dict = Field(default_factory=dict, description="输出数据")

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.FLOW_DONE


class NodeStartEvent(FlowEvent):
    """节点开始事件"""

    node_key: str = Field(..., description="节点Key")
    node_type: str = Field(..., description="节点类型")
    node_name: Optional[str] = Field(default=None, description="节点名称")
    input_data: Optional[dict] = Field(default=None, description="输入数据")

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.NODE_START


class NodeDoneEvent(FlowEvent):
    """节点完成事件"""

    node_key: str = Field(..., description="节点Key")
    node_type: str = Field(..., description="节点类型")
    output_data: Optional[dict] = Field(default=None, description="输出数据")
    error: Optional[str] = Field(default=None, description="错误信息")

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.NODE_DONE


class NodeThinkingEvent(FlowEvent):
    """节点思考事件（LLM推理过程）"""

    node_key: str = Field(..., description="节点Key")
    content: str = Field(..., description="思考内容")

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.NODE_THINKING


class NodeContentEvent(FlowEvent):
    """节点内容事件"""

    node_key: str = Field(..., description="节点Key")
    content: str = Field(..., description="内容")

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.NODE_CONTENT


class ToolCallStartEvent(FlowEvent):
    """工具调用开始事件"""

    node_key: str = Field(..., description="节点Key")
    tool_name: str = Field(..., description="工具名称")
    tool_args: dict = Field(default_factory=dict, description="工具参数")
    tool_call_id: Optional[str] = Field(
        default=None, description="工具调用ID（用于前端精确匹配同名并行调用）"
    )

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.TOOL_CALL_START


class ToolCallEndEvent(FlowEvent):
    """工具调用结束事件"""

    node_key: str = Field(..., description="节点Key")
    tool_name: str = Field(..., description="工具名称")
    status: str = Field(default="success", description="执行状态：success / error")
    result: Optional[Any] = Field(default=None, description="工具返回结果")
    tool_call_id: Optional[str] = Field(
        default=None, description="工具调用ID（用于前端精确匹配同名并行调用）"
    )

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.TOOL_CALL_END


class KnowledgeCitationsEvent(FlowEvent):
    """LLM回答中经过校验的知识库引用"""

    node_key: str = Field(..., description="节点Key")
    citations: list[dict] = Field(default_factory=list, description="知识库引用列表")

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.KNOWLEDGE_CITATIONS


class ToolApprovalEvent(FlowEvent):
    """工具确认事件（SSE 流内等待前端确认）"""

    node_key: str = Field(..., description="节点Key")
    approval_id: str = Field(
        ..., description="审批唯一 ID（前端提交时回传，精确路由到指定审批批次）"
    )
    tool_calls: list[ToolCall] = Field(
        default_factory=list, description="待确认的工具调用列表"
    )
    approval_needed: list[str] = Field(
        default_factory=list, description="需要确认的工具名列表"
    )
    expires_in: Optional[int] = Field(
        default=None, description="确认剩余秒数（后端权威；断线重连回放时由服务端重算）"
    )
    total: Optional[int] = Field(
        default=None,
        description=(
            "本轮 LLM 响应中需审批的工具总数（仅首事件填，后续同值）"
            "，供前端展示 N/M 进度指示器；与 rejection 语义 b 配套"
        ),
    )

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.TOOL_APPROVAL_REQUIRED


class SubAgentToolApprovalEvent(ToolApprovalEvent):
    """子Agent工具审批转发事件（通过父Agent的SSE流转发到前端）"""

    is_sub_agent: bool = Field(default=True, description="是否来自子Agent")
    sub_agent_id: int = Field(default=0, description="子Agent ID")
    sub_session_id: int = Field(default=0, description="子Agent会话ID")
    sub_agent_name: str = Field(default="", description="子Agent名称")


class SubAgentProgressEvent(FlowEvent):
    """子Agent执行进度事件（实时转发子Agent最后一条消息内容）"""

    node_key: str = Field(..., description="父流程中子Agent节点的Key")
    sub_agent_id: int = Field(default=0, description="子Agent ID")
    sub_session_id: int = Field(default=0, description="子Agent会话ID")
    sub_agent_name: str = Field(default="", description="子Agent名称")
    content: str = Field(
        default="", description="子Agent当前消息的累计快照（尾部截断）"
    )
    status: str = Field(default="running", description="执行状态: running/done/error")

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.SUB_AGENT_PROGRESS


class WaitingHumanEvent(FlowEvent):
    """等待人工输入事件"""

    execution_id: int = Field(..., description="执行记录ID")
    node_key: str = Field(..., description="节点Key")
    question: str = Field(..., description="问题")
    context: Optional[str] = Field(default=None, description="上下文")
    wait_data: Optional[dict] = Field(default=None, description="等待数据")

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.WAITING_HUMAN


class TokenUsageEvent(FlowEvent):
    """Token用量事件（每次LLM调用后推送）"""

    node_key: str = Field(..., description="节点Key")
    prompt_tokens: int = Field(default=0, description="输入token数")
    completion_tokens: int = Field(default=0, description="输出token数")
    total_tokens: int = Field(default=0, description="总token数")
    model: str = Field(default="", description="模型名称")
    provider: str = Field(default="", description="供应商标识")
    cache_read_tokens: int = Field(default=0, description="缓存读取token数")
    cache_write_tokens: int = Field(default=0, description="缓存写入token数")
    reasoning_tokens: int = Field(default=0, description="推理token数")

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.TOKEN_USAGE


class ToolCallLimitEvent(FlowEvent):
    """工具调用超过最大迭代次数事件"""

    node_key: str = Field(..., description="节点Key")
    max_iterations: int = Field(..., description="最大工具调用迭代次数")

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.TOOL_CALL_LIMIT


class TodoUpdateEvent(FlowEvent):
    """任务计划更新事件"""

    todos: list = Field(default_factory=list, description="任务计划列表")

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.TODO_UPDATE


class ErrorEvent(FlowEvent):
    """错误事件"""

    message: str = Field(..., description="错误信息")
    execution_id: Optional[int] = Field(default=None, description="执行记录ID")
    node_key: Optional[str] = Field(default=None, description="节点Key")

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.ERROR


class LlmRetryEvent(FlowEvent):
    """LLM重试事件"""

    message: str = Field(..., description="重试原因")
    retry_count: int = Field(..., description="当前重试次数")
    max_retries: int = Field(..., description="最大重试次数")
    wait_seconds: float = Field(..., description="等待秒数")
    node_key: Optional[str] = Field(default=None, description="节点Key")

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.LLM_RETRY


class ContextCompressingEvent(FlowEvent):
    """上下文压缩状态事件"""

    status: str = Field(..., description="压缩状态: compressing/done/failed")
    removed_count: int = Field(default=0, description="压缩的消息数量")

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.CONTEXT_COMPRESSING


class FlowPreviewEvent(FlowEvent):
    """流程预览事件（工具执行后检测到流程变更时发送，前端渲染迷你画布）"""

    flow_id: int = Field(..., description="流程ID")
    flow_name: str = Field(default="", description="流程名称")
    action: str = Field(
        default="",
        description="变更类型: create/nodes_changed/edges_changed/config_changed/delete",
    )
    nodes: list[dict] = Field(
        default_factory=list, description="节点列表（FlowNodeBase schema）"
    )
    edges: list[dict] = Field(
        default_factory=list, description="边列表（FlowEdgeBase schema）"
    )

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.FLOW_PREVIEW


class QuestionRequestEvent(FlowEvent):
    """问题反问事件（SSE 流内等待用户选择选项）"""

    node_key: str = Field(..., description="问题节点 Key")
    question_id: str = Field(..., description="问题唯一 ID（前端提交时回传）")
    question: str = Field(..., description="问题正文")
    header: Optional[str] = Field(default=None, description="短标题（弹窗表头）")
    options: list[dict] = Field(
        default_factory=list, description="选项列表 [{label, description?, preview?}]"
    )
    multiple: bool = Field(default=False, description="是否多选")
    expires_in: Optional[int] = Field(
        default=None, description="回答剩余秒数（后端权威；断线重连回放时由服务端重算）"
    )

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.QUESTION_REQUEST


class FileChangedEvent(FlowEvent):
    """文件变更事件（侧栏实时刷新）"""

    change: dict = Field(..., description="AgentFileChange 字典（前端可序列化）")

    def _get_event_type(self) -> FlowEventType:
        return FlowEventType.FILE_CHANGED


AnyFlowEvent = Union[
    FlowStartEvent,
    ResumeStartEvent,
    FlowDoneEvent,
    NodeStartEvent,
    NodeDoneEvent,
    NodeThinkingEvent,
    NodeContentEvent,
    ToolCallStartEvent,
    ToolCallEndEvent,
    KnowledgeCitationsEvent,
    TokenUsageEvent,
    ToolCallLimitEvent,
    TodoUpdateEvent,
    ToolApprovalEvent,
    SubAgentProgressEvent,
    WaitingHumanEvent,
    ErrorEvent,
    LlmRetryEvent,
    ContextCompressingEvent,
    FlowPreviewEvent,
    QuestionRequestEvent,
    FileChangedEvent,
]


class FlowEventFactory:
    """
    流程事件工厂类

    提供静态方法创建各类事件，简化事件创建逻辑
    """

    @staticmethod
    def flow_start(flow_id: int, execution_id: int) -> dict:
        """创建流程开始事件"""
        return FlowStartEvent(flow_id=flow_id, execution_id=execution_id).to_dict()

    @staticmethod
    def resume_start(execution_id: int) -> dict:
        """创建恢复执行开始事件"""
        return ResumeStartEvent(execution_id=execution_id).to_dict()

    @staticmethod
    def flow_done(
        execution_id: int, output_data: dict, status: str = "success"
    ) -> dict:
        """创建流程完成事件"""
        return FlowDoneEvent(
            execution_id=execution_id, status=status, output_data=output_data or {}
        ).to_dict()

    @staticmethod
    def node_start(
        node_key: str,
        node_type: str,
        node_name: Optional[str] = None,
        input_data: Optional[dict] = None,
    ) -> dict:
        """创建节点开始事件"""
        return NodeStartEvent(
            node_key=node_key,
            node_type=node_type,
            node_name=node_name,
            input_data=input_data,
        ).to_dict()

    @staticmethod
    def node_done(
        node_key: str,
        node_type: str,
        output_data: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> dict:
        """创建节点完成事件"""
        return NodeDoneEvent(
            node_key=node_key, node_type=node_type, output_data=output_data, error=error
        ).to_dict()

    @staticmethod
    def node_thinking(node_key: str, content: str) -> dict:
        """创建节点思考事件"""
        return NodeThinkingEvent(node_key=node_key, content=content).to_dict()

    @staticmethod
    def node_content(node_key: str, content: str) -> dict:
        """创建节点内容事件"""
        return NodeContentEvent(node_key=node_key, content=content).to_dict()

    @staticmethod
    def tool_call_start(
        node_key: str,
        tool_name: str,
        tool_args: Optional[dict] = None,
        tool_call_id: Optional[str] = None,
    ) -> dict:
        """创建工具调用开始事件"""
        return ToolCallStartEvent(
            node_key=node_key,
            tool_name=tool_name,
            tool_args=tool_args or {},
            tool_call_id=tool_call_id,
        ).to_dict()

    @staticmethod
    def tool_call_end(
        node_key: str,
        tool_name: str,
        result: Optional[Any] = None,
        status: str = "success",
        tool_call_id: Optional[str] = None,
    ) -> dict:
        """创建工具调用结束事件"""
        return ToolCallEndEvent(
            node_key=node_key,
            tool_name=tool_name,
            status=status,
            result=result,
            tool_call_id=tool_call_id,
        ).to_dict()

    @staticmethod
    def token_usage(
        node_key: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> dict:
        """创建Token用量事件"""
        return TokenUsageEvent(
            node_key=node_key,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        ).to_dict()

    @staticmethod
    def waiting_human(
        execution_id: int,
        node_key: str,
        question: str,
        context: Optional[str] = None,
        wait_data: Optional[dict] = None,
    ) -> dict:
        """创建等待人工输入事件"""
        return WaitingHumanEvent(
            execution_id=execution_id,
            node_key=node_key,
            question=question,
            context=context,
            wait_data=wait_data,
        ).to_dict()

    @staticmethod
    def error(
        message: str, execution_id: Optional[int] = None, node_key: Optional[str] = None
    ) -> dict:
        """创建错误事件"""
        return ErrorEvent(
            message=message, execution_id=execution_id, node_key=node_key
        ).to_dict()

    @staticmethod
    def todo_update(todos: list) -> dict:
        """创建任务计划更新事件"""
        return TodoUpdateEvent(todos=todos).to_dict()

    @staticmethod
    def tool_approval(
        node_key: str,
        approval_id: str,
        tool_calls: list[ToolCall],
        approval_needed: list[str],
        total: int | None = None,
    ) -> dict:
        """创建工具确认事件"""
        return ToolApprovalEvent(
            node_key=node_key,
            approval_id=approval_id,
            tool_calls=tool_calls,
            approval_needed=approval_needed,
            total=total,
        ).to_dict()

    @staticmethod
    def file_changed(change: dict) -> dict:
        """创建文件变更事件（侧栏 Diff 面板实时刷新用）

        change 字段约定：
        - change_id: AgentFileChange.id（int）
        - file_path: 绝对路径
        - change_type: create/modify/delete
        - tool_name: 触发变更的工具名
        - create_time: ISO 字符串（可选）

        SSE data 为平铺结构（前端 onFileChanged 直接读 data.change_id 等）
        """
        # 平铺字段以便前端直接读 data.change_id / data.file_path 等
        data = {
            "change_id": change.get("change_id"),
            "file_path": change.get("file_path"),
            "change_type": change.get("change_type"),
            "tool_name": change.get("tool_name"),
            "create_time": change.get("create_time"),
        }
        return {"type": "file_changed", "data": data}

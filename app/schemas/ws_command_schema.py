"""
WS 触发端点指令 Schema

ws_trigger_api 中各客户端指令的 data 参数封装。
除 execute 外的指令对未知字段宽容忽略（extra="ignore"），
execute 需要透传额外字段作为流程输入（extra="allow"）。

注意：指令 DTO 不继承 BaseView——其声明的 id/creator_name/create_time 等字段
会被 consume 掉，导致同名流程输入变量无法透传，且日期校验会误拒非法值。
"""

from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _coerce_str(value: Any) -> Any:
    """保留原逻辑对非字符串 input 的宽容转换（str(value)）"""
    return str(value) if value is not None else value


class WsCommandView(BaseModel):
    """WS 指令基类：未知字段忽略"""

    model_config = ConfigDict(extra="ignore")


class WsToolResultCommand(WsCommandView):
    """tool_result 指令：客户端返回远程工具执行结果"""

    call_id: Optional[str] = Field(default=None, description="工具调用ID")
    result: Any = Field(default=None, description="执行结果")
    error: Any = Field(default=None, description="错误信息")


class WsRegisterToolsCommand(WsCommandView):
    """register_tools 指令：注册远程工具（仅 Agent 类型生效）"""

    tools: list[dict] = Field(default_factory=list, description="工具定义列表")


class WsExecuteCommand(WsCommandView):
    """execute 指令：触发 Agent/Flow 执行

    除 action/session_id 外的额外字段作为流程输入变量透传（extra="allow"）
    """

    model_config = ConfigDict(extra="allow")

    session_id: Optional[int] = Field(default=None, description="目标会话ID")

    def build_input_data(self, base_config: Optional[dict] = None) -> dict:
        """网关默认输入配置 + 指令额外字段合并为流程输入"""
        input_data = {**(base_config or {})}
        extras = self.model_extra or {}
        input_data.update({k: v for k, v in extras.items() if k != "action"})
        return input_data


class WsResumeCommand(WsCommandView):
    """resume 指令：恢复等待人工输入的执行

    Agent 类型传 session_id，Flow 类型传 execution_id
    """

    input: Annotated[str, BeforeValidator(_coerce_str)] = Field(
        ..., description="人工输入内容"
    )
    session_id: Optional[int] = Field(default=None, description="Agent 会话ID")
    execution_id: Optional[int] = Field(default=None, description="Flow 执行记录ID")


class WsToolApprovalCommand(WsCommandView):
    """tool_approval 指令：确认/拒绝待审批的工具调用（仅 Agent 类型）"""

    result: Literal["approved", "rejected"] = Field(..., description="审批结果")
    session_id: int = Field(..., gt=0, description="会话ID")


class WsCancelCommand(WsCommandView):
    """cancel 指令：取消正在执行的会话（Agent）或执行记录（Flow）"""

    session_id: Optional[int] = Field(default=None, description="Agent 会话ID")
    execution_id: Optional[int] = Field(default=None, description="Flow 执行记录ID")


class WsCreateSessionCommand(WsCommandView):
    """create_session 指令：创建新会话（仅 Agent 类型）"""

    title: Optional[str] = Field(default=None, description="会话标题")


class WsSwitchSessionCommand(WsCommandView):
    """switch_session 指令：切换当前会话（仅 Agent 类型）"""

    session_id: int = Field(..., gt=0, description="目标会话ID")


class WsListSessionsCommand(WsCommandView):
    """list_sessions 指令：查询会话列表（仅 Agent 类型）"""

    page: int = Field(default=1, ge=1, description="页码")
    page_size: int = Field(default=20, ge=1, description="每页数量")


class WsGetMessagesCommand(WsCommandView):
    """get_messages 指令：查询会话历史消息（仅 Agent 类型）"""

    session_id: int = Field(..., gt=0, description="会话ID")
    before_id: Optional[int] = Field(default=None, ge=1, description="游标ID")
    limit: int = Field(default=20, ge=1, description="返回数量")


class WsDeleteSessionCommand(WsCommandView):
    """delete_session 指令：删除会话（仅 Agent 类型）"""

    session_id: int = Field(..., gt=0, description="会话ID")


class WsDeleteMessageCommand(WsCommandView):
    """delete_message 指令：删除会话消息（仅 Agent 类型）"""

    session_id: int = Field(..., gt=0, description="会话ID")
    message_id: int = Field(..., gt=0, description="消息ID")

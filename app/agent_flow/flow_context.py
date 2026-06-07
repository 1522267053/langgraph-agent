"""
执行上下文和状态管理
"""

from typing import Any, Optional, Annotated
from datetime import datetime
from pydantic import BaseModel, Field
import operator


class FlowState(BaseModel):
    """LangGraph 流程状态"""

    input_data: dict = Field(default_factory=dict, description="输入数据")
    output_data: Annotated[dict, operator.or_] = Field(
        default_factory=dict, description="输出数据"
    )
    current_node: Annotated[Optional[str], lambda left, right: right] = Field(
        default=None, description="当前节点key"
    )
    variables: Annotated[dict, operator.or_] = Field(
        default_factory=dict, description="流程变量"
    )
    errors: Annotated[list, operator.add] = Field(
        default_factory=list, description="错误列表"
    )
    iteration_count: Annotated[int, lambda left, right: max(left, right)] = Field(
        default=0, description="回跳迭代次数（用于条件边循环保护）"
    )
    max_iterations: Annotated[int, lambda left, right: max(left, right)] = Field(
        default=100, description="最大允许回跳次数"
    )
    visited_nodes: Annotated[list[str], operator.add] = Field(
        default_factory=list, description="已访问节点列表（用于判断回跳）"
    )
    conversation_messages: Annotated[dict, operator.or_] = Field(
        default_factory=dict, description="各节点的对话历史，通过 checkpoint 自动恢复"
    )
    is_interrupted: bool = Field(default=False, description="是否被中断")

    def get_variable(self, key: str, default: Any = None) -> Any:
        """获取变量"""
        return self.variables.get(key, default)

    def set_variable(self, key: str, value: Any) -> None:
        """设置变量"""
        self.variables[key] = value

    def set_node_variable(self, node_key: str, var_name: str, value: Any) -> None:
        """
        设置节点命名空间变量

        Args:
            node_key: 节点唯一标识
            var_name: 变量名
            value: 变量值
        """
        self.variables[f"nodes.{node_key}.{var_name}"] = value

    def get_node_variable(
        self, node_key: str, var_name: str, default: Any = None
    ) -> Any:
        """
        获取节点命名空间变量

        Args:
            node_key: 节点唯一标识
            var_name: 变量名
            default: 默认值

        Returns:
            变量值
        """
        return self.variables.get(f"nodes.{node_key}.{var_name}", default)

    def add_error(self, node_key: str, error_message: str) -> None:
        """添加错误"""
        self.errors.append(
            {
                "node_key": node_key,
                "message": error_message,
                "timestamp": datetime.now().isoformat(),
            }
        )

    def get_conversation_messages(self, node_key: str) -> list:
        """获取指定节点的对话历史"""
        return self.conversation_messages.get(node_key, [])

    def set_conversation_messages(self, node_key: str, messages: list) -> None:
        """设置指定节点的对话历史"""
        self.conversation_messages[node_key] = messages

    def set_interrupted(self) -> None:
        """设置中断标志"""
        self.is_interrupted = True


class FlowContext:
    """
    流程执行上下文
    管理流程执行过程中的状态和资源
    """

    def __init__(
        self, flow_id: int, execution_id: int, input_data: Optional[dict] = None
    ):
        self.flow_id = flow_id
        self.execution_id = execution_id
        self.state = FlowState(input_data=input_data or {})
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        self._node_start_times: dict[str, datetime] = {}

    def start(self) -> None:
        """开始执行"""
        self.start_time = datetime.now()

    def end(self) -> None:
        """结束执行"""
        self.end_time = datetime.now()

    def start_node(self, node_key: str) -> None:
        """开始执行节点"""
        self._node_start_times[node_key] = datetime.now()
        self.state.current_node = node_key

    def end_node(self, node_key: str) -> float:
        """结束执行节点，返回执行时长（秒）"""
        if node_key in self._node_start_times:
            duration = (
                datetime.now() - self._node_start_times[node_key]
            ).total_seconds()
            del self._node_start_times[node_key]
            return duration
        return 0.0

    def get_elapsed_time(self) -> float:
        """获取已执行时长（秒）"""
        if self.start_time:
            if self.end_time:
                return (self.end_time - self.start_time).total_seconds()
            return (datetime.now() - self.start_time).total_seconds()
        return 0.0

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "flow_id": self.flow_id,
            "execution_id": self.execution_id,
            "state": self.state.model_dump(),
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "elapsed_time": self.get_elapsed_time(),
        }

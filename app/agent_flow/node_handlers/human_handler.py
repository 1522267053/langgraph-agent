"""
Human节点处理器

Human节点支持两种角色：
1. 工具提供者：连接到LLM节点，提供 request_human_help 工具
2. 流程检查点：在执行流程中，暂停等待人工输入

当Human节点在执行流程中时，作为检查点暂停流程
使用 LangGraph interrupt 机制实现人工交互
"""

from typing import Optional, TYPE_CHECKING
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.types import interrupt, StreamWriter

from app.models.flow_node import FlowNode
from app.agent_flow.flow_context import FlowState
from app.agent_flow.node_handlers.base_handler import (
    BaseNodeHandler,
    BaseNodeConfig,
    NodeVariable,
)
from app.agent_flow.handler_registry import NodeHandlerRegistry

if TYPE_CHECKING:
    from app.agent_flow.tool_resolver import LlmToolConfig


class HumanNodeConfig(BaseNodeConfig):
    output_variables: list[NodeVariable] = [
        NodeVariable(name="feedback"),
    ]
    review_prompt: Optional[str] = None
    prompt: Optional[str] = None
    assist_prompt: Optional[str] = None


@tool
def request_human_help(question: str, context: Optional[str] = None) -> str:
    """
    当AI需要人类帮助时调用此工具。
    会暂停流程执行，等待人工输入后继续。

    Args:
        question: 需要人类回答的问题
        context: 相关上下文信息（可选）


    Returns:
        等待人工输入的提示信息
    """
    return f"[等待人工输入] 问题: {question}"


@NodeHandlerRegistry.register("human")
class HumanNodeHandler(BaseNodeHandler):
    """
    Human节点处理器

    在流程中创建人工检查点，使用 LangGraph interrupt 暂停执行等待人工输入
    """

    ConfigClass = HumanNodeConfig

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        """
        执行Human节点

        使用 LangGraph interrupt 机制作为流程检查点，暂停执行等待人工输入
        用户输入后，LangGraph 自动恢复执行
        """
        self._get_config(node)

        input_data = self.__class__.get_input_content(
            node, state, self._resolver, node.base_config or {}
        )

        prompt = (
            (input_data.get("prompt") or "请提供您的输入：")
            if input_data
            else "请提供您的输入："
        )

        context_parts = []
        if input_data:
            for key, value in input_data.items():
                if key != "prompt":
                    context_parts.append(f"**{key}**:\n{value}")

        context = "\n\n".join(context_parts) if context_parts else None

        output_names = self._get_output_var_names(node, ["feedback"])
        feedback_name = output_names[0] if output_names else "feedback"

        # 使用 LangGraph interrupt 机制中断执行
        human_input = interrupt(
            {
                "type": "human_checkpoint",
                "node_key": node.node_key,
                "question": prompt,
                "context": context,
                "output_variable": feedback_name,
            }
        )

        # 用户恢复后，将输入保存到状态变量
        state.set_node_variable(node.node_key, feedback_name, human_input)

        return state

    @classmethod
    def get_input_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        """获取Human节点的输入内容"""
        if config is None:
            config = node.base_config or {}
        input_data = {}

        input_vars = config.get("input_variables", [])
        context = {}
        for var in input_vars:
            name = var.get("name", "")
            source = var.get("source", "")
            if name and source:
                value = resolver.resolve_safe(source, state)
                context[name] = value
                input_data[name] = value

        prompt = config.get("review_prompt") or config.get("prompt")
        if prompt:
            input_data["prompt"] = resolver.render_template(prompt, state, context)

        return input_data if input_data else None

    @classmethod
    def get_output_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        """获取Human节点的输出内容"""
        if config is None:
            config = node.base_config or {}
        output = {}

        output_vars = config.get("output_variables", [])
        if output_vars:
            for var in output_vars:
                name = (
                    var.get("name", "")
                    if isinstance(var, dict)
                    else getattr(var, "name", "")
                )
                if name:
                    value = state.get_node_variable(node.node_key, name)
                    if value is not None:
                        output[name] = value
        else:
            value = state.get_node_variable(node.node_key, "feedback")
            if value is not None:
                output["feedback"] = value

        return output if output else None

    def get_tool(self, node: FlowNode) -> Optional[BaseTool]:
        """
        返回人工协助工具

        Args:
            node: 节点对象

        Returns:
            人工协助工具
        """
        return request_human_help

    @classmethod
    def allow_multiple_tool_connections(cls) -> bool:
        """固定工具名 request_human_help，同一 LLM 只需连接一个"""
        return False

    async def get_system_prompt_hint(self, node: FlowNode) -> Optional[str]:
        """返回人类协助使用提示，追加到 LLM system_prompt"""
        cfg = self._get_config(node)
        assist_prompt = cfg.assist_prompt
        return (
            "\n\n## 人类协助\n"
            "你已连接人类回答节点，可以在需要时向人类求助。使用规则：\n"
            "- 调用 request_human_help 工具时，question 参数应清晰描述需要人类帮助的内容\n"
            "- 可选提供 context 参数，包含相关的上下文信息帮助人类理解问题\n"
            "- 流程会暂停等待人类输入，人类回复后你会收到回复内容继续执行\n"
            f"\n提示: {assist_prompt}"
        )

    @classmethod
    def get_tool_config(cls, node: FlowNode, config: "LlmToolConfig") -> bool:
        """将Human节点配置添加到工具配置"""
        config.enable_human_assist = True
        config.human_node_keys.append(node.node_key)
        node_config = node.base_config or {}
        output_vars = node_config.get("output_variables", [])
        feedback_name = "feedback"
        if output_vars and isinstance(output_vars[0], dict):
            feedback_name = output_vars[0].get("name", "feedback")
        config.human_assist_config = {
            "prompt": node_config.get(
                "assist_prompt", "如果需要帮助，请使用 request_human_help 工具"
            ),
            "output_variable": feedback_name,
        }
        return True

    @classmethod
    def get_tool_info(cls, node: FlowNode) -> list[dict]:
        return [{"name": "request_human_help", "description": "请求人工帮助"}]

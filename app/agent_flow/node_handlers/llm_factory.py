"""
LLM 实例创建和工具绑定

从 llm_tool_handler.py 抽离的 LLM 创建职责，负责：
- 通过 AI 提供商创建 LLM 实例
- 将工具绑定到 LLM（支持降级为无工具模式）
"""

from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool

from app.agent_flow.ai_provider import create_provider
from app.agent_flow.flow_context import FlowState


def create_llm(
    api_key: str,
    model: str,
    base_url: str = "",
    max_tokens: int = 8192,
    provider_name: str = "",
    temperature: float = 0.7,
    extra_body: Optional[dict] = None,
    reasoning_effort: Optional[str] = None,
) -> BaseChatModel:
    """通过 AI 提供商创建 LLM 实例

    Args:
        api_key: API 密钥
        model: 模型名称
        base_url: 自定义 API 地址（为空则使用供应商默认地址）
        max_tokens: 最大生成 token 数
        provider_name: 供应商标识
        temperature: 温度参数（0-2）
        extra_body: 附加请求参数
        reasoning_effort: 推理深度（low/medium/high）

    Returns:
        BaseChatModel 实例
    """
    provider = create_provider(provider_name, api_key, base_url)
    kwargs: dict = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "streaming": True,
        "verbose": True,
    }
    if extra_body:
        kwargs["extra_body"] = extra_body
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    return provider.create_chat_model(**kwargs)


def prepare_llm(
    node_config: dict,
    tools: list[BaseTool],
    node_key: str,
    state: FlowState,
) -> tuple[BaseChatModel, BaseChatModel, bool]:
    """创建 LLM 实例并绑定工具

    Args:
        node_config: 节点配置
        tools: 工具列表
        node_key: 节点 key（用于错误报告）
        state: 流程状态（用于记录错误）

    Returns:
        (llm, llm_with_tools, has_tools) — 原始实例、绑定工具后的实例、是否有工具可用
    """
    api_key = node_config.get("api_key")
    model = node_config.get("model", "")
    base_url = node_config.get("base_url")
    max_tokens = node_config.get("max_tokens", 8192)
    temperature = node_config.get("temperature", 0.7)
    provider_name = node_config.get("provider", "")
    extra_body = node_config.get("extra_body")
    reasoning_effort = node_config.get("reasoning_effort")

    llm = create_llm(
        api_key,
        model,
        base_url,
        max_tokens,
        provider_name,
        temperature,
        extra_body=extra_body,
        reasoning_effort=reasoning_effort,
    )

    has_tools = len(tools) > 0
    if has_tools:
        try:
            llm_with_tools = llm.bind_tools(tools)
        except Exception as bind_error:
            # 模型不支持 function calling 时降级为无工具模式
            state.add_error(
                node_key,
                f"模型不支持工具调用，请更换支持function calling的模型: {str(bind_error)}",
            )
            has_tools = False
            llm_with_tools = llm
    else:
        llm_with_tools = llm

    return llm, llm_with_tools, has_tools

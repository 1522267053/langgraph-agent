"""
意图路由节点处理器

两层级联分类：
1. 规则层（正则 + 关键字，任一命中即归类，按列表顺序短路）
2. LLM 层（输出 JSON {intent, confidence}，低于阈值走 default）

层间级联：第一层未命中才进第二层；任一层命中即结束；都未命中走 default。
路由结果通过 state.variables["_intent_route"] 暴露给 edge_router。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Optional, TYPE_CHECKING

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter
from pydantic import BaseModel, Field

from app.agent_flow.ai_provider import create_provider
from app.agent_flow.flow_context import FlowState
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.node_handlers.base_handler import (
    BaseNodeHandler,
    BaseNodeConfig,
    NodeVariable,
)
from app.config.database import AsyncSessionLocal
from app.models.flow_node import FlowNode
from app.services.global_config_service import global_config_service

if TYPE_CHECKING:
    from app.agent_flow.flow_context import FlowState  # noqa: F811
    from app.services.conversation_service import ConversationService

logger = logging.getLogger(__name__)

# ---- 默认 system prompt 模板 ----

_DEFAULT_SYSTEM_PROMPT = """你是一个意图分类助手。根据用户输入，从以下候选意图中选择最匹配的一个。

候选意图：
{intents_description}

输出要求：
- 必须输出严格的 JSON，不要包含 markdown 代码块或额外说明
- 格式：{{"intent": "<意图 key>", "confidence": <0-1 之间的浮点数>}}
- intent 必须是上述候选 key 之一；如果都不匹配，输出 {{"intent": "", "confidence": 0.0}}
- confidence 反映你对该分类的置信度（0=完全不确定，1=非常确定）
"""


class IntentRule(BaseModel):
    """单个意图的规则配置（第一层）"""

    model_config = {"extra": "ignore"}
    keywords: list[str] = Field(default=[], description="关键字列表（包含匹配）")
    regex_patterns: list[str] = Field(
        default=[], description="正则表达式列表（任一匹配即归类）"
    )


class IntentItem(BaseModel):
    """单个意图项"""

    model_config = {"extra": "ignore"}
    key: str = Field(..., description="意图 key（slug，作为 source_handle id）")
    description: str = Field(default="", description="意图描述（供 LLM 参考）")
    examples: list[str] = Field(default=[], description="few-shot 示例（供 LLM 参考）")
    rule: IntentRule = Field(default_factory=IntentRule, description="规则层配置")


class IntentRouterConfig(BaseNodeConfig):
    """意图路由节点配置"""

    # ---- 层级开关 ----
    enable_rule_layer: bool = Field(
        default=True, description="启用规则层（关键字+正则）"
    )
    enable_llm_layer: bool = Field(default=True, description="启用 LLM 层")
    case_sensitive: bool = Field(default=False, description="规则层是否大小写敏感")

    # ---- LLM 配置（留空走全局默认）----
    provider: Optional[str] = Field(default=None, description="AI 提供商")
    model: Optional[str] = Field(default=None, description="模型名（留空走全局默认）")
    api_key: Optional[str] = Field(
        default=None, description="API Key（留空走全局默认）"
    )
    base_url: Optional[str] = Field(
        default=None, description="Base URL（留空走全局默认）"
    )
    temperature: float = Field(default=0.1, description="温度（低温度更稳定）")
    max_tokens: int = Field(default=200, description="最大 token 数")
    system_prompt: str = Field(
        default="", description="追加到默认 prompt 后的自定义提示"
    )
    confidence_threshold: float = Field(
        default=0.6, description="置信度阈值，低于此值走 default"
    )

    # ---- 输入输出 ----
    input_variable: str = Field(
        default="input.question", description="待分类文本来源变量路径"
    )
    intents: list[IntentItem] = Field(default=[], description="意图列表")
    output_variables: list[NodeVariable] = [
        NodeVariable(name="intent"),
        NodeVariable(name="raw_response"),
        NodeVariable(name="metadata"),
    ]


class IntentRouterHandler(BaseNodeHandler):
    """
    意图路由节点处理器

    工作流程：
    1. 规则层（若启用）：按 intents 顺序，对每个意图的关键字和正则做 OR 匹配，
       第一个命中的即返回（短路）。
    2. LLM 层（若启用且规则层未命中）：构建候选列表 prompt，调 LLM 输出 JSON，
       校验 intent 在候选 key 中、confidence ≥ 阈值。
    3. 任一层命中即写入 _intent_route；都未命中写 "default"。
    """

    ConfigClass = IntentRouterConfig

    def __init__(
        self,
        flow: Any = None,
        db_session_factory: Optional[Callable] = None,
        execution_id: Optional[int] = None,
        conversation_service: Optional["ConversationService"] = None,
        handler_registry: Any = None,
        session_id: int = 0,
    ):
        super().__init__()
        self._flow = flow
        self._db_session_factory = db_session_factory or AsyncSessionLocal
        self._execution_id = execution_id
        self._conversation_service = conversation_service
        self._handler_registry = handler_registry
        self._session_id = session_id

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        """执行意图分类，结果写入 _intent_route 全局变量"""
        cfg = self._get_config(node)
        node_key = node.node_key

        # ---- 解析输入文本 ----
        text = self._resolve_variable(cfg.input_variable, state)
        text_str = str(text) if text is not None else ""

        # ---- 初始化 metadata ----
        metadata: dict[str, Any] = {
            "matched_layer": "none",
            "matched_intent": None,
            "input_text": text_str,
            "rule_detail": None,
            "llm_detail": None,
        }

        chosen: Optional[str] = None

        # ---- 第一层：规则匹配 ----
        if cfg.enable_rule_layer and text_str:
            chosen, rule_detail = self._match_rule_layer(
                text_str, cfg.intents, cfg.case_sensitive
            )
            metadata["rule_detail"] = rule_detail
            if chosen:
                metadata["matched_layer"] = "rule"
                metadata["matched_intent"] = chosen

        # ---- 第二层：LLM 分类（仅当未启用规则层或规则层未命中）----
        if not chosen and cfg.enable_llm_layer and text_str:
            llm_detail = await self._match_llm_layer(text_str, cfg)
            metadata["llm_detail"] = llm_detail
            if llm_detail.get("chosen"):
                chosen = llm_detail["chosen"]
                metadata["matched_layer"] = "llm"
                metadata["matched_intent"] = chosen

        # ---- 写状态 ----
        route_key = chosen or "default"
        state.set_variable("_intent_route", route_key)
        # 写入节点级别变量，供工具边按路由器过滤工具可见性
        state.set_variable(f"_intent_route_{node_key}", route_key)
        state.set_node_variable(node_key, "intent", chosen)
        state.set_node_variable(
            node_key,
            "raw_response",
            (metadata.get("llm_detail") or {}).get("raw_response", ""),
        )
        state.set_node_variable(node_key, "metadata", metadata)

        logger.info(
            "意图路由[%s] 输入=%r 命中层=%s 命中意图=%s",
            node_key,
            text_str[:50],
            metadata["matched_layer"],
            chosen,
        )
        return state

    # ---- 第一层：规则匹配 ----

    @staticmethod
    def _match_rule_layer(
        text: str, intents: list[IntentItem], case_sensitive: bool
    ) -> tuple[Optional[str], dict]:
        """关键字 + 正则 OR 匹配，按 intents 顺序短路

        Returns:
            (命中的 intent key 或 None, detail dict)
        """
        haystack = text if case_sensitive else text.lower()
        checked: list[str] = []
        regex_errors: list[dict[str, str]] = []
        matched_intent: Optional[str] = None
        matched_type: Optional[str] = None
        matched_pattern: Optional[str] = None

        for it in intents:
            has_rule = bool(it.rule.keywords or it.rule.regex_patterns)
            if not has_rule:
                continue
            checked.append(it.key)

            # 关键字包含匹配（任一命中即归类）
            for kw in it.rule.keywords:
                if not kw:
                    continue
                needle = kw if case_sensitive else kw.lower()
                if needle in haystack:
                    matched_intent, matched_type, matched_pattern = (
                        it.key,
                        "keyword",
                        kw,
                    )
                    break
            if matched_intent:
                break

            # 正则匹配（任一命中即归类，编译失败跳过）
            flags = 0 if case_sensitive else re.IGNORECASE
            for pattern in it.rule.regex_patterns:
                if not pattern:
                    continue
                try:
                    if re.search(pattern, text, flags=flags):
                        matched_intent, matched_type, matched_pattern = (
                            it.key,
                            "regex",
                            pattern,
                        )
                        break
                except re.error as e:
                    regex_errors.append({"pattern": pattern, "error": str(e)})
            if matched_intent:
                break

        detail = {
            "checked_intents": checked,
            "matched_intent": matched_intent,
            "matched_type": matched_type,
            "matched_pattern": matched_pattern,
            "regex_errors": regex_errors,
        }
        return matched_intent, detail

    # ---- 第二层：LLM 分类 ----

    async def _match_llm_layer(self, text: str, cfg: IntentRouterConfig) -> dict:
        """调用 LLM 做意图分类，解析 JSON 输出

        Returns:
            包含 called/raw_response/parsed/chosen/threshold/fallback_reason 的 dict
        """
        detail: dict[str, Any] = {
            "called": True,
            "model": cfg.model or "(default)",
            "raw_response": "",
            "parsed": None,
            "threshold": cfg.confidence_threshold,
            "chosen": None,
            "fallback_reason": None,
        }

        if not cfg.intents:
            detail["called"] = False
            detail["fallback_reason"] = "no_candidates"
            return detail

        # ---- 构建 system prompt ----
        intents_desc = self._build_intents_description(cfg.intents)
        system_prompt = _DEFAULT_SYSTEM_PROMPT.format(intents_description=intents_desc)
        if cfg.system_prompt.strip():
            system_prompt = system_prompt + "\n\n" + cfg.system_prompt.strip()

        # ---- 解析 LLM 配置（节点配置 > 全局默认）----
        llm_cfg = await self._resolve_llm_config(cfg)
        if not llm_cfg["model"] or not llm_cfg["api_key"]:
            detail["called"] = False
            detail["fallback_reason"] = "llm_not_configured"
            return detail

        # ---- 调用 LLM ----
        try:
            llm = self._create_llm(
                api_key=llm_cfg["api_key"],
                model=llm_cfg["model"],
                base_url=llm_cfg["base_url"],
                max_tokens=cfg.max_tokens,
                provider_name=llm_cfg["provider"],
                temperature=cfg.temperature,
            )
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=text),
            ]
            response = await llm.ainvoke(messages)
            raw = response.content if hasattr(response, "content") else str(response)
            if isinstance(raw, list):
                # multimodal 返回：取第一段文本
                raw = next(
                    (seg.get("text", "") for seg in raw if isinstance(seg, dict)),
                    str(raw),
                )
            raw_str = str(raw).strip()
            detail["raw_response"] = raw_str
        except Exception as e:
            logger.exception("意图路由 LLM 调用失败: %s", e)
            detail["called"] = True
            detail["fallback_reason"] = "llm_error"
            detail["raw_response"] = f"<error>{e}</error>"
            return detail

        # ---- 解析 JSON ----
        parsed = self._try_parse_intent_json(raw_str)
        detail["parsed"] = parsed
        if parsed is None:
            detail["fallback_reason"] = "json_parse_error"
            return detail

        candidate_keys = {it.key for it in cfg.intents}
        intent = parsed.get("intent", "")
        confidence = float(parsed.get("confidence", 0.0) or 0.0)

        if not intent or intent not in candidate_keys:
            detail["fallback_reason"] = "intent_not_in_candidates"
            return detail

        if confidence < cfg.confidence_threshold:
            detail["fallback_reason"] = "low_confidence"
            return detail

        detail["chosen"] = intent
        return detail

    @staticmethod
    def _build_intents_description(intents: list[IntentItem]) -> str:
        """构建给 LLM 看的候选意图描述文本"""
        lines: list[str] = []
        for it in intents:
            line = f"- key={it.key}"
            if it.description:
                line += f"：{it.description}"
            if it.examples:
                examples_str = "；".join(it.examples)
                line += f"\n  示例：{examples_str}"
            lines.append(line)
        return "\n".join(lines)

    async def _resolve_llm_config(self, cfg: IntentRouterConfig) -> dict:
        """合并节点配置与全局默认配置（节点优先）"""
        try:
            async with self._db_session_factory() as db:
                defaults = await global_config_service.get_default_llm_config(db)
        except Exception:
            logger.exception("加载全局默认 LLM 配置失败，回退到 .env")
            from app.config.settings import settings

            defaults = {
                "provider": "deepseek",
                "model": getattr(settings, "default_model", "") or "",
                "api_key": getattr(settings, "default_api_key", "") or "",
                "base_url": getattr(settings, "default_base_url", "") or "",
            }
        return {
            "provider": cfg.provider or defaults.get("provider") or "deepseek",
            "model": cfg.model or defaults.get("model") or "",
            "api_key": cfg.api_key or defaults.get("api_key") or "",
            "base_url": cfg.base_url or defaults.get("base_url") or "",
        }

    @staticmethod
    def _create_llm(
        api_key: str,
        model: str,
        base_url: str,
        max_tokens: int,
        provider_name: str,
        temperature: float,
    ) -> BaseChatModel:
        """通过 AI 提供商创建 LLM 实例（非流式，分类场景不需要）"""
        provider = create_provider(provider_name, api_key, base_url)
        return provider.create_chat_model(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            streaming=False,
        )

    @staticmethod
    def _try_parse_intent_json(raw: str) -> Optional[dict]:
        """从 LLM 输出中解析 {intent, confidence} JSON

        支持：纯 JSON / 带 markdown code fence / 前后多余文本
        """
        if not raw:
            return None

        text = raw.strip()
        # 去掉 markdown code fence
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            text = text.strip()

        # 直接尝试整段解析
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

        # 尝试提取第一个 {...} 块
        match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if match:
            try:
                obj = json.loads(match.group(0))
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass

        return None

    # ---- 节点输入输出（供执行结果面板展示）----

    @classmethod
    def get_input_content(
        cls,
        node: FlowNode,
        state: FlowState,
        resolver: Any,
        config: Optional[dict] = None,
    ) -> Optional[dict]:
        """获取节点输入内容（执行结果面板用）"""
        if config is None:
            config = node.base_config or {}
        input_variable = config.get("input_variable", "input.question")
        value = resolver.resolve(input_variable, state) if input_variable else None
        return {
            "input_variable": input_variable,
            "input_value": value,
            "intents_count": len(config.get("intents", [])),
            "enable_rule_layer": config.get("enable_rule_layer", True),
            "enable_llm_layer": config.get("enable_llm_layer", True),
        }

    @classmethod
    def get_output_content(
        cls,
        node: FlowNode,
        state: FlowState,
        resolver: Any,
        config: Optional[dict] = None,
    ) -> Optional[dict]:
        """获取节点输出内容（写入 NodeExecution.output_data，发到 node_done 事件）"""
        node_key = node.node_key
        intent = state.get_node_variable(node_key, "intent")
        metadata = state.get_node_variable(node_key, "metadata")
        if intent is None and metadata is None:
            return None
        return {
            "intent": intent,
            "route": state.get_variable("_intent_route"),
            "metadata": metadata,
        }


# ---- 工厂注册 ----


@NodeHandlerRegistry.register_factory("intent_router")
def create_intent_router_handler(
    flow: Any = None,
    db_session_factory: Optional[Callable] = None,
    execution_id: Optional[int] = None,
    conversation_service: Optional["ConversationService"] = None,
    handler_registry: Any = None,
    session_id: int = 0,
) -> IntentRouterHandler:
    """意图路由处理器工厂（依赖注入数据库会话工厂）"""
    return IntentRouterHandler(
        flow=flow,
        db_session_factory=db_session_factory,
        execution_id=execution_id,
        conversation_service=conversation_service,
        handler_registry=handler_registry,
        session_id=session_id,
    )

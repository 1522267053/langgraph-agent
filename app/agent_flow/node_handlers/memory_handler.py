"""
记忆节点处理器

三层记忆架构：
- hot（热）：按分类拆分注入——画像/指令类随 system_prompt（get_system_prompt_hint），
  其余随消息层 <system-reminder>（get_memory_reminder），均为紧凑指针索引
- warm（温）：LLM 通过 memory_search 向量检索获取；最新若干条以 "| 温" 标记行
  并入消息层记忆索引（带预览，热前温后）
- cold（冷）：低优先级记忆，搜索命中后自动升温

为 Agent 提供的 LLM 工具（实现见 tools/memory_toolkit.py）：
1. memory_save - 保存记忆（tier 由 importance 自动推断；热记忆接近上限预警、超限拒绝）
2. memory_consolidate - 整理热记忆（agent 自主决策 keep/demote/delete/merge，无内部 LLM）
3. memory_search - 向量搜索记忆（支持 hot/warm/cold）
4. memory_list - 列出最近记忆
5. memory_get - 通过 ID 精确获取记忆完整内容
6. memory_delete - 删除记忆

温度管理完全自动：
- 搜索命中自动升温；热记忆超限由 agent 调 memory_consolidate 自主整理
- 久未访问的记忆自动降温（hot→warm→cold），importance 越高衰减越慢

本 handler 只负责注册与依赖注入（agent_id 回调、节点配置），
工具实现与描述单源见 tools/memory_toolkit.py。
"""

import logging
from typing import ClassVar, Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.types import StreamWriter
from pydantic import BaseModel, Field

from app.models.memory import MemoryCategory
from app.models.flow_node import FlowNode
from app.agent_flow.flow_context import FlowState
from app.agent_flow.node_handlers.base_handler import BaseNodeConfig, BaseNodeHandler
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.tools.memory_toolkit import MemoryToolKit
from app.config.database import AsyncSessionLocal
from app.services.memory_service import memory_service

logger = logging.getLogger(__name__)


class MemoryNodeConfig(BaseNodeConfig):
    model_config = {"extra": "ignore"}
    max_results: int = 5
    default_importance: int = 3
    default_category: str = "event"
    max_index_lines: int = 200
    max_index_bytes: int = 25000
    warm_recent_count: int = 10
    auto_promote_threshold: int = 5
    consolidate_threshold: int = 25
    hot_decay_days: int = 25
    warm_decay_days: int = 35
    system_prompt_categories: list[str] = Field(
        default_factory=lambda: ["profile", "preference", "instruction"],
        description="注入 system_prompt 的热记忆分类（用户画像/长期指令），其余分类注入消息层 reminder",
    )

    VALID_CATEGORIES: ClassVar[list[str]] = [c.value for c in MemoryCategory]

    @property
    def prompt_category_set(self) -> set[str]:
        """解析 system_prompt_categories 并过滤合法分类；非法值静默忽略"""
        valid = set(self.VALID_CATEGORIES)
        return {c for c in self.system_prompt_categories if c in valid}


@NodeHandlerRegistry.register("memory")
class MemoryNodeHandler(BaseNodeHandler):
    """
    记忆节点处理器

    作为 LLM 工具提供者使用（通过 tools Handle 连接到 LLM 节点）。
    提供三层记忆管理能力：热记忆自动注入、温记忆向量检索、冷记忆自动升温。
    温度管理完全自动，无需 LLM 手动干预。

    base_config 说明：
    - max_results: search/list 工具的默认最大返回数
    - default_importance: save 工具的默认重要程度
    - default_category: save 工具的默认分类
     - max_index_lines: 热记忆索引最大行数（默认200）
     - max_index_bytes: 热记忆索引最大字节数（默认25000）
      - warm_recent_count: 并入消息层记忆索引的温记忆最新条数（带预览、
      "| 温" 标记；默认10，0=关闭）
    - auto_promote_threshold: 自动升温阈值（默认5次访问）
    - consolidate_threshold: 热记忆上限（默认25）：保存后 20~25 条预警、
      本次保存将导致超过 25 条时拒绝保存，agent 需调 memory_consolidate 自主整理
    - hot_decay_days: 热记忆衰减天数（默认25），超过此天数未访问则降为温记忆
    - warm_decay_days: 温记忆衰减天数（默认35），超过此天数未访问则降为冷记忆
    - system_prompt_categories: 注入 system_prompt 的热记忆分类（默认 profile/preference/instruction），
      其余分类的热索引与温记忆标题注入消息层 <system-reminder>
    """

    ConfigClass = MemoryNodeConfig

    def __init__(self):
        super().__init__()
        self._agent_id: Optional[int] = None
        self._llm_config: Optional[dict] = None
        self._hot_index_cache: Optional[str] = None

    def _get_agent_id(self, config: Optional[RunnableConfig] = None) -> Optional[int]:
        if self._agent_id is not None:
            return self._agent_id
        if config and "configurable" in config:
            flow_id = config["configurable"].get("flow_id")
            if flow_id:
                self._agent_id = flow_id
                return self._agent_id
        return None

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState | dict:
        return state

    @classmethod
    def allow_multiple_tool_connections(cls) -> bool:
        """记忆节点与 Agent 绑定，同一 LLM 只需连接一个"""
        return False

    async def get_system_prompt_hint(self, node: FlowNode) -> Optional[str]:
        """异步获取静态工具说明和画像/指令类热记忆索引，注入 system_prompt

        静态工具说明无条件注入（工具始终绑定，且从会话起 system_prompt 即定型，
        避免首条记忆保存时突然变化破坏前缀缓存）；仅画像/指令类索引随行注入，
        动态部分见 get_memory_reminder。
        """
        agent_id = self._agent_id
        if not agent_id:
            return None

        try:
            async with AsyncSessionLocal() as db:
                cfg = self._get_config(node)
                stable_index, _ = await memory_service.get_hot_index_split(
                    db,
                    agent_id,
                    cfg.prompt_category_set,
                    max_lines=cfg.max_index_lines,
                    max_bytes=cfg.max_index_bytes,
                )

            valid_cats = ",".join(MemoryNodeConfig.VALID_CATEGORIES)
            static_prefix = (
                "\n\n## 记忆系统\n"
                f"工具: get(ID)查详情|search(关键词)搜记忆(默认warm/cold,可指定tier=hot)|"
                f"save(保存,category:{valid_cats},importance=5→hot,2-4→warm,1→cold)|"
                f"consolidate(整理热记忆)\n"
                f"做任何实质性任务前，先 memory_search 检索相关记忆"
                f"（历史决策/教训/口径），避免重复踩坑。\n"
                f"主动保存用户偏好、重要决策、关键信息。\n"
                f"单条content硬上限600字符，内容多时拆成多条保存，不要反复微调字数重试。\n"
                f"热记忆上限{self._get_config(node).consolidate_threshold}条：接近上限会预警、"
                "超限保存被拒——收到预警/拒绝时先 memory_list(tier=hot) 查看，"
                "再调 memory_consolidate 整理（合并重复、demote低价值到温记忆、删除过时）\n"
            )
            parts = [static_prefix]
            if stable_index:
                parts.append(stable_index)
            return "\n".join(parts)
        except Exception:
            return None

    async def get_memory_reminder(self, node: FlowNode) -> Optional[str]:
        """动态记忆区块：非画像类热索引 + 温记忆合并为单一索引，注入消息层

        注入 <system-reminder> HumanMessage。这些内容随 memory_save/整理/升温
        频繁变化，放 system_prompt 会使整段对话的前缀缓存失效；放消息层
        reminder 后变化只影响其后的尾部缓存。
        索引头部含记忆总数与展示计数，节标题说明展示规则，
        让模型明确索引≠全量、其余记忆经 memory_search 检索。
        """
        agent_id = self._agent_id
        if not agent_id:
            return None

        try:
            async with AsyncSessionLocal() as db:
                cfg = self._get_config(node)
                index_text = await memory_service.get_reminder_index(
                    db,
                    agent_id,
                    exclude_categories=cfg.prompt_category_set,
                    warm_recent_count=cfg.warm_recent_count,
                    max_lines=cfg.max_index_lines,
                    max_bytes=cfg.max_index_bytes,
                )

            if not index_text:
                return None

            header = (
                "# 记忆索引（展示规则：热记忆全部+温记忆最近条目，热前温后、"
                "组内按重要性排列；未展示的记忆用 memory_search 关键词检索）"
            )
            return header + "\n" + index_text
        except Exception:
            return None

    async def get_tool(self, node: FlowNode) -> list[BaseTool]:
        """组装 MemoryToolKit（注入 agent_id 回调与节点配置）"""
        cfg = self._get_config(node)
        kit = MemoryToolKit(get_agent_id=self._get_agent_id, cfg=cfg)
        return list(await kit.build_tools())

    @classmethod
    def get_tool_info(cls, node: FlowNode) -> list[dict]:
        """委托 toolkit 单源派生（动态描述随节点配置渲染，与 get_tool 逐字节一致）"""
        return MemoryToolKit.tool_info(node)

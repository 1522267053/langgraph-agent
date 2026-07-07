"""
记忆节点处理器

三层记忆架构：
- hot（热）：每次对话自动注入 system_prompt，紧凑指针索引
- warm（温）：LLM 通过 memory_search 向量检索获取
- cold（冷）：低优先级记忆，搜索命中后自动升温

为 Agent 提供的 LLM 工具：
1. memory_save - 保存记忆（tier 由 importance 自动推断）
2. memory_search - 向量搜索记忆（支持 hot/warm/cold）
3. memory_list - 列出最近记忆
4. memory_get - 通过 ID 精确获取记忆完整内容
5. memory_delete - 删除记忆

温度管理完全自动：
- 搜索命中自动升温，热记忆超限自动 AI 总结整理
- 久未访问的记忆自动降温（hot→warm→cold），importance 越高衰减越慢
"""

import asyncio
import json
import logging
import re
import time
from typing import ClassVar, Optional, TYPE_CHECKING
from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool, BaseTool
from langgraph.types import StreamWriter
from pydantic import BaseModel, Field

from app.models.memory import MemoryCategory
from app.models.flow_node import FlowNode
from app.agent_flow.flow_context import FlowState
from app.agent_flow.node_handlers.base_handler import BaseNodeHandler
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.config.database import AsyncSessionLocal
from app.services.memory_service import memory_service
from app.utils.message_utils import extract_text_content

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


def _format_memory(m) -> dict:
    return {
        "id": m.id,
        "title": m.title,
        "content": m.content,
        "category": m.category,
        "tier": m.memory_type,
        "importance": m.importance,
        "keywords": m.keywords,
        "access_count": m.access_count,
        "create_time": m.create_time.isoformat() if m.create_time else None,
    }


class MemoryNodeConfig(BaseModel):
    model_config = {"extra": "ignore"}
    max_results: int = 5
    default_importance: int = 3
    default_category: str = "event"
    max_index_lines: int = 200
    max_index_bytes: int = 25000
    auto_promote_threshold: int = 5
    consolidate_threshold: int = 25
    hot_decay_days: int = 30
    warm_decay_days: int = 60
    consolidate_interval_days: int = 7

    VALID_CATEGORIES: ClassVar[list[str]] = [c.value for c in MemoryCategory]


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
    - auto_promote_threshold: 自动升温阈值（默认5次访问）
    - consolidate_threshold: 热记忆超过此数量时触发 AI 总结整理（默认50）
    - hot_decay_days: 热记忆衰减天数（默认30），超过此天数未更新则降为温记忆
    - warm_decay_days: 温记忆衰减天数（默认60），超过此天数未更新则降为冷记忆
    - consolidate_interval_days: 时间触发整理间隔（默认7），距上次整理超过此天数且 hot>0 时强制整理
    """

    ConfigClass = MemoryNodeConfig

    def __init__(self):
        super().__init__()
        self._agent_id: Optional[int] = None
        self._llm_config: Optional[dict] = None
        self._hot_index_cache: Optional[str] = None
        self._last_consolidate_time: float = 0.0
        self._consolidate_cooldown: float = 300.0
        self._consolidate_locks: dict[int, asyncio.Lock] = {}
        self._last_consolidate_times: dict[int, float] = {}

    def _get_consolidate_lock(self, agent_id: int) -> asyncio.Lock:
        if agent_id not in self._consolidate_locks:
            self._consolidate_locks[agent_id] = asyncio.Lock()
        return self._consolidate_locks[agent_id]

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
        """异步获取热记忆索引并注入 system_prompt"""
        agent_id = self._agent_id
        if not agent_id:
            return None

        try:
            async with AsyncSessionLocal() as db:
                cfg = self._get_config(node)
                max_lines = cfg.max_index_lines
                max_bytes = cfg.max_index_bytes

                hot_index = await memory_service.get_hot_index(
                    db, agent_id, max_lines=max_lines, max_bytes=max_bytes
                )

            if not hot_index:
                return None

            valid_cats = ",".join(MemoryNodeConfig.VALID_CATEGORIES)
            static_prefix = (
                "\n\n## 记忆系统\n"
                f"工具: get(ID)查详情|search(关键词)搜记忆(默认warm/cold,可指定tier=hot)|"
                f"save(保存,category:{valid_cats},importance=5→hot,3-4→warm,1-2→cold)\n"
                f"主动保存用户偏好、重要决策、关键信息。\n"
            )
            return f"{static_prefix}\n{hot_index}"
        except Exception:
            return None

    async def get_tool(self, node: FlowNode) -> list[BaseTool]:
        cfg = self._get_config(node)
        handler = self

        max_results = cfg.max_results
        default_importance = cfg.default_importance
        default_category = cfg.default_category
        auto_promote_threshold = cfg.auto_promote_threshold
        consolidate_threshold = cfg.consolidate_threshold
        hot_decay_days = cfg.hot_decay_days
        warm_decay_days = cfg.warm_decay_days
        consolidate_interval_days = cfg.consolidate_interval_days

        async def _consolidate_hot_memories(
            agent_id: int,
            target_count: int,
            protected_ids: Optional[list[int]] = None,
            skip_cooldown: bool = False,
        ) -> Optional[dict]:
            """热记忆超限时调用 LLM 全量总结整理

            最多重试 3 次，失败后按 importance 排序截断降级兜底。
            """
            if not handler._llm_config:
                logger.warning("无法整理热记忆：未注入 LLM 配置")
                return None

            # 并发锁
            lock = handler._get_consolidate_lock(agent_id)
            if lock.locked():
                logger.info(f"热记忆整理已在执行中，跳过（agent_id={agent_id}）")
                return None
            async with lock:
                return await _do_consolidate(
                    agent_id, target_count, protected_ids, skip_cooldown
                )

        async def _do_consolidate(
            agent_id: int,
            target_count: int,
            protected_ids: Optional[list[int]],
            skip_cooldown: bool,
        ) -> Optional[dict]:
            # 冷却检查（时间触发不受冷却限制）
            now = time.time()
            if not skip_cooldown:
                if now - handler._last_consolidate_time < handler._consolidate_cooldown:
                    logger.info(f"热记忆整理冷却中，跳过（agent_id={agent_id}）")
                    return None

            async with AsyncSessionLocal() as db:
                all_hot = await memory_service.get_all_hot_memories_full(db, agent_id)

            if not all_hot:
                return None

            # 按 importance 降序排列，取前 target_count*2 条传给 LLM（避免 prompt 过大）
            all_hot.sort(key=lambda x: x.get("importance", 0), reverse=True)
            hot_for_llm = all_hot[: max(target_count * 2, 30)]

            hot_json = json.dumps(hot_for_llm, ensure_ascii=False, indent=2)
            system_msg = (
                "你是一个记忆管理系统。以下是一个 Agent 的所有热记忆，请进行整理。\n\n"
                "## 整理规则\n"
                "1. 合并重复或相似的记忆\n"
                "2. 压缩冗长的描述为简洁摘要（标题不超过50字，内容不超过200字）\n"
                "3. 优先保留高重要性的记忆，尤其是用户的明确偏好和关键决策\n"
                "4. 删除过时、矛盾或不再相关的信息\n"
                "5. 每条记忆必须包含 title(标题) 和 content(内容)\n"
                "6. 每条记忆应包含 importance(重要程度1-5)，尽量保留原始重要程度\n"
                "7. 可选 category 和 keywords，尽量保留原始分类和关键词\n"
                "8. 分类只能是：decision/preference/lesson/relation/event/task/profile/other\n\n"
                f"## 重要：尽量精简到 {target_count} 条以内，但如果信息都很重要，可以适当超出。\n\n"
                "## 当前热记忆\n"
                f"{hot_json}\n\n"
                "## 输出格式\n"
                '返回纯 JSON 数组，每个元素: {"title":"...","content":"...","category":"...","importance":5,"keywords":"..."}\n'
                "不要输出任何其他内容，不要用 markdown 代码块包裹。"
            )

            provider_name = handler._llm_config.get("provider", "deepseek")
            from app.agent_flow.ai_provider import create_provider

            provider = create_provider(
                provider_name,
                handler._llm_config.get("api_key", ""),
                handler._llm_config.get("base_url", ""),
            )
            llm = provider.create_chat_model(
                model=handler._llm_config.get("model", ""),
                temperature=0.3,
                streaming=False,
            )

            # ---- 最多重试 3 次 ----
            max_retries = 3
            last_error = None
            new_memories = None

            for attempt in range(1, max_retries + 1):
                try:
                    prompt = system_msg
                    if attempt > 1 and last_error:
                        prompt = (
                            f"{system_msg}\n\n"
                            "## 上次输出格式错误\n"
                            f"错误: {last_error}\n"
                            "请严格按以下格式输出 JSON 数组，不要包含任何额外文字或代码块：\n"
                            '[{"title":"...","content":"...","category":"...","importance":5}]\n'
                        )

                    response = await llm.ainvoke([SystemMessage(content=prompt)])
                    text = extract_text_content(response.content).strip()
                    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
                    text = re.sub(r"\n?```\s*$", "", text)
                    parsed = json.loads(text)

                    if not isinstance(parsed, list):
                        last_error = f"输出不是数组，而是 {type(parsed).__name__}"
                        logger.warning(
                            f"热记忆整理第{attempt}次尝试格式异常: {last_error}"
                        )
                        continue

                    # 条目级校验
                    valid_items = []
                    invalid_count = 0
                    for idx, item in enumerate(parsed):
                        title = (item.get("title") or "").strip()
                        content = (item.get("content") or "").strip()
                        if not title or not content:
                            invalid_count += 1
                            continue
                        valid_items.append(item)

                    if not valid_items:
                        last_error = "所有条目均缺少 title 或 content"
                        logger.warning(f"热记忆整理第{attempt}次尝试: {last_error}")
                        continue

                    # 无效条目占比 > 50% 时触发重试
                    if invalid_count > len(parsed) * 0.5:
                        last_error = f"无效条目占比过高: {invalid_count}/{len(parsed)}"
                        logger.warning(f"热记忆整理第{attempt}次尝试: {last_error}")
                        continue

                    new_memories = valid_items
                    break

                except json.JSONDecodeError as e:
                    last_error = f"JSON 解析失败: {e}"
                    logger.warning(f"热记忆整理第{attempt}次尝试: {last_error}")
                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"热记忆整理第{attempt}次尝试异常: {last_error}")
                    if attempt < max_retries:
                        await asyncio.sleep(1)

            # ---- 结果处理 ----
            if new_memories:
                async with AsyncSessionLocal() as db:
                    result = await memory_service.consolidate_hot_memories(
                        db,
                        agent_id,
                        new_memories,
                        protected_ids=protected_ids,
                    )
                logger.info(
                    f"热记忆 AI 整理完成: agent_id={agent_id}, "
                    f"{result['old_count']}条 → {result['new_count']}条"
                )
                handler._last_consolidate_time = time.time()
                handler._last_consolidate_times[agent_id] = time.time()
                return result

            # ---- LLM 全部失败，降级兜底 ----
            logger.warning(
                f"热记忆 AI 整理全部失败（{max_retries}次），"
                f"回退到截断降级模式: agent_id={agent_id}"
            )
            async with AsyncSessionLocal() as db:
                # 先降级到目标数量
                await memory_service._demote_to_target(db, agent_id, target_count)
                # 再截断到 consolidate_threshold - 10
                hard_limit = max(target_count - 10, 5)
                await memory_service._demote_to_target(db, agent_id, hard_limit)
                await db.commit()

            handler._last_consolidate_time = time.time()
            handler._last_consolidate_times[agent_id] = time.time()
            return {
                "old_count": len(all_hot),
                "new_count": 0,
                "fallback": True,
                "message": f"LLM 整理失败，已按重要性降级到 {hard_limit} 条",
            }

        async def save_memory(
            memories: str,
        ) -> str:
            agent_id = handler._get_agent_id()
            if not agent_id:
                return json.dumps({"error": "无法获取Agent ID"}, ensure_ascii=False)

            now_ts = time.time()

            try:
                items = json.loads(memories)
            except json.JSONDecodeError:
                return json.dumps(
                    {"error": "memories 参数必须是合法的 JSON 数组"}, ensure_ascii=False
                )

            if not isinstance(items, list):
                items = [items]

            if not items:
                return json.dumps({"error": "memories 不能为空"}, ensure_ascii=False)

            async with AsyncSessionLocal() as db:
                results = []
                has_hot = False
                protected_ids: list[int] = []
                skipped = []
                for idx, item in enumerate(items):
                    title = item.get("title", "").strip()
                    content = item.get("content", "").strip()
                    if not title and not content:
                        skipped.append(
                            {"index": idx, "reason": "title 和 content 均为空"}
                        )
                        continue
                    if not title:
                        skipped.append(
                            {
                                "index": idx,
                                "reason": "缺少 title（必填），请提供简短标题",
                            }
                        )
                        continue
                    if not content:
                        skipped.append({"index": idx, "reason": "缺少 content（必填）"})
                        continue
                    category = item.get("category", default_category)
                    if category not in MemoryNodeConfig.VALID_CATEGORIES:
                        category = default_category
                    importance = item.get("importance", default_importance)
                    tier = item.get("tier") or memory_service.infer_tier(importance)
                    if tier == "hot":
                        has_hot = True
                    memory = await memory_service.save_memory(
                        db=db,
                        agent_id=agent_id,
                        title=title,
                        content=content,
                        memory_type=tier,
                        category=category,
                        importance=importance,
                        keywords=item.get("keywords"),
                        hot_decay_days=hot_decay_days,
                        warm_decay_days=warm_decay_days,
                        skip_decay=True,
                    )
                    if tier == "hot":
                        protected_ids.append(memory.id)
                    results.append(
                        {
                            "id": memory.id,
                            "title": memory.title,
                            "tier": memory.memory_type,
                        }
                    )

                need_consolidate = False
                skip_cooldown = False
                if has_hot:
                    hot_count = await memory_service.get_hot_count(db, agent_id)
                    if hot_count > consolidate_threshold:
                        need_consolidate = True
                    elif hot_count > 0 and consolidate_interval_days > 0:
                        last_ts = handler._last_consolidate_times.get(agent_id, 0)
                        if last_ts > 0:
                            interval_secs = consolidate_interval_days * 86400
                            if now_ts - last_ts > interval_secs:
                                need_consolidate = True
                                skip_cooldown = True

            response_data = {
                "success": len(results) > 0 or not skipped,
                "saved_count": len(results),
                "results": results,
            }

            if skipped:
                response_data["skipped"] = skipped
                if not results:
                    response_data["error"] = (
                        f"所有 {len(items)} 条记忆均缺少必填字段 title，"
                        f"请为每条记忆提供 title（简短标题，≤50字符）"
                    )

            if need_consolidate:
                target_count = max(consolidate_threshold - 10, 10)
                consolidated = await _consolidate_hot_memories(
                    agent_id,
                    target_count,
                    protected_ids=protected_ids if protected_ids else None,
                    skip_cooldown=skip_cooldown,
                )
                if consolidated:
                    msg = consolidated.get("message", "")
                    if consolidated.get("fallback"):
                        response_data["consolidated"] = {
                            "triggered": True,
                            "fallback": True,
                            "message": msg,
                        }
                    else:
                        response_data["consolidated"] = {
                            "triggered": True,
                            "old_count": consolidated["old_count"],
                            "new_count": consolidated["new_count"],
                            "message": f"热记忆已整理：{consolidated['old_count']}条 → {consolidated['new_count']}条",
                        }
                else:
                    response_data["consolidated"] = {
                        "triggered": False,
                        "message": "热记忆整理失败，将在下次保存时重试",
                    }

            return json.dumps(response_data, ensure_ascii=False)

        async def search_memory(
            query: str,
            tier: Optional[str] = None,
            categories: Optional[str] = None,
            max_results: int = max_results,
            min_score: float = 0.0,
        ) -> str:
            agent_id = handler._get_agent_id()
            if not agent_id:
                return json.dumps({"error": "无法获取Agent ID"}, ensure_ascii=False)

            cat_list = (
                [c.strip() for c in categories.split(",")] if categories else None
            )

            effective_tier = tier if tier else "warm,cold"
            tier_list = [t.strip() for t in effective_tier.split(",") if t.strip()]
            if not tier_list:
                tier_list = ["warm", "cold"]

            async with AsyncSessionLocal() as db:
                all_results: list = []
                seen_ids: set = set()
                per_tier = max(1, max(max_results // len(tier_list), 3))

                for t in tier_list:
                    tier_results = await memory_service.search(
                        db=db,
                        agent_id=agent_id,
                        query=query,
                        tier=t,
                        categories=cat_list,
                        max_results=per_tier,
                        min_score=min_score,
                        hot_decay_days=hot_decay_days,
                        warm_decay_days=warm_decay_days,
                    )
                    for memory, score in tier_results:
                        if memory.id not in seen_ids:
                            seen_ids.add(memory.id)
                            all_results.append((memory, score))

                all_results.sort(key=lambda x: x[1], reverse=True)
                all_results = all_results[:max_results]

                items = []
                for memory, score in all_results:
                    item = _format_memory(memory)
                    item["score"] = score
                    items.append(item)

                hit_ids = [m.id for m, _ in all_results]
                for mid in hit_ids:
                    await memory_service.increment_access(
                        db, mid, threshold=auto_promote_threshold
                    )

                return json.dumps(
                    {"results": items, "total": len(items)}, ensure_ascii=False
                )

        async def list_memory(
            limit: int = max_results,
            tier: Optional[str] = None,
            category: Optional[str] = None,
        ) -> str:
            agent_id = handler._get_agent_id()
            if not agent_id:
                return json.dumps({"error": "无法获取Agent ID"}, ensure_ascii=False)

            async with AsyncSessionLocal() as db:
                memories = await memory_service.get_recent(
                    db=db,
                    agent_id=agent_id,
                    limit=limit,
                    tier=tier,
                    category=category,
                )

                results = [_format_memory(m) for m in memories]
                return json.dumps(
                    {"results": results, "total": len(results)}, ensure_ascii=False
                )

        async def delete_memory(memory_ids: str) -> str:
            agent_id = handler._get_agent_id()
            if not agent_id:
                return "无法获取Agent ID"

            ids = [int(i.strip()) for i in memory_ids.split(",") if i.strip()]
            if not ids:
                return "未提供有效的记忆ID"

            async with AsyncSessionLocal() as db:
                valid_memories = await memory_service.get_by_ids(db, agent_id, ids)
                valid_ids = {m.id for m in valid_memories}
                deleted_ids = []
                for mid in ids:
                    if mid in valid_ids:
                        await memory_service.delete(db, mid)
                        deleted_ids.append(mid)
                await db.commit()
                return f"已删除 {len(deleted_ids)} 条记忆 (ID: {', '.join(str(i) for i in deleted_ids)})"

        async def get_memory(memory_ids: str) -> str:
            """通过 ID 批量获取记忆的完整内容"""
            agent_id = handler._get_agent_id()
            if not agent_id:
                return json.dumps({"error": "无法获取Agent ID"}, ensure_ascii=False)

            ids = [int(i.strip()) for i in memory_ids.split(",") if i.strip()]
            if not ids:
                return json.dumps({"error": "未提供有效的记忆ID"}, ensure_ascii=False)

            async with AsyncSessionLocal() as db:
                memories = await memory_service.get_by_ids(db, agent_id, ids)
                items = [_format_memory(m) for m in memories]
                return json.dumps(
                    {"results": items, "total": len(items)}, ensure_ascii=False
                )

        return [
            StructuredTool(
                name="memory_save",
                description=(
                    f"保存记忆。importance=5→hot,3-4→warm,1-2→cold，也可指定tier。"
                    f"category: {','.join(MemoryNodeConfig.VALID_CATEGORIES)}，"
                    f"默认category={default_category},importance={default_importance}。"
                ),
                func=None,
                coroutine=save_memory,
                args_schema=MemorySaveInput,
            ),
            StructuredTool(
                name="memory_search",
                description="语义搜索记忆。默认搜warm+cold，tier='hot'搜热记忆。命中自动升温。",
                func=None,
                coroutine=search_memory,
                args_schema=MemorySearchInput,
            ),
            StructuredTool(
                name="memory_list",
                description="列出最近记忆，可按tier和category过滤。",
                func=None,
                coroutine=list_memory,
                args_schema=MemoryListInput,
            ),
            StructuredTool(
                name="memory_delete",
                description="批量删除记忆，ID逗号分隔。",
                func=None,
                coroutine=delete_memory,
                args_schema=MemoryDeleteInput,
            ),
            StructuredTool(
                name="memory_get",
                description="按ID获取记忆完整内容，逗号分隔多ID。",
                func=None,
                coroutine=get_memory,
                args_schema=MemoryGetInput,
            ),
        ]

    @classmethod
    def get_tool_info(cls, node: FlowNode) -> list[dict]:
        return [
            {"name": "memory_save", "description": "保存记忆"},
            {"name": "memory_search", "description": "语义搜索记忆"},
            {"name": "memory_list", "description": "列出最近记忆"},
            {"name": "memory_delete", "description": "批量删除记忆"},
            {"name": "memory_get", "description": "按ID获取记忆"},
        ]


# ---- 工具输入 Schema ----


class MemorySaveInput(BaseModel):
    memories: str = Field(
        ...,
        description='JSON数组，每条: {"title":"必填","content":"必填","category":"可选","importance":3,"tier":"可选"}',
    )


class MemorySearchInput(BaseModel):
    query: str = Field(..., description="搜索关键词或自然语言")
    tier: Optional[str] = Field(
        None, description="hot/warm/cold，不传默认warm,cold，可逗号分隔多个"
    )
    categories: Optional[str] = Field(None, description="分类过滤，逗号分隔")
    max_results: int = Field(5, ge=1, le=20, description="最大返回数")
    min_score: float = Field(0.0, ge=0.0, le=1.0, description="最低相关度")


class MemoryListInput(BaseModel):
    limit: int = Field(10, ge=1, le=50, description="返回数量")
    tier: Optional[str] = Field(None, description="hot/warm/cold")
    category: Optional[str] = Field(None, description="分类过滤")


class MemoryDeleteInput(BaseModel):
    memory_ids: str = Field(..., description="ID列表，逗号分隔")


class MemoryGetInput(BaseModel):
    memory_ids: str = Field(..., description="ID列表，逗号分隔")

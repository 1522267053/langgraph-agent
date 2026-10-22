"""
记忆管理工具集（MemoryToolKit）

从 memory_handler.get_tool 抽出的 6 个工具：memory_save / memory_consolidate /
memory_search / memory_list / memory_delete / memory_get。

三层记忆架构（hot/warm/cold）说明见 memory_handler 模块 docstring；
温度管理全自动：搜索命中自动升温、超限由 agent 调 consolidate 自主整理。

依赖注入：get_agent_id 回调（handler._get_agent_id）+ MemoryNodeConfig
节点配置值（阈值/衰减天数等 8 个）。

描述单源：动态 f-string 描述提取为模块级构建函数 _build_*_description(cfg)，
tool_info 与 build_tools 共用——描述随节点配置自动同步，禁止手抄短句。

依赖方向：tools/* → app/services/*、app/config/*；
本模块不 import node_handlers。
"""

import logging
from typing import TYPE_CHECKING, Callable, Optional, Sequence

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent_flow.tools.base_abs_tool import BaseToolKit
from app.config.database import AsyncSessionLocal
from app.services.memory_service import memory_service

if TYPE_CHECKING:
    from app.models.flow_node import FlowNode

logger = logging.getLogger(__name__)


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


# ---- 工具参数 Schema ----


class MemoryItem(BaseModel):
    model_config = {"extra": "ignore"}
    title: str = Field(..., description="记忆标题（必填,70字以内）")
    content: str = Field(
        ...,
        description=(
            "记忆内容（必填，硬上限600字符，超限直接报错）。内容较长时拆成多条记忆"
            "（memories 数组同次传多条），不要反复微调字数重试"
        ),
    )
    category: Optional[str] = Field(default=None, description="分类")
    importance: int = Field(default=3, description="重要程度1-5")
    tier: Optional[str] = Field(default=None, description="hot/warm/cold")
    keywords: Optional[str] = Field(default=None, description="关键词")


class MemorySaveInput(BaseModel):
    memories: list[MemoryItem] = Field(
        ...,
        description="记忆数组",
    )


class ConsolidateItem(BaseModel):
    model_config = {"extra": "ignore"}
    id: int = Field(..., description="热记忆 ID（memory_list(tier=hot) 返回的 id）")
    action: str = Field(
        ...,
        description=(
            "处置动作: keep（保留）/ demote（降为温记忆，信息不丢）/ "
            "delete（删除，仅过时矛盾错误条目）/ merge（合并为一条新热记忆，"
            "需提供 title/content）"
        ),
    )
    title: Optional[str] = Field(
        default=None, description="merge 必填：合并后的标题（70字以内）"
    )
    content: Optional[str] = Field(
        default=None, description="merge 必填：合并后的内容（硬上限600字符）"
    )
    category: Optional[str] = Field(
        default=None, description="merge 可选：分类，默认继承原记忆"
    )
    importance: Optional[int] = Field(
        default=None, description="merge 可选：重要程度1-5，默认继承原记忆"
    )
    keywords: Optional[str] = Field(
        default=None, description="merge 可选：关键词，默认继承原记忆"
    )


class ConsolidateInput(BaseModel):
    memories: list[ConsolidateItem] = Field(
        ...,
        description=(
            "整理决策数组，覆盖全部热记忆（每条一个决策）。任一 id 无效整批拒绝"
        ),
    )


class MemorySearchInput(BaseModel):
    query: str = Field(
        ...,
        description="搜索文本。建议用完整的句子或描述（向量检索句子越长越精准），"
        "也支持空格分隔多个关键词（任一命中即召回）",
    )
    tier: Optional[list[str]] = Field(
        default=None, description="hot/warm/cold，不传默认warm,cold，可传多个"
    )
    categories: Optional[list[str]] = Field(
        default=None, description="分类过滤，可传多个"
    )
    max_results: int = Field(default=5, ge=1, le=20, description="最大返回数")
    min_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="最低相关度(0-1)，不传使用系统默认阈值",
    )


class MemoryListInput(BaseModel):
    limit: int = Field(default=10, ge=1, le=50, description="返回数量")
    tier: Optional[str] = Field(default=None, description="hot/warm/cold")
    category: Optional[str] = Field(default=None, description="分类过滤")


class MemoryDeleteInput(BaseModel):
    memory_ids: list[int] = Field(..., description="要删除的记忆ID列表")


class MemoryGetInput(BaseModel):
    memory_ids: list[int] = Field(..., description="要获取的记忆ID列表")


# ---- 描述单源（动态构建函数；tool_info 与 build_tools 共用）----
# 描述含节点配置值（阈值/分类等），用构建函数而非类属性——随配置自动同步


def _build_save_description(cfg) -> str:
    return (
        f"保存记忆。importance=5→hot,2-4→warm,1→cold，也可指定tier。"
        f"category: {','.join(cfg.VALID_CATEGORIES)}，"
        f"默认category={cfg.default_category},importance={cfg.default_importance}。"
        f"单条content硬上限600字符，超限报错；内容多时拆成多条（memories数组），"
        f"禁止反复微调字数重试。"
        f"热记忆上限{cfg.consolidate_threshold}条：达到"
        f"{max(cfg.consolidate_threshold - 5, 1)}条时返回预警，"
        f"超限时保存被拒绝——此时先 memory_list(tier=hot) 查看，"
        f"再调 memory_consolidate 整理后重试"
    )


def _build_consolidate_description(cfg) -> str:
    return (
        f"整理热记忆（热记忆接近/达到上限{cfg.consolidate_threshold}条时调用）。"
        "先用 memory_list(tier=hot) 查看全部热记忆，再对每条输出处置决策："
        "keep保留 / demote降为温记忆（信息不丢，仍可检索） / "
        "delete删除（仅过时矛盾错误条目） / merge合并为一条新热记忆"
        "（需提供合并后的title/content）。"
        "整理规则：合并重复相似、压缩冗长描述（标题≤70字内容≤200字）、"
        "优先保留用户偏好与关键决策、删除过时矛盾信息、"
        f"低价值条目demote，目标压到{max(cfg.consolidate_threshold - 10, 10)}条以内。"
        "任一id无效整批拒绝，覆盖全部热记忆逐条给出决策"
    )


def _build_search_description(cfg) -> str:
    return (
        "语义搜索记忆。默认搜warm+cold，tier='hot'搜热记忆。命中自动升温。"
        "向量检索query越完整（句子/描述）越精准，短关键词精度较低；"
        "也支持空格分隔多个关键词，任一命中即召回。"
        "实现说明：已配置向量模型时向量检索优先、无结果自动SQL兜底；"
        "未配置向量模型时直接SQL模糊搜索。"
    )


def _build_list_description(cfg) -> str:
    return "列出最近记忆，可按tier和category过滤。"


def _build_delete_description(cfg) -> str:
    return "批量删除记忆，ID逗号分隔。"


def _build_get_description(cfg) -> str:
    return "按ID获取记忆完整内容，逗号分隔多ID。"


# tool_info 派生用：短名 → 构建函数
_MEMORY_DESCRIPTION_BUILDERS = {
    "memory_save": _build_save_description,
    "memory_consolidate": _build_consolidate_description,
    "memory_search": _build_search_description,
    "memory_list": _build_list_description,
    "memory_delete": _build_delete_description,
    "memory_get": _build_get_description,
}


class MemoryToolKit(BaseToolKit):
    """记忆管理工具集（6 个工具）

    由 memory_handler.get_tool 构造，注入 agent_id 读取回调与节点配置。
    """

    def __init__(self, get_agent_id: Callable[[], Optional[int]], cfg):
        self._get_agent_id = get_agent_id
        self._cfg = cfg

    async def build_tools(self) -> Sequence[StructuredTool]:
        get_agent_id = self._get_agent_id
        cfg = self._cfg
        max_results = cfg.max_results
        auto_promote_threshold = cfg.auto_promote_threshold
        consolidate_threshold = cfg.consolidate_threshold
        hot_decay_days = cfg.hot_decay_days
        warm_decay_days = cfg.warm_decay_days

        async def save_memory(
            memories: list[MemoryItem],
        ) -> dict:
            agent_id = get_agent_id()
            if not agent_id:
                return {"error": "无法获取Agent ID"}

            items = memories

            if not items:
                return {"error": "memories 不能为空"}

            # ---- 预判式超限检查：本次保存将导致热记忆超过上限时整批拒绝 ----
            # （先查数再落库，不先写后删；本次 items 中 tier=hot 的条数计入）
            hot_items_count = 0
            for item in items:
                title = (item.title or "").strip()
                content = (item.content or "").strip()
                if not title or not content:
                    continue
                importance = item.importance or cfg.default_importance
                tier = item.tier or memory_service.infer_tier(importance)
                if tier == "hot":
                    hot_items_count += 1

            async with AsyncSessionLocal() as db:
                current_hot = await memory_service.get_hot_count(db, agent_id)

            warn_threshold = max(consolidate_threshold - 5, 1)
            if current_hot + hot_items_count > consolidate_threshold:
                return {
                    "success": False,
                    "error": (
                        f"热记忆已达 {consolidate_threshold} 条上限"
                        f"（当前 {current_hot} 条，本次再存 {hot_items_count} 条将超限），"
                        "无法继续保存。请先 memory_list(tier=hot) 查看全部热记忆，"
                        "再调用 memory_consolidate 整理"
                        f"（合并重复/降级低价值到温记忆/删除过时条目，"
                        f"目标压到 {max(consolidate_threshold - 10, 10)} 条以内），"
                        "整理后再保存"
                    ),
                    "hot_count": current_hot,
                    "action": "consolidate",
                }

            async with AsyncSessionLocal() as db:
                results = []
                skipped = []
                new_hot_count = 0
                for idx, item in enumerate(items):
                    title = (item.title or "").strip()
                    content = (item.content or "").strip()
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
                    category = item.category or cfg.default_category
                    if category not in cfg.VALID_CATEGORIES:
                        category = cfg.default_category
                    importance = item.importance or cfg.default_importance
                    tier = item.tier or memory_service.infer_tier(importance)
                    memory = await memory_service.save_memory(
                        db=db,
                        agent_id=agent_id,
                        title=title,
                        content=content,
                        memory_type=tier,
                        category=category,
                        importance=importance,
                        keywords=item.keywords,
                        hot_decay_days=hot_decay_days,
                        warm_decay_days=warm_decay_days,
                        skip_decay=True,
                    )
                    if tier == "hot":
                        new_hot_count += 1
                    results.append(
                        {
                            "id": memory.id,
                            "title": memory.title,
                            "tier": memory.memory_type,
                        }
                    )

                hot_count_after = await memory_service.get_hot_count(db, agent_id)

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
                        f"请为每条记忆提供 title（简短标题，≤70字符）"
                    )

            # ---- 预警：热记忆接近上限（保存成功但提示 agent 尽快整理） ----
            if new_hot_count > 0 and hot_count_after >= warn_threshold:
                response_data["warning"] = (
                    f"热记忆已达 {hot_count_after} 条，接近上限 {consolidate_threshold}，"
                    "达到上限后将无法继续保存；建议尽快调用 memory_consolidate 整理"
                    f"（目标压到 {max(consolidate_threshold - 10, 10)} 条以内）"
                )
                response_data["hot_count"] = hot_count_after

            return response_data

        async def consolidate_memory(memories: list[ConsolidateItem]) -> dict:
            """按主 agent 的处置决策批量整理热记忆（前台自整理，无内部 LLM 调用）"""
            agent_id = get_agent_id()
            if not agent_id:
                return {"error": "无法获取Agent ID"}
            if not memories:
                return {"error": "memories 不能为空"}

            items = [
                {
                    "id": it.id,
                    "action": it.action,
                    "title": it.title,
                    "content": it.content,
                    "category": it.category,
                    "importance": it.importance,
                    "keywords": it.keywords,
                }
                for it in memories
            ]

            try:
                async with AsyncSessionLocal() as db:
                    result = await memory_service.batch_consolidate(db, agent_id, items)
            except ValueError as e:
                return {"success": False, "error": str(e)}
            except Exception as e:
                logger.warning(f"热记忆整理失败: agent_id={agent_id}, {e}")
                return {
                    "success": False,
                    "error": f"整理执行失败: {e}；可稍后重试，或用 memory_delete "
                    "删除少量低价值记忆腾出空间",
                }

            response_data = {
                "success": True,
                "before": result["before"],
                "after": result["after"],
                "actions": result["actions"],
                "merged": result["results"],
                "note": (
                    f"整理完成：{result['before']}条 → {result['after']}条"
                    f"（keep={result['actions']['keep']} "
                    f"demote={result['actions']['demote']} "
                    f"delete={result['actions']['delete']} "
                    f"merge={result['actions']['merge']}）；"
                    "demote 的记忆降为温记忆仍可被 memory_search 检索到"
                ),
            }
            # 压力提示：整理后仍超目标数量
            soft_target = max(consolidate_threshold - 10, 10)
            if result["after"] > soft_target:
                response_data["warning"] = (
                    f"整理后仍有 {result['after']} 条热记忆，超过建议目标 {soft_target} 条；"
                    "建议继续合并重复条目或将低价值记忆 demote"
                )
            return response_data

        async def search_memory(
            query: str,
            tier: Optional[list[str]] = None,
            categories: Optional[list[str]] = None,
            max_results: int = max_results,
            min_score: Optional[float] = None,
        ) -> dict:
            agent_id = get_agent_id()
            if not agent_id:
                return {"error": "无法获取Agent ID"}

            # 未显式指定时使用全局阈值配置（MEMORY_SEARCH_MIN_SCORE）
            if min_score is None:
                from app.config.settings import settings

                min_score = settings.memory_search_min_score

            cat_list = categories

            tier_list = tier if tier else ["warm", "cold"]

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

                return {"results": items, "total": len(items)}

        async def list_memory(
            limit: int = max_results,
            tier: Optional[str] = None,
            category: Optional[str] = None,
        ) -> str | dict:
            agent_id = get_agent_id()
            if not agent_id:
                return {"error": "无法获取Agent ID"}

            async with AsyncSessionLocal() as db:
                memories = await memory_service.get_recent(
                    db=db,
                    agent_id=agent_id,
                    limit=limit,
                    tier=tier,
                    category=category,
                )

                results = [_format_memory(m) for m in memories]
                return {"results": results, "total": len(results)}

        async def delete_memory(memory_ids: list[int]) -> dict:
            agent_id = get_agent_id()
            if not agent_id:
                return {"error": "无法获取Agent ID"}

            if not memory_ids:
                return {"error": "未提供有效的记忆ID"}

            async with AsyncSessionLocal() as db:
                valid_memories = await memory_service.get_by_ids(
                    db, agent_id, memory_ids
                )
                valid_ids = {m.id for m in valid_memories}
                deleted_ids = []
                for mid in memory_ids:
                    if mid in valid_ids:
                        await memory_service.delete(db, mid)
                        deleted_ids.append(mid)
                await db.commit()
                return {
                    "success": True,
                    "deleted_count": len(deleted_ids),
                    "deleted_ids": deleted_ids,
                }

        async def get_memory(memory_ids: list[int]) -> dict:
            agent_id = get_agent_id()
            if not agent_id:
                return {"error": "无法获取Agent ID"}

            if not memory_ids:
                return {"error": "未提供有效的记忆ID"}

            async with AsyncSessionLocal() as db:
                memories = await memory_service.get_by_ids(db, agent_id, memory_ids)
                items = [_format_memory(m) for m in memories]
                return {"results": items, "total": len(items)}

        return [
            StructuredTool(
                name="memory_save",
                description=_build_save_description(cfg),
                func=None,
                coroutine=save_memory,
                args_schema=MemorySaveInput,
            ),
            StructuredTool(
                name="memory_consolidate",
                description=_build_consolidate_description(cfg),
                func=None,
                coroutine=consolidate_memory,
                args_schema=ConsolidateInput,
            ),
            StructuredTool(
                name="memory_search",
                description=_build_search_description(cfg),
                func=None,
                coroutine=search_memory,
                args_schema=MemorySearchInput,
            ),
            StructuredTool(
                name="memory_list",
                description=_build_list_description(cfg),
                func=None,
                coroutine=list_memory,
                args_schema=MemoryListInput,
            ),
            StructuredTool(
                name="memory_delete",
                description=_build_delete_description(cfg),
                func=None,
                coroutine=delete_memory,
                args_schema=MemoryDeleteInput,
            ),
            StructuredTool(
                name="memory_get",
                description=_build_get_description(cfg),
                func=None,
                coroutine=get_memory,
                args_schema=MemoryGetInput,
            ),
        ]

    @classmethod
    def tool_info(cls, node: "FlowNode") -> list[dict]:
        """从描述构建函数派生（单源：与 build_tools 产出逐字节一致）

        动态描述：读取节点配置渲染阈值/分类等值（纯读 node，零 DB）。
        """
        from app.agent_flow.node_handlers.memory_handler import MemoryNodeConfig

        cfg = MemoryNodeConfig(**(node.base_config or {}))
        return [
            {"name": name, "description": builder(cfg)}
            for name, builder in _MEMORY_DESCRIPTION_BUILDERS.items()
        ]

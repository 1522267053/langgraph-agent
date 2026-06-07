"""
记忆服务

三层记忆架构：
- hot（热）：常驻 system_prompt 的指针索引，通过 get_hot_index 获取
- warm（温）：按需向量检索的详细记忆
- cold（冷）：低优先级记忆，可被自动升温

向量语义检索：保存时自动向量化，搜索时结合向量相似度。
自动升温：access_count 达阈值后自动 cold→warm→hot。
自动降温：久未访问的记忆按时间衰减（hot→warm→cold），importance 越高衰减越慢。
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, List, Tuple

from sqlalchemy import select, and_, Select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import Memory, MemoryType
from app.models.base_model import DbBaseModel
from app.schemas.memory_schema import MemoryCreate, MemoryUpdate
from app.services.base_service import BaseService
from app.services.embedding_service import get_embedding_service_async
from app.services.vector_store_service import ChromaVectorStoreService

logger = logging.getLogger(__name__)

MEMORY_COLLECTION_NAME = "memory_vectors"
_vector_store: Optional[ChromaVectorStoreService] = None
_vector_store_lock = threading.Lock()


DECAY_DEDUP_INTERVAL = 30.0
_last_decay_times: dict[int, float] = {}


def _get_vector_store() -> ChromaVectorStoreService:
    global _vector_store
    if _vector_store is None:
        with _vector_store_lock:
            if _vector_store is None:
                _vector_store = ChromaVectorStoreService(
                    collection_name=MEMORY_COLLECTION_NAME
                )
    return _vector_store


class MemoryService(BaseService[Memory, MemoryCreate, MemoryUpdate]):
    def __init__(self):
        super().__init__(Memory)

    def _apply_filters(
        self,
        query: Optional[Select],
        count_query: Optional[Select],
        condition: Optional[DbBaseModel],
    ) -> Tuple[Optional[Select], Optional[Select]]:
        query, count_query = super()._apply_filters(query, count_query, condition)

        if not condition:
            return query, count_query

        if hasattr(condition, "title") and condition.title:
            query, count_query = self._apply_like_filter(
                query, count_query, "title", condition.title
            )

        if hasattr(condition, "keywords") and condition.keywords:
            query, count_query = self._apply_like_filter(
                query, count_query, "keywords", condition.keywords
            )

        return query, count_query

    # ---- 记忆衰减（自动降温） ----

    async def decay_stale_memories(
        self,
        db: AsyncSession,
        agent_id: int,
        hot_decay_days: int = 30,
        warm_decay_days: int = 60,
    ) -> int:
        """将久未访问的记忆自动降级

        衰减规则（基于 last_access_time，无访问记录时退化为 modify_time）：
        - hot → warm：超过 hot_decay_days 天未被访问
        - warm → cold：超过 warm_decay_days 天未被访问
        - importance 加成：(importance - 1) * 10 天，高重要度记忆衰减更慢

        同一 agent 在 DECAY_DEDUP_INTERVAL（30s）内不重复执行。

        Returns:
            衰减的记忆数量
        """
        now_ts = time.time()
        last_ts = _last_decay_times.get(agent_id, 0)
        if now_ts - last_ts < DECAY_DEDUP_INTERVAL:
            return 0
        _last_decay_times[agent_id] = now_ts

        now = datetime.now()
        decayed_count = 0

        hot_cutoff = datetime(now.year, now.month, now.day) - timedelta(
            days=hot_decay_days
        )
        warm_cutoff = datetime(now.year, now.month, now.day) - timedelta(
            days=warm_decay_days
        )

        stmt = select(Memory).where(
            and_(
                Memory.agent_id == agent_id,
                Memory.memory_type.in_([MemoryType.HOT.value, MemoryType.WARM.value]),
            )
        )
        result = await db.execute(stmt, execution_options={"include_deleted": False})
        memories = result.scalars().all()

        for memory in memories:
            access_time = memory.last_access_time or memory.modify_time
            if not access_time:
                continue

            bonus = ((memory.importance or 1) - 1) * 10

            if (
                memory.memory_type == MemoryType.HOT.value
                and access_time < hot_cutoff - timedelta(days=bonus)
            ):
                memory.memory_type = MemoryType.WARM.value
                memory.access_count = 0
                decayed_count += 1
            elif (
                memory.memory_type == MemoryType.WARM.value
                and access_time < warm_cutoff - timedelta(days=bonus)
            ):
                memory.memory_type = MemoryType.COLD.value
                memory.access_count = 0
                decayed_count += 1

        if decayed_count > 0:
            await db.commit()
            logger.info(f"记忆衰减完成: agent_id={agent_id}, decayed={decayed_count}")

        return decayed_count

    # ---- 记忆保存 ----

    TITLE_MAX_LENGTH = 50
    CONTENT_MAX_LENGTH = 500

    async def save_memory(
        self,
        db: AsyncSession,
        agent_id: int,
        title: str,
        content: str,
        memory_type: str = MemoryType.COLD.value,
        category: str = "event",
        importance: int = 3,
        keywords: Optional[str] = None,
        source_session_id: Optional[str] = None,
        hot_decay_days: int = 30,
        warm_decay_days: int = 60,
        skip_decay: bool = False,
        skip_vectorize: bool = False,
    ) -> Memory:
        """保存记忆并自动向量化

        Args:
            skip_decay: 调用方已执行过衰减检查时设为 True，避免重复执行
            skip_vectorize: 跳过单条向量化，由调用方统一批量处理
        """
        if not skip_decay:
            await self.decay_stale_memories(
                db,
                agent_id,
                hot_decay_days=hot_decay_days,
                warm_decay_days=warm_decay_days,
            )
        if len(title) > self.TITLE_MAX_LENGTH:
            raise ValueError(
                f"记忆标题不可超过 {self.TITLE_MAX_LENGTH} 字符（当前 {len(title)} 字符）"
            )
        if len(content) > self.CONTENT_MAX_LENGTH:
            raise ValueError(
                f"记忆内容不可超过 {self.CONTENT_MAX_LENGTH} 字符（当前 {len(content)} 字符）"
            )
        memory = Memory(
            agent_id=agent_id,
            title=title,
            content=content,
            category=category,
            importance=importance,
            keywords=keywords,
            source_session_id=source_session_id,
            memory_type=memory_type,
            peak_tier=memory_type,
            last_access_time=datetime.now(),
        )

        self._set_creator_fields(memory)
        memory.is_delete = 0

        db.add(memory)
        await db.commit()
        await db.refresh(memory)

        if not skip_vectorize:
            await self._vectorize_memory(memory)

        logger.info(
            f"记忆已保存: agent_id={agent_id}, tier={memory_type}, "
            f"title={title}, category={category}"
        )

        return memory

    # ---- 热记忆索引 ----

    async def get_hot_memories(self, db: AsyncSession, agent_id: int) -> List[Memory]:
        """获取所有热记忆，按 category 分组后组内按 importance 降序"""
        stmt = (
            select(Memory)
            .where(
                and_(
                    Memory.agent_id == agent_id,
                    Memory.memory_type == MemoryType.HOT.value,
                )
            )
            .order_by(Memory.importance.desc(), Memory.access_count.desc())
        )

        result = await db.execute(stmt, execution_options={"include_deleted": False})
        return list(result.scalars().all())

    @staticmethod
    def build_memory_index(memories: List[Memory]) -> str:
        """将热记忆列表格式化为紧凑索引文本，按 importance 降序排列"""
        sorted_memories = sorted(
            memories, key=lambda m: m.importance or 3, reverse=True
        )
        count = len(sorted_memories)

        lines = [f"[记忆索引] {count}条 | 用 memory_get <ID> 查详情", ""]

        for m in sorted_memories:
            importance = m.importance or 3
            title = m.title or ""
            content_preview = (m.content or "")[:50]
            detail = f"{title}：{content_preview}..." if content_preview else title
            lines.append(f"P{importance} [ID:{m.id}] | {detail} | {m.category}")

        return "\n".join(lines)

    @staticmethod
    def truncate_index(raw: str, max_lines: int = 200, max_bytes: int = 25000) -> str:
        """双重截断保护：先按行数，再按字节数

        在最后一个换行符处切开避免截断到一半，
        截断后追加 WARNING 让 LLM 知道索引不完整。
        """
        content_lines = raw.split("\n")
        was_line_truncated = len(content_lines) > max_lines

        if was_line_truncated:
            truncated = "\n".join(content_lines[:max_lines])
        else:
            truncated = raw.strip()

        if len(truncated.encode("utf-8")) > max_bytes:
            cut_at = truncated.rfind("\n", max_lines, max_bytes)
            if cut_at <= 0:
                cut_at = max_bytes
            truncated = truncated[:cut_at]
            was_line_truncated = True

        if was_line_truncated:
            truncated += (
                "\n\n> WARNING: 记忆索引过大，仅部分加载。"
                "请使用 memory_search 工具检索完整记忆。"
            )

        return truncated

    async def get_hot_index(
        self,
        db: AsyncSession,
        agent_id: int,
        max_lines: int = 200,
        max_bytes: int = 25000,
    ) -> str:
        """获取热记忆索引文本（含截断保护），同时刷新最后访问时间"""
        memories = await self.get_hot_memories(db, agent_id)
        if not memories:
            return ""

        now = datetime.now()
        for m in memories:
            m.last_access_time = now
        await db.commit()

        raw_index = self.build_memory_index(memories)
        return self.truncate_index(raw_index, max_lines, max_bytes)

    async def get_last_consolidate_time(
        self, db: AsyncSession, agent_id: int
    ) -> Optional[float]:
        """获取该 agent 热记忆的最大 modify_time 作为上次整理时间的近似值

        consolidate 会全量替换热记忆，新记忆的 modify_time 就是整理时间。
        如果没有热记忆返回 None。
        """
        from sqlalchemy import func

        stmt = select(func.max(Memory.modify_time)).where(
            and_(
                Memory.agent_id == agent_id,
                Memory.memory_type == MemoryType.HOT.value,
            )
        )
        result = await db.execute(stmt, execution_options={"include_deleted": False})
        max_time = result.scalar()
        if not max_time:
            return None
        return max_time.timestamp()

    async def get_hot_count(self, db: AsyncSession, agent_id: int) -> int:
        """获取热记忆数量"""
        from sqlalchemy import func

        stmt = select(func.count(Memory.id)).where(
            and_(
                Memory.agent_id == agent_id,
                Memory.memory_type == MemoryType.HOT.value,
            )
        )
        result = await db.execute(stmt, execution_options={"include_deleted": False})
        return result.scalar() or 0

    async def get_tier_stats(self, db: AsyncSession, agent_id: int) -> dict:
        """获取各层级记忆总数（hot / warm / cold）"""
        from sqlalchemy import func

        stmt = (
            select(Memory.memory_type, func.count(Memory.id))
            .where(Memory.agent_id == agent_id)
            .group_by(Memory.memory_type)
        )
        result = await db.execute(stmt, execution_options={"include_deleted": False})
        rows = result.all()
        stats = {"hot": 0, "warm": 0, "cold": 0}
        for memory_type, count in rows:
            if memory_type in stats:
                stats[memory_type] = count
        return stats

    async def get_all_hot_memories_full(
        self, db: AsyncSession, agent_id: int
    ) -> List[dict]:
        """获取所有热记忆的完整内容，用于交给 LLM 总结"""
        memories = await self.get_hot_memories(db, agent_id)
        return [
            {
                "id": m.id,
                "title": m.title,
                "content": m.content,
                "category": m.category,
                "importance": m.importance,
                "keywords": m.keywords,
            }
            for m in memories
        ]

    async def consolidate_hot_memories(
        self, db: AsyncSession, agent_id: int, new_memories: List[dict]
    ) -> dict:
        """全量替换热记忆：软删除旧热记忆 → 批量保存新热记忆

        新记忆的 importance/keywords 优先从 LLM 输出中取，
        缺失时从旧记忆按 title 匹配兜底继承。
        向量化在同一个事务完成后批量执行。
        """
        old_memories = await self.get_hot_memories(db, agent_id)
        old_count = len(old_memories)

        title_to_old = {m.title: m for m in old_memories}

        for old_m in old_memories:
            if old_m.vector_id:
                try:
                    vector_store = _get_vector_store()
                    await vector_store.delete([old_m.vector_id])
                except Exception:
                    logger.warning(f"删除旧记忆向量失败: memory_id={old_m.id}")
            old_m.is_delete = 1
            self._set_modifier_fields(old_m)

        await db.flush()

        now = datetime.now()
        new_memory_objects: List[Memory] = []
        results = []
        for item in new_memories:
            title = (item.get("title") or "").strip()
            content = (item.get("content") or "").strip()
            if not title or not content:
                continue
            if (
                len(title) > self.TITLE_MAX_LENGTH
                or len(content) > self.CONTENT_MAX_LENGTH
            ):
                logger.warning(
                    f"整理时跳过超长记忆: title={title[:50]}, "
                    f"title_len={len(title)}, content_len={len(content)}"
                )
                continue

            importance = item.get("importance", 5)
            if not isinstance(importance, int) or importance < 1 or importance > 5:
                importance = 5

            keywords = item.get("keywords")
            if not keywords and title in title_to_old:
                keywords = title_to_old[title].keywords

            memory = Memory(
                agent_id=agent_id,
                title=title,
                content=content,
                memory_type=MemoryType.HOT.value,
                peak_tier=MemoryType.HOT.value,
                category=item.get("category", "other"),
                importance=importance,
                keywords=keywords,
                last_access_time=now,
            )
            self._set_creator_fields(memory)
            memory.is_delete = 0
            db.add(memory)
            await db.flush()
            new_memory_objects.append(memory)
            results.append(
                {"id": memory.id, "title": memory.title, "tier": memory.memory_type}
            )

        await db.commit()

        await self._vectorize_memories_batch(new_memory_objects, db=db)

        logger.info(
            f"热记忆整理完成: agent_id={agent_id}, {old_count}条 → {len(results)}条"
        )

        return {"old_count": old_count, "new_count": len(results), "results": results}

    # ---- 自动升温 ----

    @staticmethod
    def infer_tier(importance: int) -> str:
        """根据 importance 推断记忆层级"""
        if importance >= 5:
            return MemoryType.HOT.value
        elif importance >= 3:
            return MemoryType.WARM.value
        else:
            return MemoryType.COLD.value

    _TIER_ORDER = [
        MemoryType.COLD.value,
        MemoryType.WARM.value,
        MemoryType.HOT.value,
    ]

    async def increment_access(
        self, db: AsyncSession, memory_id: int, threshold: int = 5
    ) -> None:
        """访问计数+1，达阈值自动升温

        升温规则：
        - cold 记忆 access_count 达阈值 → warm
        - warm 记忆 access_count 再次达阈值（重置后）→ hot
        - 曾为 hot 的记忆（peak_tier == hot）使用更低阈值（1 次）快速回升
        """
        memory = await self.get_by_id(db, memory_id)
        if not memory:
            return

        memory.access_count = (memory.access_count or 0) + 1
        memory.last_access_time = datetime.now()
        current_count = memory.access_count

        promoted = False

        # 曾为 hot 的记忆，1 次命中即可升温
        effective_threshold = (
            1 if memory.peak_tier == MemoryType.HOT.value else threshold
        )

        if (
            memory.memory_type == MemoryType.COLD.value
            and current_count >= effective_threshold
        ):
            memory.memory_type = MemoryType.WARM.value
            memory.access_count = 0
            promoted = True

        elif (
            memory.memory_type == MemoryType.WARM.value
            and current_count >= effective_threshold
        ):
            memory.memory_type = MemoryType.HOT.value
            memory.access_count = 0
            promoted = True

        if promoted:
            # 更新 peak_tier 为当前最高层级
            current_idx = self._TIER_ORDER.index(memory.memory_type)
            peak_idx = self._TIER_ORDER.index(memory.peak_tier)
            if current_idx > peak_idx:
                memory.peak_tier = memory.memory_type
            self._set_modifier_fields(memory)
            await db.commit()
            logger.info(
                f"记忆自动升温: id={memory_id}, "
                f"new_tier={memory.memory_type}, title={memory.title}"
            )
        else:
            await db.commit()

    async def promote_memory(
        self, db: AsyncSession, memory_id: int, target_tier: str
    ) -> Optional[Memory]:
        """手动将记忆提升到指定层级（cold→warm 或 warm→hot）"""
        memory = await self.get_by_id(db, memory_id)
        if not memory:
            return None

        current_idx = (
            self._TIER_ORDER.index(memory.memory_type)
            if memory.memory_type in self._TIER_ORDER
            else -1
        )
        target_idx = (
            self._TIER_ORDER.index(target_tier)
            if target_tier in self._TIER_ORDER
            else -1
        )

        if target_idx <= current_idx:
            return memory

        memory.memory_type = target_tier
        memory.access_count = 0
        # 更新 peak_tier
        if target_idx > self._TIER_ORDER.index(memory.peak_tier):
            memory.peak_tier = target_tier
        self._set_modifier_fields(memory)
        await db.commit()
        await db.refresh(memory)

        logger.info(
            f"记忆手动升温: id={memory_id}, "
            f"tier={memory.memory_type}, title={memory.title}"
        )
        return memory

    async def demote_memory(self, db: AsyncSession, memory_id: int) -> Optional[Memory]:
        """将热记忆降级为温记忆（LLM 手动操作）"""
        memory = await self.get_by_id(db, memory_id)
        if not memory:
            return None

        if memory.memory_type == MemoryType.HOT.value:
            memory.memory_type = MemoryType.WARM.value
            memory.access_count = 0
            self._set_modifier_fields(memory)
            await db.commit()
            await db.refresh(memory)
            logger.info(f"记忆降级: id={memory_id}, tier=warm, title={memory.title}")

        return memory

    async def demote_low_value_hot_memories(
        self, db: AsyncSession, agent_id: int, target_count: int
    ) -> int:
        """选择性降级低价值热记忆

        按 importance ASC、access_count ASC、create_time ASC 排序，
        将末尾低价值记忆降为 warm，直到热记忆总数 <= target_count。

        Returns:
            降级的记忆数量
        """
        stmt = (
            select(Memory)
            .where(
                and_(
                    Memory.agent_id == agent_id,
                    Memory.memory_type == MemoryType.HOT.value,
                )
            )
            .order_by(
                Memory.importance.asc(),
                Memory.access_count.asc(),
                Memory.create_time.asc(),
            )
        )

        result = await db.execute(stmt, execution_options={"include_deleted": False})
        all_hot = list(result.scalars().all())

        if len(all_hot) <= target_count:
            return 0

        demote_count = len(all_hot) - target_count
        for memory in all_hot[:demote_count]:
            memory.memory_type = MemoryType.WARM.value
            self._set_modifier_fields(memory)

        await db.commit()
        logger.info(
            f"选择性降级热记忆: agent_id={agent_id}, "
            f"demoted={demote_count}, remaining={target_count}"
        )
        return demote_count

    async def get_warm_for_promote(
        self,
        db: AsyncSession,
        agent_id: int,
        limit: int = 20,
        categories: Optional[List[str]] = None,
    ) -> List[Memory]:
        """获取待升温的温记忆（高 access_count 优先）"""
        conditions = [
            Memory.agent_id == agent_id,
            Memory.memory_type == MemoryType.WARM.value,
        ]
        if categories:
            conditions.append(Memory.category.in_(categories))

        stmt = (
            select(Memory)
            .where(and_(*conditions))
            .order_by(Memory.access_count.desc(), Memory.importance.desc())
            .limit(limit)
        )

        result = await db.execute(stmt, execution_options={"include_deleted": False})
        return list(result.scalars().all())

    async def get_by_ids(
        self, db: AsyncSession, agent_id: int, memory_ids: List[int]
    ) -> List[Memory]:
        """根据 ID 列表批量获取记忆（按 ID 排序，仅返回属于该 agent 的记录）"""
        if not memory_ids:
            return []
        stmt = (
            select(Memory)
            .where(Memory.agent_id == agent_id, Memory.id.in_(memory_ids))
            .order_by(Memory.id)
        )
        result = await db.execute(stmt, execution_options={"include_deleted": False})
        return list(result.scalars().all())

    # ---- 向量检索 ----

    async def search(
        self,
        db: AsyncSession,
        agent_id: int,
        query: str,
        tier: Optional[str] = None,
        categories: Optional[List[str]] = None,
        max_results: int = 5,
        min_score: float = 0.0,
        hot_decay_days: int = 30,
        warm_decay_days: int = 60,
    ) -> List[Tuple[Memory, float]]:
        """搜索记忆：向量可用时用向量搜索，否则降级为 SQL 关键词匹配"""
        await self.decay_stale_memories(
            db, agent_id, hot_decay_days=hot_decay_days, warm_decay_days=warm_decay_days
        )

        emb_svc = await get_embedding_service_async()
        if emb_svc.is_available():
            return await self._vector_search_memories(
                db, agent_id, query, tier, categories, max_results, min_score
            )
        else:
            return await self._keyword_search_memories(
                db, agent_id, query, tier, categories, max_results
            )

    async def _vector_search_memories(
        self,
        db: AsyncSession,
        agent_id: int,
        query: str,
        tier: Optional[str],
        categories: Optional[List[str]],
        max_results: int,
        min_score: float,
    ) -> List[Tuple[Memory, float]]:
        """向量语义搜索"""
        scored_ids = await self._vector_search(
            agent_id, query, max_results * 2, min_score
        )
        if not scored_ids:
            return []
        ids = [mid for mid, _ in scored_ids]
        stmt = select(Memory).where(Memory.id.in_(ids))
        if tier:
            stmt = stmt.where(Memory.memory_type == tier)
        if categories:
            stmt = stmt.where(Memory.category.in_(categories))
        result = await db.execute(stmt, execution_options={"include_deleted": False})
        id_to_memory = {m.id: m for m in result.scalars().all()}
        ordered: List[Tuple[Memory, float]] = []
        for mid, score in scored_ids:
            m = id_to_memory.get(mid)
            if m:
                ordered.append((m, score))
            if len(ordered) >= max_results:
                break
        return ordered

    async def _keyword_search_memories(
        self,
        db: AsyncSession,
        agent_id: int,
        query: str,
        tier: Optional[str],
        categories: Optional[List[str]],
        max_results: int,
    ) -> List[Tuple[Memory, float]]:
        """SQL 关键词 fallback 搜索"""
        from sqlalchemy import or_

        conditions = [Memory.agent_id == agent_id]
        if tier:
            conditions.append(Memory.memory_type == tier)
        if categories:
            conditions.append(Memory.category.in_(categories))

        keywords = query.strip().split()
        for kw in keywords[:5]:
            conditions.append(
                or_(
                    Memory.title.contains(kw),
                    Memory.content.contains(kw),
                )
            )

        stmt = (
            select(Memory)
            .where(and_(*conditions))
            .order_by(Memory.importance.desc(), Memory.create_time.desc())
            .limit(max_results)
        )
        result = await db.execute(stmt)
        memories = list(result.scalars().all())
        return [(m, 0.5) for m in memories]

    async def _vector_search(
        self, agent_id: int, query: str, top_k: int, min_score: float = 0.0
    ) -> List[Tuple[int, float]]:
        """向量相似度搜索"""
        try:
            embedding_service = await get_embedding_service_async()
            vector_store = _get_vector_store()

            query_embedding = await embedding_service.embed_query(query)

            results = await vector_store.similarity_search(
                query_embedding=query_embedding,
                k=top_k,
                filter={"agent_id": agent_id},
            )

            scored: List[Tuple[int, float]] = []
            for r in results:
                distance = r.get("distance", 1.0)
                score = round(1 - distance, 4)
                if score < min_score:
                    continue
                memory_id = r.get("metadata", {}).get("memory_id")
                if memory_id:
                    scored.append((memory_id, score))

            return scored
        except Exception as e:
            logger.warning(f"记忆向量搜索失败: {e}")
            return []

    # ---- 记忆列表 ----

    async def get_recent(
        self,
        db: AsyncSession,
        agent_id: int,
        limit: int = 10,
        tier: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[Memory]:
        """获取最近记忆列表"""
        conditions = [Memory.agent_id == agent_id]

        if tier:
            conditions.append(Memory.memory_type == tier)

        if category:
            conditions.append(Memory.category == category)

        stmt = (
            select(Memory)
            .where(and_(*conditions))
            .order_by(Memory.create_time.desc())
            .limit(limit)
        )

        result = await db.execute(stmt, execution_options={"include_deleted": False})
        return list(result.scalars().all())

    async def get_by_agent(self, db: AsyncSession, agent_id: int) -> List[Memory]:
        stmt = (
            select(Memory)
            .where(Memory.agent_id == agent_id)
            .order_by(Memory.create_time.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    # ---- 删除与向量化 ----

    async def delete(self, db: AsyncSession, id: int) -> None:
        db_obj = await self.get_by_id(db, id)
        if not db_obj:
            return

        if db_obj.vector_id:
            try:
                vector_store = _get_vector_store()
                await vector_store.delete([db_obj.vector_id])
            except Exception:
                logger.warning(
                    f"删除记忆向量失败: memory_id={id}, vector_id={db_obj.vector_id}"
                )

        if hasattr(db_obj, "is_delete"):
            setattr(db_obj, "is_delete", 1)
            self._set_modifier_fields(db_obj)
            await db.commit()
        else:
            await db.delete(db_obj)
            await db.commit()

    async def revectorize(
        self, db: AsyncSession, agent_id: int, memory_ids: List[int]
    ) -> dict:
        """批量重新向量化指定记忆

        先删除旧向量，再批量生成新向量。返回处理结果统计。
        """
        if not memory_ids:
            return {"total": 0, "success": 0, "failed": 0, "failed_ids": []}

        memories = await self.get_by_ids(db, agent_id, memory_ids)
        if not memories:
            return {"total": 0, "success": 0, "failed": 0, "failed_ids": []}

        vector_store = _get_vector_store()

        old_vector_ids = [m.vector_id for m in memories if m.vector_id]
        if old_vector_ids:
            try:
                await vector_store.delete(old_vector_ids)
            except Exception as e:
                logger.warning(f"批量删除旧记忆向量失败: {e}")

        for m in memories:
            m.vector_id = None
        await db.flush()

        success_count, failed_ids = await self._vectorize_memories_batch(
            memories, db=db
        )

        return {
            "total": len(memory_ids),
            "success": success_count,
            "failed": len(memories) - success_count,
            "failed_ids": failed_ids,
        }

    async def _vectorize_memories_batch(
        self, memories: List[Memory], db: Optional[AsyncSession] = None
    ) -> Tuple[int, List[int]]:
        """批量向量化多条记忆，一次性 embed + 一次性写入 ChromaDB

        Returns:
            (success_count, failed_ids)
        """
        emb_svc = await get_embedding_service_async()
        if not emb_svc.is_available() or not memories:
            return 0, [m.id for m in memories] if memories else (0, [])

        vector_store = _get_vector_store()

        texts = []
        valid_memories: List[Memory] = []
        for m in memories:
            text = f"{m.title}。{m.content}"
            if not text.strip():
                continue
            texts.append(text)
            valid_memories.append(m)

        if not texts:
            return 0, [m.id for m in memories]

        try:
            embeddings = await emb_svc.embed_texts(texts)
        except Exception as e:
            logger.warning(f"批量记忆向量化 embed 失败: {e}")
            return 0, [m.id for m in memories]

        if len(embeddings) != len(texts):
            logger.warning(
                f"批量 embed 返回数量不匹配: {len(embeddings)} != {len(texts)}"
            )
            return 0, [m.id for m in memories]

        metadatas = [
            {"agent_id": m.agent_id, "memory_id": m.id} for m in valid_memories
        ]
        ids = [f"memory_{m.id}" for m in valid_memories]

        try:
            await vector_store.add_texts(
                texts=texts,
                embeddings=embeddings,
                metadatas=metadatas,
                ids=ids,
            )
        except Exception as e:
            logger.warning(f"批量记忆写入 ChromaDB 失败: {e}")
            return 0, [m.id for m in memories]

        failed_ids: List[int] = []
        for i, m in enumerate(valid_memories):
            m.vector_id = ids[i]

        if db:
            await db.commit()
        else:
            from app.config.database import AsyncSessionLocal

            async with AsyncSessionLocal() as new_db:
                for m in valid_memories:
                    await new_db.merge(m)
                await new_db.commit()

        logger.info(
            f"批量记忆向量化完成: {len(valid_memories)} 条, 失败 {len(failed_ids)} 条"
        )
        return len(valid_memories), failed_ids

    async def _vectorize_memory(
        self, memory: Memory, db: Optional[AsyncSession] = None
    ) -> None:
        """将单条记忆内容向量化并存入向量库（兼容旧调用）"""
        emb_svc = await get_embedding_service_async()
        if not emb_svc.is_available():
            return

        try:
            vector_store = _get_vector_store()

            text = f"{memory.title}。{memory.content}"
            embeddings = await emb_svc.embed_texts([text])

            vector_id = f"memory_{memory.id}"
            await vector_store.add_texts(
                texts=[text],
                embeddings=embeddings,
                metadatas=[{"agent_id": memory.agent_id, "memory_id": memory.id}],
                ids=[vector_id],
            )

            memory.vector_id = vector_id
            if db:
                await db.merge(memory)
                await db.commit()
            else:
                from app.config.database import AsyncSessionLocal

                async with AsyncSessionLocal() as new_db:
                    await new_db.merge(memory)
                    await new_db.commit()
        except Exception as e:
            logger.warning(f"记忆向量化失败: memory_id={memory.id}, error={e}")


memory_service = MemoryService()

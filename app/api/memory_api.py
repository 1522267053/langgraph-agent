"""
记忆 API 路由
"""

from datetime import datetime

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.base_api import BaseApi
from app.config.database import get_db
from app.models.memory import Memory
from app.schemas.base_schema import ApiResponse
from app.services.memory_service import memory_service
from app.schemas.memory_schema import (
    MemoryView,
    MemoryCondition,
    MemoryCreate,
    MemoryUpdate,
    MemoryExportRequest,
    MemoryImportRequest,
    MemorySearchRequest,
)


class MemoryApi(
    BaseApi[Memory, MemoryView, MemoryCondition, MemoryCreate, MemoryUpdate]
):
    def __init__(self):
        super().__init__(
            service=memory_service,
            router_prefix="/api/memory",
            router_tags=["记忆管理"],
        )

        @self.router.post(
            "/revectorize", response_model=ApiResponse, summary="批量重新向量化记忆"
        )
        async def revectorize_memories(
            body: dict = ...,
            db: AsyncSession = Depends(get_db),
        ):
            """批量重新向量化指定记忆，先删除旧向量再重新生成"""
            ids = body.get("ids", [])
            agent_id = body.get("agent_id")
            result = await memory_service.revectorize(db, agent_id, ids)
            return ApiResponse.success(
                data=result,
                msg=f"向量化完成：成功 {result['success']} 条，失败 {result['failed']} 条",
            )

        @self.router.post("/search", response_model=ApiResponse, summary="语义搜索记忆")
        async def search_memories(
            body: MemorySearchRequest,
            db: AsyncSession = Depends(get_db),
        ):
            """向量语义搜索记忆，embedding 不可用时降级为 SQL 关键词匹配"""
            results = await memory_service.search(
                db,
                agent_id=body.agent_id,
                query=body.query,
                tier=body.tier,
                max_results=body.max_results,
            )
            items = [
                {
                    "memory": MemoryView.model_to_view(m).model_dump(),
                    "score": round(score, 4),
                }
                for m, score in results
            ]
            return ApiResponse.success(
                data={"items": items}, msg=f"搜索到 {len(items)} 条记忆"
            )

        @self.router.post("/export", response_model=ApiResponse, summary="导出记忆")
        async def export_memories(
            body: MemoryExportRequest,
            db: AsyncSession = Depends(get_db),
        ):
            """导出 Agent 记忆为 JSON"""
            if body.ids:
                raw = await memory_service.get_by_ids(db, body.agent_id, body.ids)
            else:
                raw = await memory_service.get_by_agent(db, body.agent_id)

            if body.tier:
                raw = [m for m in raw if m.memory_type == body.tier]

            items = [
                {
                    "title": m.title,
                    "content": m.content,
                    "memory_type": m.memory_type,
                    "category": m.category,
                    "importance": m.importance,
                    "keywords": m.keywords,
                }
                for m in raw
            ]

            return ApiResponse.success(
                data={
                    "agent_id": body.agent_id,
                    "export_time": datetime.now().isoformat(),
                    "total": len(items),
                    "memories": items,
                },
                msg=f"导出 {len(items)} 条记忆",
            )

        @self.router.post("/import", response_model=ApiResponse, summary="导入记忆")
        async def import_memories(
            body: MemoryImportRequest,
            db: AsyncSession = Depends(get_db),
        ):
            """从 JSON 导入记忆到指定 Agent（批量写入 DB 后统一向量化）"""
            total = len(body.memories)
            imported = 0
            failed = 0
            errors: list[dict] = []
            saved_memories: list[Memory] = []

            for i, item in enumerate(body.memories):
                try:
                    memory = await memory_service.save_memory(
                        db,
                        agent_id=body.agent_id,
                        title=item.title,
                        content=item.content,
                        memory_type=item.memory_type,
                        category=item.category,
                        importance=item.importance,
                        keywords=item.keywords,
                        skip_decay=i > 0,
                        skip_vectorize=True,
                    )
                    saved_memories.append(memory)
                    imported += 1
                except Exception as e:
                    failed += 1
                    errors.append({"index": i, "title": item.title, "error": str(e)})

            if saved_memories:
                success, _ = await memory_service._vectorize_memories_batch(
                    saved_memories, db=db
                )
                vectorize_failed = len(saved_memories) - success
                if vectorize_failed:
                    imported -= vectorize_failed
                    failed += vectorize_failed

            return ApiResponse.success(
                data={
                    "total": total,
                    "imported": imported,
                    "failed": failed,
                    "errors": errors,
                },
                msg=f"导入 {imported} 条记忆"
                + (f"，{failed} 条失败" if failed else ""),
            )

        @self.router.get(
            "/stats", response_model=ApiResponse, summary="获取各层级记忆数量"
        )
        async def get_tier_stats(
            agent_id: int = Query(..., description="Agent ID"),
            db: AsyncSession = Depends(get_db),
        ):
            """返回 hot/warm/cold 各层级记忆总数"""
            stats = await memory_service.get_tier_stats(db, agent_id)
            return ApiResponse.success(data=stats)


memory_api = MemoryApi()
router = memory_api.router

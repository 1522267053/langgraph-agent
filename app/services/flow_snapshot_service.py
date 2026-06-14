"""
流程版本快照服务

提供快照的创建、恢复、列表查询、删除和自动清理功能。
"""

import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flow_snapshot import FlowSnapshot
from app.schemas.flow_snapshot_schema import (
    FlowSnapshotCreate,
    FlowSnapshotUpdate,
)
from app.services.base_service import BaseService

logger = logging.getLogger(__name__)

# 自动快照保留数量上限
MAX_AUTO_SNAPSHOTS = 20

# 快照中排除的字段（内部字段，不需要序列化）
_SNAPSHOT_EXCLUDE_FIELDS = frozenset(
    {
        "id",
        "flow_id",
        "creator_id",
        "creator_type",
        "creator_name",
        "create_time",
        "modifier_id",
        "modifier_type",
        "modifier_name",
        "modify_time",
        "is_delete",
        "is_builtin",
        "saved_as_card",
        "status",
    }
)

_NODE_EXCLUDE_FIELDS = frozenset(
    {
        "id",
        "flow_id",
        "creator_id",
        "creator_type",
        "creator_name",
        "create_time",
        "modifier_id",
        "modifier_type",
        "modifier_name",
        "modify_time",
        "is_delete",
    }
)

_EDGE_EXCLUDE_FIELDS = frozenset(
    {
        "id",
        "flow_id",
        "creator_id",
        "creator_type",
        "creator_name",
        "create_time",
        "modifier_id",
        "modifier_type",
        "modifier_name",
        "modify_time",
        "is_delete",
    }
)


class FlowSnapshotService(
    BaseService[FlowSnapshot, FlowSnapshotCreate, FlowSnapshotUpdate]
):
    """流程版本快照服务"""

    def __init__(self):
        super().__init__(FlowSnapshot)

    # ---- 创建快照 ----

    async def create_snapshot(
        self,
        db: AsyncSession,
        flow_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        snapshot_type: str = "auto",
    ) -> Optional[FlowSnapshot]:
        """创建快照，序列化当前流程的完整拓扑

        Args:
            db: 数据库异步会话
            flow_id: 流程ID
            name: 快照名称（为空时自动生成）
            description: 快照描述
            snapshot_type: auto/manual

        Returns:
            快照对象，流程不存在时返回 None
        """
        from app.services.flow_service import flow_service

        flow = await flow_service.get_with_nodes_and_edges(db, flow_id)
        if not flow:
            return None

        snapshot_data = self._serialize_flow(flow)

        snapshot_name = (
            name or f"自动快照 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        snapshot = FlowSnapshot(
            flow_id=flow_id,
            snapshot_name=snapshot_name,
            snapshot_description=description,
            snapshot_data=snapshot_data,
            snapshot_type=snapshot_type,
            is_pinned=0,
        )
        db.add(snapshot)
        await db.commit()
        await db.refresh(snapshot)

        # 自动清理旧的 auto 快照
        if snapshot_type == "auto":
            await self._auto_cleanup(db, flow_id)

        return snapshot

    def _serialize_flow(self, flow) -> dict:
        """序列化流程为快照数据"""
        # Flow 元数据
        flow_meta = {}
        for col in flow.__table__.columns:
            key = col.name
            if key in _SNAPSHOT_EXCLUDE_FIELDS:
                continue
            flow_meta[key] = getattr(flow, key, None)

        # 节点列表
        nodes = []
        for node in flow.nodes or []:
            node_data = {}
            for col in node.__table__.columns:
                key = col.name
                if key in _NODE_EXCLUDE_FIELDS:
                    continue
                node_data[key] = getattr(node, key, None)
            nodes.append(node_data)

        # 边列表
        edges = []
        for edge in flow.edges or []:
            edge_data = {}
            for col in edge.__table__.columns:
                key = col.name
                if key in _EDGE_EXCLUDE_FIELDS:
                    continue
                edge_data[key] = getattr(edge, key, None)
            edges.append(edge_data)

        return {
            "flow_meta": flow_meta,
            "nodes": nodes,
            "edges": edges,
        }

    # ---- 恢复快照 ----

    async def restore_snapshot(
        self, db: AsyncSession, snapshot_id: int
    ) -> Optional[dict]:
        """恢复快照到对应流程

        使用 full_update_flow 全量替换流程的节点和边。

        Args:
            db: 数据库异步会话
            snapshot_id: 快照ID

        Returns:
            恢复后的流程名称，快照不存在时返回 None

        Raises:
            ValueError: 快照数据损坏
        """
        from app.services.flow_service import flow_service

        snapshot = await self.get_by_id(db, snapshot_id)
        if not snapshot or not snapshot.snapshot_data:
            return None

        data = snapshot.snapshot_data
        flow_meta = data.get("flow_meta", {})
        ai_nodes = data.get("nodes", [])
        ai_edges = data.get("edges", [])

        flow = await flow_service.full_update_flow(
            db,
            flow_id=snapshot.flow_id,
            name=flow_meta.get("name"),
            description=flow_meta.get("description"),
            input_schema=flow_meta.get("input_schema"),
            output_schema=flow_meta.get("output_schema"),
            ai_nodes=ai_nodes,
            ai_edges=ai_edges,
        )

        return {"flow_id": flow.id, "flow_name": flow.name}

    # ---- 列表查询 ----

    async def list_snapshots(
        self, db: AsyncSession, flow_id: int
    ) -> List[FlowSnapshot]:
        """获取流程的所有快照（按创建时间倒序）"""
        stmt = (
            select(FlowSnapshot)
            .where(FlowSnapshot.flow_id == flow_id, FlowSnapshot.is_delete == 0)
            .order_by(FlowSnapshot.id.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    # ---- 自动清理 ----

    async def _auto_cleanup(self, db: AsyncSession, flow_id: int) -> int:
        """清理旧的自动快照，保留最近 MAX_AUTO_SNAPSHOTS 个（is_pinned=1 不删）

        Returns:
            删除的快照数量
        """
        # 查询 auto 类型且未置顶的快照
        stmt = (
            select(FlowSnapshot.id)
            .where(
                FlowSnapshot.flow_id == flow_id,
                FlowSnapshot.snapshot_type == "auto",
                FlowSnapshot.is_pinned == 0,
                FlowSnapshot.is_delete == 0,
            )
            .order_by(FlowSnapshot.id.desc())
        )
        result = await db.execute(stmt)
        all_ids = [row[0] for row in result.all()]

        if len(all_ids) <= MAX_AUTO_SNAPSHOTS:
            return 0

        # 需要删除的多余快照（保留最近 MAX_AUTO_SNAPSHOTS 个）
        ids_to_delete = all_ids[MAX_AUTO_SNAPSHOTS:]
        if not ids_to_delete:
            return 0

        # 软删除（使用 update 设置 is_delete=1）
        from sqlalchemy import update as sql_update

        update_stmt = (
            sql_update(FlowSnapshot)
            .where(FlowSnapshot.id.in_(ids_to_delete))
            .values(is_delete=1)
        )
        await db.execute(update_stmt)
        await db.commit()

        logger.info(f"自动清理流程 {flow_id} 的 {len(ids_to_delete)} 个旧快照")
        return len(ids_to_delete)

    # ---- 置顶/取消置顶 ----

    async def toggle_pin(self, db: AsyncSession, snapshot_id: int) -> Optional[int]:
        """切换快照的置顶状态

        Args:
            db: 数据库异步会话
            snapshot_id: 快照ID

        Returns:
            新的 is_pinned 值（0 或 1），快照不存在时返回 None
        """
        snapshot = await self.get_by_id(db, snapshot_id)
        if not snapshot:
            return None

        snapshot.is_pinned = 0 if snapshot.is_pinned == 1 else 1
        await db.commit()
        return snapshot.is_pinned


flow_snapshot_service = FlowSnapshotService()

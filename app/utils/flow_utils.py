"""
流程检查工具函数
"""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flow_node import FlowNode

logger = logging.getLogger(__name__)


async def flow_contains_nodes(
    db: AsyncSession,
    flow_id: int,
    node_types: set[str],
    visited: Optional[set[int]] = None,
) -> bool:
    """检查流程（递归含卡片子流程）是否包含指定类型的节点。

    Args:
        db: 数据库会话
        flow_id: 流程ID
        node_types: 要检查的节点类型集合
        visited: 已访问的流程ID集合（防止循环引用）

    Returns:
        True 表示包含至少一个指定类型的节点
    """
    if visited is None:
        visited = set()

    if flow_id in visited:
        return False
    visited.add(flow_id)

    stmt = select(FlowNode.node_type, FlowNode.base_config).where(
        FlowNode.flow_id == flow_id,
        FlowNode.is_delete == 0,
    )
    result = await db.execute(stmt)
    rows = result.fetchall()

    for node_type, base_config in rows:
        if node_type in node_types:
            return True
        if node_type == "card" and isinstance(base_config, dict):
            ref_flow_id = base_config.get("ref_flow_id")
            if ref_flow_id and isinstance(ref_flow_id, int):
                if await flow_contains_nodes(db, ref_flow_id, node_types, visited):
                    return True

    return False

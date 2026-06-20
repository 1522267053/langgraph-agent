"""
流程变更追踪器（进程级注册表）

用于追踪 AI 工具调用期间对流程的修改操作。
由于 api_call_tool / shell+curl 均通过 HTTP 请求操作流程，
运行在独立的 ASGI 请求上下文中，无法直接访问 agent 的 writer。

本模块利用单进程内模块级变量共享的特性，
让 flow 写 API 记录变更，agent 工具执行器消费变更，
从而在不引入 Redis 等外部依赖的前提下实现变更检测。

限制：仅在单 uvicorn worker 下有效（开发环境 --reload 默认单 worker）。
"""

import time
from dataclasses import dataclass, field
from typing import List

# 注册表最大容量，超出时截断旧条目防止内存泄漏
_MAX_ENTRIES = 200


@dataclass
class FlowChangeEntry:
    """单条流程变更记录"""

    flow_id: int
    action: str  # create / nodes_changed / edges_changed / config_changed / delete
    timestamp: float = field(default_factory=time.time)


# 进程级变更列表（所有请求共享）
_recent_changes: List[FlowChangeEntry] = []


def record_flow_change(flow_id: int, action: str) -> None:
    """记录一次流程变更（在 flow 写 API commit 后调用）

    Args:
        flow_id: 被修改的流程 ID
        action: 变更类型描述
    """
    _recent_changes.append(FlowChangeEntry(flow_id=flow_id, action=action))
    if len(_recent_changes) > _MAX_ENTRIES:
        _recent_changes[:] = _recent_changes[-_MAX_ENTRIES // 2 :]


def consume_changes_since(since: float) -> List[FlowChangeEntry]:
    """消费指定时间点之后的所有变更记录（取出并清除）

    在 LLM 工具执行批次完成后调用，获取本批次期间发生的流程变更。

    Args:
        since: 起始时间戳（通常为工具批次开始前的时间）

    Returns:
        变更记录列表（按时间顺序）
    """
    matched = [c for c in _recent_changes if c.timestamp >= since]
    if matched:
        _recent_changes[:] = [c for c in _recent_changes if c.timestamp < since]
    return matched

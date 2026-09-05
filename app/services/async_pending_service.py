"""
通用异步等待服务基类

用于管理"会话级 + 唯一ID级" 的异步等待 Future 队列，
供 question_service / tool_approval_service 复用同一套生命周期管理逻辑。

设计目标：
1. 同一 (session_id, item_id) 重复 register 复用现有 Future（避免 LLM 死循环）
2. 不同 item_id 同时入队形成队列（前端逐个展示）
3. resolve/cancel 按 item_id 精确路由
4. 列表查询供前端展示当前 pending 顺序

生命周期：
1. 子类 register(session_id, item_id, **kwargs) → 创建 Future，存入 _pending[session_id][item_id]
2. emit_fn 通过 SSE 推送对应事件给前端
3. 前端弹窗展示 → 用户提交 → resolve(session_id, item_id, result)
4. 后端 await Future.event.wait() 返回 → Future.result 被填充 → 后端从 _pending 移除

参考：question_service / tool_approval_service 的原实现已并入本基类，
两个服务仅保留子类钩子（_create_future / _resolve_future / _cancel_result / _log_register）。
"""

import logging
import time
from dataclasses import dataclass
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

# 泛型参数：Future 结构 + resolve 后填充的结果类型
T_FUTURE = TypeVar("T_FUTURE")
T_RESULT = TypeVar("T_RESULT")


@dataclass
class PendingItem(Generic[T_FUTURE]):
    """队列中的等待项

    Attributes:
        item_id: 唯一标识（question_id / approval_id）
        future: 异步等待句柄
        created_at: 创建时间戳（用于队列排序）
        expires_at: 过期时间戳（time.time() 秒）
    """

    item_id: str
    future: T_FUTURE
    created_at: float
    expires_at: float


class AsyncPendingService(Generic[T_FUTURE, T_RESULT]):
    """异步等待服务基类（队列化版）

    子类需实现：
    - _create_future(item_id, **kwargs) -> T_FUTURE：构造 Future 对象
    - _resolve_future(future, result: T_RESULT) -> None：填充 Future 结果
    - _cancel_result() -> T_RESULT：cancel 时填充的默认值（如 None / "rejected"）
    - _log_register(session_id, item_id, future)：日志格式化（子类定制）

    数据结构：_pending[session_id][item_id] = PendingItem
    """

    def __init__(self, timeout_seconds: int):
        self._pending: dict[int, dict[str, PendingItem]] = {}
        self._timeout_seconds = timeout_seconds

    # ---- 子类需实现的钩子 ----

    def _create_future(self, item_id: str, **kwargs) -> T_FUTURE:
        """构造 Future 对象（子类定制 Future 结构和字段）"""
        raise NotImplementedError

    def _resolve_future(self, future: T_FUTURE, result: T_RESULT) -> None:
        """填充 Future 结果（子类决定 result 落到哪个字段）"""
        raise NotImplementedError

    def _cancel_result(self) -> T_RESULT:
        """cancel 时填充的默认值（question=None / approval=rejected）"""
        raise NotImplementedError

    def _log_register(self, session_id: int, item_id: str, future: T_FUTURE) -> None:
        """注册日志（子类定制日志格式）"""
        raise NotImplementedError

    # ---- 通用 API ----

    def register(self, session_id: int, item_id: str, **kwargs) -> T_FUTURE:
        """注册一个等待项，返回 Future

        同 (session_id, item_id) 重复调用复用现有 Future（避免 LLM 死循环）
        不同 item_id 同时入队（形成队列，前端逐个展示）

        Args:
            session_id: 会话 ID
            item_id: 唯一标识（question_id / approval_id）
            **kwargs: 透传给子类的额外参数（如 tool_calls / metadata）
        """
        existing = self._get_item(session_id, item_id)
        if existing:
            logger.warning(
                "%s 重复注册复用 Future: session_id=%s, item_id=%s",
                self.__class__.__name__,
                session_id,
                item_id,
            )
            return existing.future

        now = time.time()
        future = self._create_future(item_id, **kwargs)
        item = PendingItem(
            item_id=item_id,
            future=future,
            created_at=now,
            expires_at=now + self._timeout_seconds,
        )
        self._pending.setdefault(session_id, {})[item_id] = item
        self._log_register(session_id, item_id, future)
        return future

    def resolve(self, session_id: int, item_id: str, result: T_RESULT) -> bool:
        """按 item_id 精确路由到指定 Future 并唤醒

        Returns:
            True 表示找到并唤醒；False 表示 item_id 不存在（已超时/已 cancel/从未注册）
        """
        item = self._pop_item(session_id, item_id)
        if not item:
            return False
        self._resolve_future(item.future, result)
        item.future.event.set()
        logger.info(
            "%s 唤醒: session_id=%s, item_id=%s",
            self.__class__.__name__,
            session_id,
            item_id,
        )
        return True

    def remove(self, session_id: int, item_id: str) -> None:
        """await 后由调用方清理队列

        与 resolve 不同：remove 不触发 event.set()，仅从队列移除。
        调用方在 future.event.wait() 返回后调用，确保 _pending 不残留。
        """
        self._pop_item(session_id, item_id)

    def cancel(self, session_id: int, item_id: str | None = None) -> None:
        """取消等待（item_id=None 时取消该 session 下所有 item）

        触发 event.set() 并填充 cancel_result（None / "rejected"），
        让 await future.event.wait() 的调用方立即返回并执行后续清理逻辑。
        """
        items = self._pending.get(session_id)
        if not items:
            return
        target_ids = [item_id] if item_id else list(items.keys())
        for iid in target_ids:
            item = items.pop(iid, None)
            if item:
                self._resolve_future(item.future, self._cancel_result())
                item.future.event.set()
        if not items:
            self._pending.pop(session_id, None)
        logger.info(
            "%s 已取消: session_id=%s, item_id=%s",
            self.__class__.__name__,
            session_id,
            item_id,
        )

    def remaining_seconds(self, session_id: int, item_id: str) -> int | None:
        """返回当前 item 的剩余响应秒数；无等待时返回 None"""
        item = self._get_item(session_id, item_id)
        if not item:
            return None
        return max(0, int(item.expires_at - time.time()))

    def is_pending(self, session_id: int, item_id: str | None = None) -> bool:
        """判断是否有 pending 项

        item_id=None 时：返回该 session 是否有任意 pending
        item_id 给出时：返回该 session 下指定 item_id 是否 pending
        """
        items = self._pending.get(session_id)
        if not items:
            return False
        if item_id:
            return item_id in items
        return bool(items)

    def list_pending(self, session_id: int) -> list[str]:
        """返回当前 session 下所有 pending item_id（按创建时间排序）"""
        items = self._pending.get(session_id)
        if not items:
            return []
        return sorted(items.keys(), key=lambda iid: items[iid].created_at)

    def get_pending(self, session_id: int, item_id: str) -> T_FUTURE | None:
        """获取指定 item_id 的 Future 对象"""
        item = self._get_item(session_id, item_id)
        return item.future if item else None

    # ---- 内部辅助 ----

    def _get_item(self, session_id: int, item_id: str) -> PendingItem | None:
        items = self._pending.get(session_id)
        if not items:
            return None
        return items.get(item_id)

    def _pop_item(self, session_id: int, item_id: str) -> PendingItem | None:
        items = self._pending.get(session_id)
        if not items:
            return None
        item = items.pop(item_id, None)
        if not items:
            self._pending.pop(session_id, None)
        return item

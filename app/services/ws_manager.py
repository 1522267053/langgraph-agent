"""
WebSocket 连接管理器

支持从后台任务（流程执行、网关触发等）向已连接的前端客户端推送通知消息。
支持全局广播和按用户名定向推送。
基于 asyncio 单线程模型，无需锁保护。
"""

import logging
from typing import Optional
from uuid import uuid4

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """WebSocket 连接管理器，维护活跃连接并支持广播与定向推送"""

    def __init__(self):
        # username → connections 映射，支持定向推送
        self._user_connections: dict[str, set[WebSocket]] = {}
        # ws → username 反向映射，便于 disconnect 时 O(1) 查找
        self._ws_user: dict[WebSocket, str] = {}
        self._notification_enabled: bool = True

    def set_notification_enabled(self, enabled: bool) -> None:
        """设置执行完成通知开关（由系统配置更新调用）"""
        self._notification_enabled = enabled

    def is_notification_enabled(self) -> bool:
        """通知是否启用"""
        return self._notification_enabled

    async def connect(self, ws: WebSocket, username: str = "default") -> None:
        """接受连接并加入管理器，绑定用户名"""
        await ws.accept()
        if username not in self._user_connections:
            self._user_connections[username] = set()
        self._user_connections[username].add(ws)
        self._ws_user[ws] = username
        logger.info(
            f"WebSocket 已连接（用户: {username}），当前连接数: {self.connection_count()}"
        )

    def disconnect(self, ws: WebSocket) -> None:
        """移除断开的连接，同时清理反向映射"""
        username = self._ws_user.pop(ws, None)
        if username and username in self._user_connections:
            self._user_connections[username].discard(ws)
            if not self._user_connections[username]:
                del self._user_connections[username]

    async def broadcast(self, message: dict) -> None:
        """向所有连接的客户端广播消息，自动清理失效连接"""
        all_ws = [ws for conns in self._user_connections.values() for ws in conns]
        await self._send_to_list(all_ws, message)

    async def broadcast_to_user(self, username: str, message: dict) -> None:
        """定向推送给指定用户的所有连接，自动清理失效连接"""
        conns = self._user_connections.get(username)
        if not conns:
            return
        await self._send_to_list(list(conns), message)

    async def _send_to_list(self, ws_list: list[WebSocket], message: dict) -> None:
        """向给定连接列表发送消息，自动清理失效连接"""
        if not ws_list:
            return

        dead: list[WebSocket] = []
        for ws in ws_list:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self.disconnect(ws)

        if dead:
            logger.info(f"清理 {len(dead)} 个失效 WebSocket 连接")

    def connection_count(self) -> int:
        """获取当前连接数"""
        return sum(len(conns) for conns in self._user_connections.values())

    async def _persist_notification(
        self,
        *,
        type: str,
        title: str,
        content: Optional[str] = None,
        ref_type: Optional[str] = None,
        ref_id: Optional[int] = None,
        username: Optional[str] = None,
    ) -> None:
        """通知落库到 notification 表（统一通知中心，失败仅告警不阻断推送）"""
        try:
            from app.config.database import AsyncSessionLocal
            from app.services.notification_service import notification_service

            async with AsyncSessionLocal() as db:
                await notification_service.notify(
                    db,
                    type=type,
                    title=title,
                    content=content,
                    ref_type=ref_type,
                    ref_id=ref_id,
                    username=username,
                    push=False,  # 推送由调用方完成，此处仅落库
                )
        except Exception as e:
            logger.warning(f"通知落库失败 type={type}: {e}")

    async def notify_execution_done(
        self,
        *,
        execution_id: Optional[int],
        flow_id: Optional[int],
        flow_name: str,
        status: str,
        source: str = "flow",
        error_message: Optional[str] = None,
        duration_ms: Optional[int] = None,
        last_user_message: Optional[str] = None,
        browser_notify: bool = True,
    ) -> None:
        """广播执行完成通知（统一入口），受通知开关控制

        event_id 为本次推送的唯一标识：多标签场景下前端据此「抢占去重」，
        保证同一条事件只弹一次桌面通知（页内提示仍各标签各弹）。
        """
        if not self._notification_enabled:
            return

        # ---- 统一通知中心：落库（失败不阻断推送）----
        await self._persist_notification(
            type="execution_cancelled"
            if status == "cancelled"
            else ("execution_failed" if status == "failed" else "execution_done"),
            title=f"「{flow_name}」执行{'取消' if status == 'cancelled' else ('失败' if status == 'failed' else '完成')}",
            content=error_message,
            ref_type="session" if source == "agent" else "execution",
            ref_id=execution_id,
        )

        await self.broadcast(
            {
                "type": "execution_done",
                "event_id": uuid4().hex,
                "browser_notify": browser_notify,
                "data": {
                    "execution_id": execution_id,
                    "flow_id": flow_id,
                    "flow_name": flow_name,
                    "status": status,
                    "source": source,
                    "error_message": error_message,
                    "duration_ms": duration_ms,
                    "last_user_message": last_user_message,
                },
            }
        )

    async def notify_agenda_reminder(
        self,
        *,
        username: str,
        agenda_id: int,
        title: str,
        description: Optional[str] = None,
        start_time: Optional[str] = None,
        location: Optional[str] = None,
        browser_notify: bool = True,
        category: str = "agenda_reminder",
    ) -> None:
        """定向推送日程/提醒给指定用户（不受通知开关限制）

        category 用于前端按类型区分弹窗样式：
        - agenda_reminder（默认）：日程提醒，含完成、延后按钮
        - reminder：定时任务触发的提醒（最简 UI）

        event_id 为本次推送的唯一标识，用于多标签桌面通知去重（同 execution_done）。
        """
        # ---- 统一通知中心：落库（失败不阻断推送）----
        await self._persist_notification(
            type=category,
            title=title,
            content=description,
            ref_type="agenda" if category == "agenda_reminder" else "task",
            ref_id=agenda_id if agenda_id > 0 else None,
            username=username,
        )

        await self.broadcast_to_user(
            username,
            {
                "type": "agenda_reminder",
                "event_id": uuid4().hex,
                "category": category,
                "browser_notify": browser_notify,
                "data": {
                    "agenda_id": agenda_id,
                    "title": title,
                    "description": description,
                    "start_time": start_time,
                    "location": location,
                },
            },
        )


# 全局单例
ws_manager = WebSocketManager()

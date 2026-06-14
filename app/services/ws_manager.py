"""
WebSocket 连接管理器

支持从后台任务（流程执行、Webhook 触发等）向所有已连接的前端客户端广播通知消息。
基于 asyncio 单线程模型，无需锁保护。
"""

import logging
from typing import Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """WebSocket 连接管理器，维护活跃连接并支持广播"""

    def __init__(self):
        self._connections: set[WebSocket] = set()
        self._notification_enabled: bool = True

    def set_notification_enabled(self, enabled: bool) -> None:
        """设置执行完成通知开关（由系统配置更新调用）"""
        self._notification_enabled = enabled

    def is_notification_enabled(self) -> bool:
        """通知是否启用"""
        return self._notification_enabled

    async def connect(self, ws: WebSocket) -> None:
        """接受连接并加入管理器"""
        await ws.accept()
        self._connections.add(ws)
        logger.info(f"WebSocket 已连接，当前连接数: {len(self._connections)}")

    def disconnect(self, ws: WebSocket) -> None:
        """移除断开的连接"""
        self._connections.discard(ws)

    async def broadcast(self, message: dict) -> None:
        """向所有连接的客户端广播消息，自动清理失效连接"""
        if not self._connections:
            return

        dead: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self._connections.discard(ws)

        if dead:
            logger.info(f"清理 {len(dead)} 个失效 WebSocket 连接")

    def connection_count(self) -> int:
        """获取当前连接数"""
        return len(self._connections)

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
    ) -> None:
        """广播执行完成通知（统一入口），受通知开关控制"""
        if not self._notification_enabled:
            return

        await self.broadcast(
            {
                "type": "execution_done",
                "data": {
                    "execution_id": execution_id,
                    "flow_id": flow_id,
                    "flow_name": flow_name,
                    "status": status,
                    "source": source,
                    "error_message": error_message,
                    "duration_ms": duration_ms,
                },
            }
        )


# 全局单例
ws_manager = WebSocketManager()

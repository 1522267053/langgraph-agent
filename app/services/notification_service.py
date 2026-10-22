"""
统一通知中心服务

所有通知的唯一入口：先落库（notification 表），再经 WebSocket 推送前端。
落库失败不阻断推送（通知属辅助提示，丢一条无实质影响）。

现有通知源收敛口径：
- ws_manager.notify_execution_done / notify_agenda_reminder 内部已追加落库，
  既有调用点（flow/agent 执行器、调度器、定时任务）无需逐个改造；
- 新增通知源直接调用 notification_service.notify()。
"""

import logging
from typing import Optional

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.schemas.notification_schema import (
    NotificationCondition,
    NotificationCreate,
    NotificationUpdate,
)
from app.services.base_service import BaseService

logger = logging.getLogger(__name__)

# 通知正文截断上限（60 字符）：通知是提示性入口，正文只承担「是什么事」的
# 概览职责，全文（如 LLM 完整输出、审批工具参数快照）应到关联页面
# （ref_type/ref_id）查看。落库与 WS 推送共用同一截断，避免前端弹窗被
# 超长内容撑爆。所有通知正文写入路径（notify / 聚合原位更新）统一经
# _compact_content 收口，禁止绕过。
NOTIFICATION_CONTENT_MAX_CHARS = 60


def _compact_content(content: Optional[str]) -> Optional[str]:
    """通知正文统一压缩截断：单行压缩（换行变空格）+ 限长（超长加省略号）。"""
    if not content:
        return content
    compact = " ".join(content.split())
    if len(compact) > NOTIFICATION_CONTENT_MAX_CHARS:
        return compact[:NOTIFICATION_CONTENT_MAX_CHARS] + "…"
    return compact


class NotificationService(
    BaseService[Notification, NotificationCreate, NotificationUpdate]
):
    """统一通知服务：落库 + WebSocket 推送二合一"""

    def __init__(self):
        super().__init__(Notification)

    # ---- 统一入口 ----

    async def notify(
        self,
        db: AsyncSession,
        *,
        type: str,
        title: str,
        content: Optional[str] = None,
        ref_type: Optional[str] = None,
        ref_id: Optional[int] = None,
        ref_sub_id: Optional[int] = None,
        username: Optional[str] = None,
        browser_notify: bool = True,
        push: bool = True,
    ) -> Optional[Notification]:
        """创建通知并推送（统一入口）

        Args:
            db: 数据库会话
            type: 通知类型（NotificationType）
            title: 标题
            content: 正文
            ref_type: 关联类型（NotificationRefType），前端跳转用
            ref_id: 关联 ID
            ref_sub_id: 辅助关联 ID（session 通知存 agent flow_id，跳转用）
            username: 定向推送用户名（None 时全局广播）
            browser_notify: 是否触发浏览器桌面通知
            push: 是否 WebSocket 推送（False 仅落库）

        Returns:
            通知记录（落库失败返回 None，不抛异常）
        """
        notification: Optional[Notification] = None
        compact = _compact_content(content)
        try:
            create = NotificationCreate(
                type=type,
                title=title[:255] if title else "通知",
                content=compact,
                ref_type=ref_type,
                ref_id=ref_id,
                ref_sub_id=ref_sub_id,
            )
            notification = await self.create(db, create)
        except Exception as e:
            # 落库失败不阻断推送（通知属辅助提示）
            logger.warning(f"通知落库失败 type={type} title={title}: {e}")

        if push:
            try:
                unread = await self.get_unread_count(db)
            except Exception:
                unread = 0
            payload = {
                "notification_id": notification.id if notification else None,
                "unread_count": unread,
                "type": type,
                "title": title,
                "content": compact,
                "ref_type": ref_type,
                "ref_id": ref_id,
                "ref_sub_id": ref_sub_id,
                "browser_notify": browser_notify,
            }
            try:
                from app.services.ws_manager import ws_manager

                if username:
                    await ws_manager.broadcast_to_user(
                        username, {"type": "notification", "data": payload}
                    )
                else:
                    await ws_manager.broadcast(
                        {"type": "notification", "data": payload}
                    )
            except Exception as e:
                logger.warning(f"通知 WebSocket 推送失败 type={type}: {e}")

        return notification

    # ---- Agent 等待用户输入便捷入口（审批弹窗 / Question 反问共用）----

    # 审批通知聚合注册表（进程内存态）：session_id -> {"id", "tools", "count"}。
    # 连环多轮审批复用同一条通知（原位更新工具并集+批次数），用户决策/超时/已读后
    # 失效回归新建。与 tool_approval_service 队列同为内存单例口径（桌面单进程），
    # 重启后注册表清空 → 最多退化为「多批各自一条」，不产生错误聚合。
    _agent_approval_notifications: dict[int, dict] = {}

    async def notify_agent_input_needed(
        self,
        *,
        session_id: int,
        title: str,
        content: str,
        kind: str = "input",
        tool_name: str = "",
    ) -> None:
        """Agent 等待用户输入通知（工具审批 / Question 反问弹窗触发）。

        与 _handle_interrupt 的 human_input 通知同一跳转链路
        （ref_type=session + ref_sub_id=flow_id）。调用方处于工具执行
        协程内、无现成 db session，此处自建短会话；任何失败静默
        （通知属辅助提示，失败不能阻断审批/Question 等待主流程）。

        聚合口径：同 session 存在未读「待响应」审批通知时，后续批次
        **原位更新**该条（tool_name 并集进正文 + 批次数计数，silent 推送
        只刷角标不弹新窗），用户全部决策/超时/已读后失效回归新建；
        Question 与超时通知不参与聚合（超时是终态，必须独立成条）。

        Args:
            session_id: Agent 会话 ID（跳转定位用）
            title: 通知标题（{session_title} 占位符填充）
            content: 正文（问题正文 / 聚合失效时的兜底文案）
            kind: input=待响应 / timeout=超时未响应（标题自动加前缀）
            tool_name: 审批工具名（参与聚合；空=不聚合，如 Question）
        """
        aggregated = False
        if kind == "input" and tool_name:
            entry = self._agent_approval_notifications.get(session_id)
            if entry is not None:
                # 复用未读待响应通知：工具名并集 + 批次数计数，原位更新
                tools = entry["tools"]
                if tool_name not in tools:
                    tools.append(tool_name)
                entry["count"] += 1
                names = "、".join(tools)
                content = (
                    f"{len(tools)} 个工具等待审批（共 {entry['count']} 批）：{names}。"
                    "请前往会话处理"
                )
                try:
                    from app.config.database import AsyncSessionLocal

                    async with AsyncSessionLocal() as db:
                        notification = await self.get_by_id(
                            db, entry["id"], raise_not_found=False
                        )
                        if (
                            notification is None
                            or notification.is_read
                            or notification.is_delete
                        ):
                            # 已被用户已读/删除/清理：注册表失效，回归新建
                            self._agent_approval_notifications.pop(session_id, None)
                        else:
                            notification.title = title.format(
                                session_title=await self._get_session_title(
                                    db, session_id
                                )
                            )
                            notification.content = _compact_content(content)
                            await db.commit()
                            aggregated = True
                            await self._push_silent(session_id)
                except Exception as e:
                    logger.warning(f"审批通知聚合更新失败 session_id={session_id}: {e}")
                    aggregated = True  # 更新失败不回退新建（避免重复弹窗），下轮重试
        if aggregated:
            return

        try:
            from app.config.database import AsyncSessionLocal
            from app.models.agent_session import AgentSession

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(AgentSession).where(AgentSession.id == session_id)
                )
                session = result.scalar_one_or_none()
                flow_id = session.flow_id if session else None
                session_title = (session.title if session else None) or "Agent对话"
                notification = await self.notify(
                    db,
                    type="human_input",
                    title=(
                        f"[已超时] {title.format(session_title=session_title)}"
                        if kind == "timeout"
                        else title.format(session_title=session_title)
                    ),
                    content=content,
                    ref_type="session",
                    ref_id=session_id,
                    ref_sub_id=flow_id,
                )
                # 审批待响应通知登记进聚合注册表（Question/超时不登记）
                if kind == "input" and tool_name and notification is not None:
                    self._agent_approval_notifications[session_id] = {
                        "id": notification.id,
                        "tools": [tool_name],
                        "count": 1,
                    }
                elif kind != "input" or not tool_name:
                    # Question / 超时通知不参与聚合，且需清除该 session 的旧注册
                    # 条目：超时是终态，旧「待响应」聚合目标已失效，残留会让
                    # 后续批次错误聚合到过期通知上（断言实锤后修复）
                    self._agent_approval_notifications.pop(session_id, None)
        except Exception as e:
            logger.warning(
                f"Agent 输入通知失败 session_id={session_id} kind={kind}: {e}"
            )

    async def _get_session_title(self, db: AsyncSession, session_id: int) -> str:
        from app.models.agent_session import AgentSession

        result = await db.execute(
            select(AgentSession.title).where(AgentSession.id == session_id)
        )
        return (result.scalar_one_or_none()) or "Agent对话"

    async def _push_silent(self, session_id: int) -> None:
        """silent 推送：只刷角标与通知列表（前端 silent 分支不弹新窗）。"""
        try:
            from app.config.database import AsyncSessionLocal
            from app.services.ws_manager import ws_manager

            async with AsyncSessionLocal() as db:
                unread = await self.get_unread_count(db)
            await ws_manager.broadcast(
                {
                    "type": "notification",
                    "data": {
                        "silent": True,
                        "notification_id": None,
                        "unread_count": unread,
                        "type": "human_input",
                        "title": "工具审批已更新",
                        "content": f"会话 {session_id} 有新的待审批批次",
                        "browser_notify": False,
                    },
                }
            )
        except Exception as e:
            logger.warning(f"审批聚合 silent 推送失败 session_id={session_id}: {e}")

    async def invalidate_agent_approval_notification(
        self, session_id: int, silent: bool = False
    ) -> None:
        """审批通知聚合失效：本轮执行结束/超时后调用，下一轮回归新建。

        「聚合」口径 = 同一轮执行（两次 flow_done 之间）内的连环批次；
        跨轮不聚合（用户 2026-10-21 实测拍板：两轮对话的审批通知不应合并）。

        Args:
            session_id: Agent 会话 ID
            silent: True 时静默失效（flow_done 收尾用，不发 silent 推送——
                该通知要么已被用户处理、要么用户正在会话页看着，无需打扰）
        """
        had = self._agent_approval_notifications.pop(session_id, None) is not None
        if had and not silent:
            await self._push_silent(session_id)

    # ---- 查询 ----

    async def get_unread_count(self, db: AsyncSession) -> int:
        """未读通知数量"""
        result = await db.execute(
            select(func.count(Notification.id)).where(
                Notification.is_read == 0, Notification.is_delete == 0
            )
        )
        return int(result.scalar() or 0)

    def _apply_filters(
        self,
        query: Optional[Select],
        count_query: Optional[Select],
        condition: Optional[NotificationCondition],
    ):
        """重写过滤：类型/已读精确、标题模糊"""
        if not condition:
            return query, count_query

        if condition.type:
            if query is not None:
                query = query.where(Notification.type == condition.type)
            if count_query is not None:
                count_query = count_query.where(Notification.type == condition.type)
        if condition.is_read is not None:
            if query is not None:
                query = query.where(Notification.is_read == condition.is_read)
            if count_query is not None:
                count_query = count_query.where(
                    Notification.is_read == condition.is_read
                )
        if condition.title:
            query, count_query = self._apply_like_filter(
                query, count_query, "title", condition.title
            )
        return query, count_query

    # ---- 已读操作 ----

    async def mark_read(self, db: AsyncSession, notification_id: int) -> bool:
        """标记单条已读。返回通知是否存在"""
        from datetime import datetime

        notification = await self.get_by_id(db, notification_id, raise_not_found=False)
        if notification is None:
            return False
        if not notification.is_read:
            notification.is_read = 1
            notification.read_time = datetime.now()
            await db.commit()
        return True

    async def mark_all_read(self, db: AsyncSession) -> int:
        """全部标记已读。返回影响条数"""
        from datetime import datetime

        result = await db.execute(
            select(Notification).where(
                Notification.is_read == 0, Notification.is_delete == 0
            )
        )
        items = result.scalars().all()
        now = datetime.now()
        for item in items:
            item.is_read = 1
            item.read_time = now
        if items:
            await db.commit()
        return len(items)

    async def clear_read(self, db: AsyncSession) -> int:
        """清空所有已读通知（软删除）。返回清理条数"""
        result = await db.execute(
            select(Notification).where(
                Notification.is_read == 1, Notification.is_delete == 0
            )
        )
        items = result.scalars().all()
        for item in items:
            item.is_delete = 1
        if items:
            await db.commit()
        return len(items)


notification_service = NotificationService()

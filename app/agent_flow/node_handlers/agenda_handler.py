"""
日程管理节点处理器

为 Agent 提供 LLM 工具，使 LLM 能创建、查询、更新和删除日程。
日程数据存储在 agenda 表中，通过 creator_name 区分用户。

提供的工具：
1. agenda_create - 创建日程
2. agenda_list - 查询日程列表
3. agenda_update - 更新日程
4. agenda_delete - 删除日程
"""

import json
from datetime import datetime
from typing import Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import StreamWriter
from pydantic import BaseModel, Field

from app.agent_flow.flow_context import FlowState
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.node_handlers.base_handler import BaseNodeHandler
from app.config.database import AsyncSessionLocal
from app.models.agenda import (
    Agenda,
    AgendaCategory,
    AgendaRecurrence,
    AgendaStatus,
)
from app.models.flow_node import FlowNode
from app.services.agenda_service import agenda_service
from app.utils.user_util import get_current_username


@NodeHandlerRegistry.register("agenda")
class AgendaNodeHandler(BaseNodeHandler):
    """
    日程管理节点处理器

    作为 LLM 工具提供者使用（通过 tools Handle 连接到 LLM 节点）。
    提供日程的创建、查询、更新和删除能力，帮助 LLM 管理用户的日程安排。
    """

    @classmethod
    def get_config_schema(cls) -> list[dict]:
        return []

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState | dict:
        return state

    @classmethod
    def allow_multiple_tool_connections(cls) -> bool:
        """日程管理工具只需一个，同一 LLM 不允许多实例"""
        return False

    async def get_system_prompt_hint(self, node: FlowNode) -> Optional[str]:
        """返回日程管理工具使用提示，追加到 LLM system_prompt"""
        from datetime import datetime

        now = datetime.now().strftime("%Y年%m月%d日")
        return (
            f"\n\n## 日程管理\n当前时间：{now}\n"
            "拥有日程管理能力（agenda_create/agenda_list/agenda_update/agenda_delete）。使用规则：\n"
            "- 用户提到日程、提醒、安排时间时，主动使用日程工具\n"
            "- 时间格式统一使用 YYYY-MM-DD HH:MM:SS\n"
            "- 修改日程流程：① agenda_list 查询找到目标日程 → ② 从结果中获取 id → ③ agenda_update(id, ...) 更新\n"
            "- 删除日程流程：① agenda_list 查询找到目标日程 → ② 从结果中获取 id → ③ agenda_delete(id)\n"
            "- 日程时间已过时也可查询（用于回顾）"
        )

    async def get_tool(self, node: FlowNode) -> list[BaseTool]:
        async def create_agenda(
            title: str,
            start_time: str = "",
            end_time: str = "",
            category: str = "other",
            priority: int = 2,
            location: str = "",
            remind_at: str = "",
            recurrence: str = "none",
            description: str = "",
        ) -> str:
            """创建日程"""
            username = await get_current_username()

            # 构建创建数据
            data: dict = {
                "title": title,
                "category": category
                if category in [c.value for c in AgendaCategory]
                else "other",
                "priority": priority if 1 <= priority <= 3 else 2,
                "recurrence": recurrence
                if recurrence in [r.value for r in AgendaRecurrence]
                else "none",
                "location": location or None,
                "description": description or None,
            }
            if start_time:
                data["start_time"] = start_time
            if end_time:
                data["end_time"] = end_time
            if remind_at:
                data["remind_at"] = remind_at

            from app.schemas.agenda_schema import AgendaCreate

            schema = AgendaCreate(**data)
            schema.creator_name = username
            async with AsyncSessionLocal() as db:
                agenda = await agenda_service.create(db, schema)
                # 同步提醒调度
                from app.services.scheduler_service import scheduler_service

                scheduler_service.sync_agenda_reminder(agenda)
                if agenda.remind_at:
                    await db.commit()
                    await db.refresh(agenda)

            return json.dumps(
                {
                    "success": True,
                    "id": agenda.id,
                    "title": agenda.title,
                    "message": f"日程「{title}」创建成功",
                },
                ensure_ascii=False,
            )

        async def list_agendas(
            status: int = -1,
            category: str = "",
            search_keyword: str = "",
            start_date: str = "",
            end_date: str = "",
            limit: int = 20,
        ) -> str:
            """查询日程列表"""
            username = await get_current_username()
            async with AsyncSessionLocal() as db:
                from sqlalchemy import or_, select

                stmt = select(Agenda).where(Agenda.creator_name == username)
                if status >= 0:
                    stmt = stmt.where(Agenda.status == status)
                if category:
                    stmt = stmt.where(Agenda.category == category)
                if search_keyword:
                    pattern = f"%{search_keyword}%"
                    stmt = stmt.where(
                        or_(
                            Agenda.title.like(pattern),
                            Agenda.description.like(pattern),
                        )
                    )
                if start_date:
                    stmt = stmt.where(Agenda.start_time >= start_date)
                if end_date:
                    stmt = stmt.where(Agenda.start_time <= f"{end_date} 23:59:59")
                stmt = stmt.order_by(Agenda.start_time.desc().nullslast()).limit(limit)
                result = await db.execute(stmt)
                items = list(result.scalars().all())

            status_labels = {0: "待办", 1: "进行中", 2: "已完成"}
            data = [
                {
                    "id": item.id,
                    "title": item.title,
                    "start_time": item.start_time.strftime("%Y-%m-%d %H:%M")
                    if item.start_time
                    else None,
                    "end_time": item.end_time.strftime("%Y-%m-%d %H:%M")
                    if item.end_time
                    else None,
                    "category": item.category,
                    "priority": item.priority,
                    "status": item.status,
                    "status_label": status_labels.get(item.status, "未知"),
                    "location": item.location,
                    "remind_at": item.remind_at.strftime("%Y-%m-%d %H:%M")
                    if item.remind_at
                    else None,
                }
                for item in items
            ]
            return json.dumps({"agendas": data, "total": len(data)}, ensure_ascii=False)

        async def update_agenda(
            id: int,
            title: str = "",
            start_time: str = "",
            end_time: str = "",
            status: int = -1,
            category: str = "",
            priority: int = -1,
            location: str = "",
            remind_at: str = "",
            description: str = "",
        ) -> str:
            """更新日程"""
            update_data: dict = {"id": id}
            if title:
                update_data["title"] = title
            if start_time:
                update_data["start_time"] = start_time
            if end_time:
                update_data["end_time"] = end_time
            if status >= 0:
                update_data["status"] = status
                if status == AgendaStatus.COMPLETED.value:
                    update_data["completed_at"] = datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
            if category:
                update_data["category"] = category
            if priority >= 0:
                update_data["priority"] = priority
            if location:
                update_data["location"] = location
            if remind_at:
                update_data["remind_at"] = remind_at
                update_data["is_reminded"] = 0  # 重置提醒标志，允许重新推送
            if description:
                update_data["description"] = description

            from app.schemas.agenda_schema import AgendaUpdate

            schema = AgendaUpdate(**update_data)
            async with AsyncSessionLocal() as db:
                agenda = await agenda_service.update(db, schema)
                if agenda:
                    from app.services.scheduler_service import scheduler_service

                    scheduler_service.sync_agenda_reminder(agenda)
                    await db.commit()
                    await db.refresh(agenda)
                    return json.dumps(
                        {
                            "success": True,
                            "id": agenda.id,
                            "message": f"日程「{agenda.title}」更新成功",
                        },
                        ensure_ascii=False,
                    )
                return json.dumps(
                    {"success": False, "message": f"日程(id={id})不存在"},
                    ensure_ascii=False,
                )

        async def delete_agenda(id: int) -> str:
            """删除日程"""
            async with AsyncSessionLocal() as db:
                try:
                    from app.services.scheduler_service import scheduler_service

                    scheduler_service.remove_agenda_reminder(id)
                    await agenda_service.delete(db, id)
                    return json.dumps(
                        {"success": True, "message": f"日程(id={id})已删除"},
                        ensure_ascii=False,
                    )
                except Exception as e:
                    return json.dumps(
                        {"success": False, "message": f"删除失败: {e}"},
                        ensure_ascii=False,
                    )

        tools: list[BaseTool] = [
            StructuredTool(
                name="agenda_create",
                description=(
                    "创建一条日程。title 必填，其他参数可选。"
                    "时间格式：YYYY-MM-DD HH:MM:SS。"
                    "category: work/life/study/other, priority: 1低/2中/3高, "
                    "recurrence: none/daily/weekday/weekly/monthly"
                ),
                func=None,
                coroutine=create_agenda,
                args_schema=AgendaCreateInput,
            ),
            StructuredTool(
                name="agenda_list",
                description=(
                    "查询日程列表。支持按状态、分类筛选，支持按关键词搜索标题和描述，"
                    "支持按 start_date/end_date 筛选时间范围（格式 YYYY-MM-DD）。"
                    "status: -1全部/0待办/1进行中/2已完成。"
                    "limit 控制返回数量（默认20）。"
                ),
                func=None,
                coroutine=list_agendas,
                args_schema=AgendaListInput,
            ),
            StructuredTool(
                name="agenda_update",
                description=(
                    "更新日程。id 必填，其他参数可选（只更新提供的字段）。"
                    "status: 0待办/1进行中/2已完成"
                ),
                func=None,
                coroutine=update_agenda,
                args_schema=AgendaUpdateInput,
            ),
            StructuredTool(
                name="agenda_delete",
                description="删除日程。id 必填。",
                func=None,
                coroutine=delete_agenda,
                args_schema=AgendaDeleteInput,
            ),
        ]

        return tools


# ---- 工具参数 Schema ----


class AgendaCreateInput(BaseModel):
    title: str = Field(..., description="日程标题")
    start_time: str = Field("", description="开始时间 YYYY-MM-DD HH:MM:SS")
    end_time: str = Field("", description="结束时间 YYYY-MM-DD HH:MM:SS")
    category: str = Field("other", description="分类：work/life/study/other")
    priority: int = Field(2, description="优先级：1=低/2=中/3=高")
    location: str = Field("", description="地点")
    remind_at: str = Field("", description="提醒时间 YYYY-MM-DD HH:MM:SS")
    recurrence: str = Field(
        "none", description="重复：none/daily/weekday/weekly/monthly"
    )
    description: str = Field("", description="备注")


class AgendaListInput(BaseModel):
    status: int = Field(-1, description="状态筛选：-1=全部/0=待办/1=进行中/2=已完成")
    category: str = Field("", description="分类筛选：work/life/study/other")
    search_keyword: str = Field("", description="关键词搜索标题和描述")
    start_date: str = Field("", description="开始时间范围起 YYYY-MM-DD")
    end_date: str = Field("", description="开始时间范围止 YYYY-MM-DD")
    limit: int = Field(20, description="返回数量上限")


class AgendaUpdateInput(BaseModel):
    id: int = Field(..., description="日程ID")
    title: str = Field("", description="新标题")
    start_time: str = Field("", description="新开始时间")
    end_time: str = Field("", description="新结束时间")
    status: int = Field(-1, description="新状态：0=待办/1=进行中/2=已完成")
    category: str = Field("", description="新分类")
    priority: int = Field(-1, description="新优先级：1=低/2=中/3=高")
    location: str = Field("", description="新地点")
    remind_at: str = Field("", description="新提醒时间")
    description: str = Field("", description="新备注")


class AgendaDeleteInput(BaseModel):
    id: int = Field(..., description="日程ID")

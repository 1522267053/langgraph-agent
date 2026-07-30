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

from datetime import datetime
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import StreamWriter
from pydantic import BaseModel, Field, ValidationError

from app.schemas.base_schema import ChinaDateTime

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


_VALID_CATEGORIES = {c.value for c in AgendaCategory}
_VALID_RECURRENCES = {r.value for r in AgendaRecurrence}


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
        now = datetime.now().strftime("%Y年%m月%d日")
        return (
            f"\n\n## 日程管理\n当前时间：{now}\n"
            "拥有日程管理能力（agenda_create/agenda_list/agenda_update/agenda_delete）。使用规则：\n"
            "- 用户提到日程、提醒、安排时间时，主动使用日程工具\n"
            "- 时间格式统一使用 YYYY-MM-DD HH:MM:SS\n"
            "- 修改日程流程：① agenda_list 查询找到目标日程（含 recurrence/color）→ ② 获取 id ③ agenda_update(id, ...) 更新\n"
            "- 删除日程流程：① agenda_list 查询找到目标日程 → ② 获取 id → ③ agenda_delete(id)\n"
            "- 日程时间已过时也可查询（用于回顾）\n"
            "- 更新字段语义：不传=保持原值；传 null 或空串=清空（title 不支持清空）；传值=设为该值"
        )

    async def get_tool(self, node: FlowNode) -> list[BaseTool]:
        async def create_agenda(
            title: str,
            start_time: Optional[ChinaDateTime] = None,
            end_time: Optional[ChinaDateTime] = None,
            category: str = "other",
            priority: int = 2,
            location: Optional[str] = None,
            remind_at: Optional[ChinaDateTime] = None,
            recurrence: str = "none",
            description: Optional[str] = None,
            color: Optional[str] = None,
        ) -> dict:
            """创建日程"""
            if category not in _VALID_CATEGORIES:
                return {
                    "success": False,
                    "message": f"无效分类: {category}（有效值: work/life/study/other）",
                }
            if recurrence not in _VALID_RECURRENCES:
                return {
                    "success": False,
                    "message": f"无效重复规则: {recurrence}（有效值: none/daily/weekday/weekly/monthly）",
                }
            if not 1 <= priority <= 3:
                return {
                    "success": False,
                    "message": f"无效优先级: {priority}（有效值: 1=低/2=中/3=高）",
                }

            data: dict = {
                "title": title,
                "category": category,
                "priority": priority,
                "recurrence": recurrence,
            }
            if start_time:
                data["start_time"] = start_time
            if end_time:
                data["end_time"] = end_time
            if remind_at:
                data["remind_at"] = remind_at
            if location is not None:
                data["location"] = location
            if description is not None:
                data["description"] = description
            if color is not None:
                data["color"] = color

            from app.schemas.agenda_schema import AgendaCreate

            schema = AgendaCreate(**data)
            if (
                schema.start_time
                and schema.end_time
                and schema.end_time < schema.start_time
            ):
                return {"success": False, "message": "结束时间不能早于开始时间"}

            schema.creator_name = await get_current_username()

            async with AsyncSessionLocal() as db:
                agenda = await agenda_service.create(db, schema)
                from app.services.scheduler_service import scheduler_service

                scheduler_service.sync_agenda_reminder(agenda)

            return {
                "success": True,
                "id": agenda.id,
                "title": agenda.title,
                "message": f"日程「{title}」创建成功",
            }

        async def list_agendas(
            status: int = -1,
            category: str = "",
            search_keyword: str = "",
            start_date: str = "",
            end_date: str = "",
            limit: int = 20,
        ) -> dict:
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
                    "recurrence": item.recurrence,
                    "color": item.color,
                    "description": item.description,
                }
                for item in items
            ]
            return {"agendas": data, "total": len(data)}

        async def update_agenda(id: int, **kwargs: Any) -> dict:
            """更新日程。

            字段语义（与前端 PATCH 一致，仅修改显式传入的有效字段）：
            - 字段未传或传 null：保持原值
            - 传空串：清空（仅字符串字段，title 除外因数据库 NOT NULL）
            - 传有效值：设为该值
            """
            username = await get_current_username()

            # LLM 重复传 id 时去重
            kwargs.pop("id", None)

            try:
                schema = AgendaUpdateInput(id=id, **kwargs)
            except ValidationError as e:
                return {"success": False, "message": f"参数错误: {e}"}

            # 仅取 LLM 显式提供的有效字段（null 视为未传，保持原值）
            set_fields = schema.model_dump(exclude_unset=True)
            set_fields.pop("id", None)
            set_fields = {k: v for k, v in set_fields.items() if v is not None}

            # 枚举/范围校验
            if (
                "category" in set_fields
                and set_fields["category"] not in _VALID_CATEGORIES
            ):
                return {
                    "success": False,
                    "message": f"无效分类: {set_fields['category']}",
                }
            if (
                "recurrence" in set_fields
                and set_fields["recurrence"] not in _VALID_RECURRENCES
            ):
                return {
                    "success": False,
                    "message": f"无效重复规则: {set_fields['recurrence']}",
                }
            if "priority" in set_fields and not 1 <= set_fields["priority"] <= 3:
                return {
                    "success": False,
                    "message": f"无效优先级: {set_fields['priority']}",
                }

            # 字符串字段：空串视为 None（清空）
            for field in ("location", "description", "color"):
                if field in set_fields and set_fields[field] == "":
                    set_fields[field] = None

            # title：空串视为不变（数据库 NOT NULL，不支持清空）
            if "title" in set_fields and not set_fields["title"]:
                del set_fields["title"]

            # 时间顺序校验
            if set_fields.get("start_time") and set_fields.get("end_time"):
                if set_fields["end_time"] < set_fields["start_time"]:
                    return {"success": False, "message": "结束时间不能早于开始时间"}

            # 状态切换语义
            new_status = set_fields.get("status")
            if new_status is not None:
                if new_status == AgendaStatus.COMPLETED.value:
                    set_fields["completed_at"] = datetime.now()
                else:
                    set_fields["completed_at"] = None

            # 修改 recurrence / start_time / end_time 时重置生成锁，
            # 避免旧的已生成实例与新规则不一致
            if (
                "recurrence" in set_fields
                or "start_time" in set_fields
                or "end_time" in set_fields
            ):
                set_fields["recurrence_generated"] = 0

            # 修改 remind_at 时重置提醒标志，允许重新推送
            if "remind_at" in set_fields:
                set_fields["is_reminded"] = 0
            # 从已完成改回其他状态：允许重新推送
            if new_status is not None and new_status != AgendaStatus.COMPLETED.value:
                set_fields["is_reminded"] = 0

            async with AsyncSessionLocal() as db:
                # 权限校验：先查询归属
                existing = await agenda_service.get_by_id(db, id, raise_not_found=False)
                if not existing:
                    return {"success": False, "message": f"日程(id={id})不存在"}
                if existing.creator_name != username:
                    return {"success": False, "message": "无权操作此日程"}

                # 切到已完成时移除提醒调度
                if new_status == AgendaStatus.COMPLETED.value:
                    from app.services.scheduler_service import scheduler_service

                    scheduler_service.remove_agenda_reminder(id)

                # 直接修改 ORM 对象后交给 service.update 提交
                for field, value in set_fields.items():
                    setattr(existing, field, value)
                agenda = await agenda_service.update(db, existing)

                from app.services.scheduler_service import scheduler_service

                scheduler_service.sync_agenda_reminder(agenda)

                return {
                    "success": True,
                    "id": agenda.id,
                    "message": f"日程「{agenda.title}」更新成功",
                }

        async def delete_agenda(id: int) -> dict:
            """删除日程"""
            username = await get_current_username()
            async with AsyncSessionLocal() as db:
                try:
                    # 权限校验：先查询归属
                    existing = await agenda_service.get_by_id(
                        db, id, raise_not_found=False
                    )
                    if not existing:
                        return {"success": False, "message": f"日程(id={id})不存在"}
                    if existing.creator_name != username:
                        return {"success": False, "message": "无权操作此日程"}

                    from app.services.scheduler_service import scheduler_service

                    scheduler_service.remove_agenda_reminder(id)
                    await agenda_service.delete(db, id)
                    return {"success": True, "message": f"日程(id={id})已删除"}
                except Exception as e:
                    return {"success": False, "message": f"删除失败: {e}"}

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
                    "limit 控制返回数量（默认20）。返回字段含 recurrence/color/description。"
                ),
                func=None,
                coroutine=list_agendas,
                args_schema=AgendaListInput,
            ),
            StructuredTool(
                name="agenda_update",
                description=(
                    "更新日程。id 必填，其他字段可选。"
                    "字段语义：不传=保持原值；传 null 或空串=清空；传值=设为该值（title 不可清空）。"
                    "status: 0=待办/1=进行中/2=已完成。"
                    "  → 设为已完成自动写入完成时间；从已完成改回其他状态会清空完成时间并重置提醒标志。"
                    "recurrence: none/daily/weekday/weekly/monthly（修改后会重置重复生成锁）。"
                    "修改 remind_at 会重置已推送标志，允许重新推送。"
                    "color/location/description 支持清空（传 null 或空串）。"
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

    @classmethod
    def get_tool_info(cls, node: FlowNode) -> list[dict]:
        return [
            {"name": "agenda_create", "description": "创建日程"},
            {"name": "agenda_list", "description": "查询日程列表"},
            {"name": "agenda_update", "description": "更新日程"},
            {"name": "agenda_delete", "description": "删除日程"},
        ]


# ---- 工具参数 Schema ----


class _AgendaFields(BaseModel):
    """议程工具共用字段基类"""

    start_time: Optional[ChinaDateTime] = Field(
        None, description="开始时间 YYYY-MM-DD HH:MM:SS"
    )
    end_time: Optional[ChinaDateTime] = Field(
        None, description="结束时间 YYYY-MM-DD HH:MM:SS"
    )
    category: Optional[str] = Field(None, description="分类：work/life/study/other")
    priority: Optional[int] = Field(None, description="优先级：1=低/2=中/3=高")
    location: Optional[str] = Field(None, description="地点")
    remind_at: Optional[ChinaDateTime] = Field(
        None, description="提醒时间 YYYY-MM-DD HH:MM:SS"
    )
    recurrence: Optional[str] = Field(
        None, description="重复规则：none/daily/weekday/weekly/monthly"
    )
    description: Optional[str] = Field(None, description="备注")
    color: Optional[str] = Field(None, description="颜色标签")


class AgendaCreateInput(_AgendaFields):
    title: str = Field(..., description="日程标题")
    category: str = Field("other", description="分类：work/life/study/other")
    priority: int = Field(2, description="优先级：1=低/2=中/3=高")
    recurrence: str = Field(
        "none", description="重复：none/daily/weekday/weekly/monthly"
    )


class AgendaListInput(BaseModel):
    status: int = Field(-1, description="状态筛选：-1=全部/0=待办/1=进行中/2=已完成")
    category: str = Field("", description="分类筛选：work/life/study/other")
    search_keyword: str = Field("", description="关键词搜索标题和描述")
    start_date: str = Field("", description="开始时间范围起 YYYY-MM-DD")
    end_date: str = Field("", description="开始时间范围止 YYYY-MM-DD")
    limit: int = Field(20, description="返回数量上限")


class AgendaUpdateInput(_AgendaFields):
    """更新日程参数 Schema。

    字段语义（与前端 PATCH 一致，仅修改显式传入的有效字段）：
    - 未传或传 null：保持原值
    - 传空串：清空（仅字符串字段，title 除外因数据库 NOT NULL）
    - 传有效值：设为该值
    """

    id: int = Field(..., description="日程ID")
    title: str = Field("", description="新标题（不支持清空，传空串视为不变）")
    status: Optional[int] = Field(None, description="新状态：0=待办/1=进行中/2=已完成")


class AgendaDeleteInput(BaseModel):
    id: int = Field(..., description="日程ID")

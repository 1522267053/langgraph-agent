"""
日程管理工具集（AgendaToolKit）

从 agenda_handler.get_tool 抽出的 4 个工具：agenda_create / agenda_list /
agenda_update / agenda_delete。日程数据存储在 agenda 表中，通过
creator_name 区分用户。

闭包无 handler 状态依赖（纯 DB + get_current_username），构造函数零注入。

依赖方向：tools/* → app/services/*、app/models/*、app/config/*；
本模块不 import node_handlers。
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional, Sequence

from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from app.agent_flow.tools.base_abs_tool import BaseToolKit
from app.config.database import AsyncSessionLocal
from app.models.agenda import Agenda, AgendaCategory, AgendaRecurrence, AgendaStatus
from app.services.agenda_service import agenda_service
from app.utils.user_util import get_current_username
from app.schemas.base_schema import ChinaDateTime
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.models.flow_node import FlowNode

_VALID_CATEGORIES = {c.value for c in AgendaCategory}
_VALID_RECURRENCES = {r.value for r in AgendaRecurrence}
# LLM 可见的重复规则枚举说明（工具 description 与校验报错共用，加枚举值时同步）
_RECURRENCE_DESC = (
    "none(不重复)/daily(每日)/weekday(仅工作日)/weekly(每周)/monthly(每月)/"
    "weekend(每周六日)/holiday(法定节假日)/holiday_weekend(节假日和周六日)"
)

# ---- 描述单源（唯一维护点；tool_info 与 build_tools 共用）----
AGENDA_TOOL_DESCRIPTIONS: dict[str, str] = {
    "agenda_create": (
        "创建一条日程。title 必填，其他参数可选。"
        "时间格式：YYYY-MM-DD HH:MM:SS。"
        "category: work/life/study/other, priority: 1低/2中/3高, "
        f"recurrence: {_RECURRENCE_DESC}"
    ),
    "agenda_list": (
        "查询日程列表。支持按状态、分类筛选，支持按关键词搜索标题和描述，"
        "支持按 start_date/end_date 筛选时间范围（格式 YYYY-MM-DD）。"
        "status: -1全部/0待办/1进行中/2已完成。"
        "limit 控制返回数量（默认20）。返回字段含 recurrence/color/description。"
    ),
    "agenda_update": (
        "更新日程。id 必填，其他字段可选。"
        "字段语义：不传=保持原值；传 null 或空串=清空；传值=设为该值（title 不可清空）。"
        "status: 0=待办/1=进行中/2=已完成。"
        "  → 设为已完成自动写入完成时间；从已完成改回其他状态会清空完成时间并重置提醒标志。"
        f"recurrence: {_RECURRENCE_DESC}（修改后会重置重复生成锁）。"
        "修改 remind_at 会重置已推送标志，允许重新推送。"
        "color/location/description 支持清空（传 null 或空串）。"
    ),
    "agenda_delete": "删除日程。id 必填。",
}


# ---- 工具参数 Schema ----


class _AgendaFields(BaseModel):
    """议程工具共用字段基类"""

    start_time: Optional[ChinaDateTime] = Field(
        None, description="开始时间 YYYY-MM-DD HH:MM:SS"
    )
    end_time: Optional[ChinaDateTime] = Field(
        None, description="结束时间 YYYY-MM-DD HH:MM:SS"
    )
    category: Optional[str] = Field(
        default=None, description="分类：work/life/study/other"
    )
    priority: Optional[int] = Field(default=None, description="优先级：1=低/2=中/3=高")
    location: Optional[str] = Field(default=None, description="地点")
    remind_at: Optional[ChinaDateTime] = Field(
        default=None, description="提醒时间 YYYY-MM-DD HH:MM:SS"
    )
    recurrence: Optional[str] = Field(
        default=None, description=f"重复规则：{_RECURRENCE_DESC}"
    )
    description: Optional[str] = Field(default=None, description="备注")
    color: Optional[str] = Field(default=None, description="颜色标签")


class AgendaCreateInput(_AgendaFields):
    title: str = Field(..., description="日程标题")
    category: str = Field(default="other", description="分类：work/life/study/other")
    priority: int = Field(default=2, description="优先级：1=低/2=中/3=高")
    recurrence: str = Field(default="none", description=f"重复：{_RECURRENCE_DESC}")


class AgendaListInput(BaseModel):
    status: int = Field(
        default=-1, description="状态筛选：-1=全部/0=待办/1=进行中/2=已完成"
    )
    category: str = Field(default="", description="分类筛选：work/life/study/other")
    search_keyword: str = Field(default="", description="关键词搜索标题和描述")
    start_date: str = Field(default="", description="开始时间范围起 YYYY-MM-DD")
    end_date: str = Field(default="", description="开始时间范围止 YYYY-MM-DD")
    limit: int = Field(default=20, description="返回数量上限")


class AgendaUpdateInput(_AgendaFields):
    """更新日程参数 Schema。

    字段语义（与前端 PATCH 一致，仅修改显式传入的有效字段）：
    - 未传或传 null：保持原值
    - 传空串：清空（仅字符串字段，title 除外因数据库 NOT NULL）
    - 传有效值：设为该值
    """

    id: int = Field(..., description="日程ID")
    title: str = Field(default="", description="新标题（不支持清空，传空串视为不变）")
    status: Optional[int] = Field(
        default=None, description="新状态：0=待办/1=进行中/2=已完成"
    )


class AgendaDeleteInput(BaseModel):
    id: int = Field(..., description="日程ID")


class AgendaToolKit(BaseToolKit):
    """日程管理工具集（4 个工具）

    闭包无 handler 状态依赖，构造函数零注入。
    """

    async def build_tools(self) -> Sequence[StructuredTool]:
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
                    "message": f"无效重复规则: {recurrence}（有效值: {_RECURRENCE_DESC}）",
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

        return [
            StructuredTool(
                name="agenda_create",
                description=AGENDA_TOOL_DESCRIPTIONS["agenda_create"],
                func=None,
                coroutine=create_agenda,
                args_schema=AgendaCreateInput,
            ),
            StructuredTool(
                name="agenda_list",
                description=AGENDA_TOOL_DESCRIPTIONS["agenda_list"],
                func=None,
                coroutine=list_agendas,
                args_schema=AgendaListInput,
            ),
            StructuredTool(
                name="agenda_update",
                description=AGENDA_TOOL_DESCRIPTIONS["agenda_update"],
                func=None,
                coroutine=update_agenda,
                args_schema=AgendaUpdateInput,
            ),
            StructuredTool(
                name="agenda_delete",
                description=AGENDA_TOOL_DESCRIPTIONS["agenda_delete"],
                func=None,
                coroutine=delete_agenda,
                args_schema=AgendaDeleteInput,
            ),
        ]

    @classmethod
    def tool_info(cls, node: "FlowNode") -> list[dict]:
        """从描述常量派生（单源：与 build_tools 产出逐字节一致）"""
        return [
            {"name": n, "description": d} for n, d in AGENDA_TOOL_DESCRIPTIONS.items()
        ]

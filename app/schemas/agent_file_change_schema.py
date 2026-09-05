"""
Agent 文件变更相关 Pydantic Schema（侧栏 Diff 面板 / 单条撤销接口用）

定义：
- AgentFileChangeListItem：list_file_changes 接口的单条返回
- AgentFileChangeListResponse：list_file_changes 顶层返回 { list, total }
- AgentFileChangeDiffResponse：get_file_change_diff 接口的返回
- AgentFileChangeRevertItem：revert_file_change 接口的单条结果

所有 schema 都使用 ApiResponse[T] 泛型包装以保持与项目其他接口一致。
时间字段统一使用项目公共类型 ChinaDateTime（格式 2026-09-05 10:03:28）。
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base_schema import ChinaDateTime


class AgentFileChangeListItem(BaseModel):
    """list_file_changes 接口的单条变更项（侧栏 Diff 面板列表用）

    字段对齐前端 AgentFileChangeListItem 类型（frontend/src/types/agent.ts）。
    不继承 BaseView 是因为 AgentFileChange 模型没有 creator/modifier 字段。
    """

    id: int = Field(..., description="变更记录 ID")
    file_path: str = Field(..., description="被变更文件的绝对路径")
    change_type: str = Field(
        ...,
        description="变更类型：create=新建（回退时删除），modify=修改（回退时还原备份），delete=删除（回退时还原文件）",
    )
    tool_name: str = Field(..., description="产生变更的工具名")
    create_time: Optional[ChinaDateTime] = Field(default=None, description="创建时间")
    is_reverted: int = Field(
        default=0,
        description="是否已随消息回退：0=未回退，1=已回退",
    )
    has_backup: bool = Field(
        default=False,
        description="是否存在备份（modify/delete 时为 True，可用于回退）",
    )

    model_config = ConfigDict(
        from_attributes=True,
        validate_assignment=True,
        json_schema_extra={
            "example": {
                "id": 4,
                "file_path": "D:/work/example.html",
                "change_type": "create",
                "tool_name": "file_write",
                "create_time": "2026-09-05 10:03:28",
                "is_reverted": 0,
                "has_backup": False,
            }
        },
    )

    @classmethod
    def model_to_view(cls, model_instance) -> "AgentFileChangeListItem":
        """从 ORM 模型构造 schema 实例（直接拷贝字段，避免 BaseView 适配问题）"""
        return cls(
            id=model_instance.id,
            file_path=model_instance.file_path,
            change_type=model_instance.change_type,
            tool_name=model_instance.tool_name,
            create_time=model_instance.create_time,
            is_reverted=model_instance.is_reverted,
            has_backup=bool(model_instance.backup_path),
        )


class AgentFileChangeListResponse(BaseModel):
    """list_file_changes 顶层返回结构

    字段命名刻意与既有前端契约保持一致：list / total。
    如未来要切到标准分页（PaginatedResponse 风格），再扩展 page / page_size。
    """

    list: List[AgentFileChangeListItem] = Field(
        default_factory=list, description="文件变更列表（按 create_time 倒序）"
    )
    total: int = Field(default=0, description="列表条目数")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "list": [
                    {
                        "id": 4,
                        "file_path": "D:/work/example.html",
                        "change_type": "create",
                        "tool_name": "file_write",
                        "create_time": "2026-09-05 10:03:28",
                        "is_reverted": 0,
                        "has_backup": False,
                    }
                ],
                "total": 1,
            }
        }
    )


class AgentFileChangeDiffResponse(BaseModel):
    """get_file_change_diff 接口返回：backup + current 内容（前端自行渲染 diff）"""

    change_id: int = Field(..., description="文件变更记录 ID")
    file_path: str = Field(..., description="被变更文件的绝对路径")
    change_type: str = Field(..., description="变更类型：create/modify/delete")
    tool_name: str = Field(..., description="产生变更的工具名")
    backup_content: str = Field(default="", description="修改前内容（create 时为空）")
    current_content: str = Field(default="", description="修改后内容（delete 时为空）")
    is_binary: bool = Field(default=False, description="是否为二进制文件")
    backup_missing: bool = Field(default=False, description="备份文件是否已过期或丢失")
    backup_size: int = Field(default=0, description="备份内容字节数")
    current_size: int = Field(default=0, description="当前内容字节数")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "change_id": 4,
                "file_path": "D:/work/example.html",
                "change_type": "modify",
                "tool_name": "text_editor",
                "backup_content": "<h1>old</h1>",
                "current_content": "<h1>new</h1>",
                "is_binary": False,
                "backup_missing": False,
                "backup_size": 13,
                "current_size": 13,
            }
        }
    )


class AgentFileChangeRevertItem(BaseModel):
    """revert_file_change 接口的单条结果"""

    file_path: str = Field(..., description="被恢复文件的绝对路径")
    change_type: str = Field(..., description="变更类型：create/modify/delete")
    tool_name: str = Field(..., description="产生变更的工具名")
    status: str = Field(
        default="ok",
        description="恢复状态：ok / backup_missing / failed / already_reverted / unknown_type",
    )



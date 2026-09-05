"""
Agent 文件变更相关 Pydantic Schema（侧栏 Diff 面板 / 单条撤销接口用）

定义：
- AgentFileChangeQuery：page 分页接口的等值过滤条件
- AgentFileChangeBase：page 分页接口的单条返回
- AgentFileChangeDiffResponse：get_file_change_diff 接口的返回
- AgentFileChangeRevertItem：revert_file_change 接口的单条结果

所有 schema 都使用 ApiResponse[T] 泛型包装以保持与项目其他接口一致。
时间字段统一使用项目公共类型 ChinaDateTime（格式 2026-09-05 10:03:28）。
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.schemas.base_schema import BaseView


class AgentFileChangeQuery(BaseView):
    """page 分页接口的等值过滤条件（is_reverted 由 service 强制为 0）"""

    session_id: Optional[int] = Field(default=None, description="会话 ID")


class AgentFileChangeBase(BaseView):
    """page 分页接口的单条变更项（侧栏 Diff 面板列表用）

    字段对齐前端 AgentFileChangeBase 类型（frontend/src/types/agent.ts）。
    继承 BaseView 提供 id/create_time 等公共字段（creator/modifier 为
    Optional，模型无此字段留空即可）；转换直接复用 BaseView 的
    model_to_view/model_to_view_batch（from_attributes 按字段名拷贝）。
    """

    file_path: str = Field(..., description="被变更文件的绝对路径")
    change_type: str = Field(
        ...,
        description="变更类型：create=新建（回退时删除），modify=修改（回退时还原备份），delete=删除（回退时还原文件）",
    )
    tool_name: str = Field(..., description="产生变更的工具名")
    is_reverted: int = Field(
        default=0,
        description="是否已随消息回退：0=未回退，1=已回退",
    )
    # 内部转换用：from_attributes 时由 ORM 填充，不随接口输出
    backup_path: Optional[str] = Field(default=None, exclude=True)

    @computed_field
    @property
    def has_backup(self) -> bool:
        """是否存在备份（modify/delete 时为 True，可用于回退）"""
        return self.backup_path is not None


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

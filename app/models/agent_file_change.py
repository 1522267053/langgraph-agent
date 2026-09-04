"""
Agent 文件变更记录模型

记录 Agent 对话过程中 AI 工具对文件系统的修改（创建/修改），
用于"回退消息"时同步恢复被更改的文件。

注意：shell_executor 执行的任意命令产生的文件变更无法追踪，不在记录范围内。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_model import DbBaseModel


class AgentFileChange(DbBaseModel):
    """
    Agent 文件变更记录表模型

    每次工具写入文件前记录一条变更（modify 先备份原文件，create 无需备份），
    回退消息时按时间边界取变更记录逆序恢复。
    """

    __tablename__ = "agent_file_change"

    session_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="会话ID")
    run_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="执行批次标识（排查用，不参与回退定位）"
    )
    tool_name: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="产生变更的工具名：file_write/text_editor/save_file/upload_to_file_manager",
    )
    file_path: Mapped[str] = mapped_column(
        String(1000), nullable=False, comment="被变更文件的绝对路径"
    )
    file_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="关联 File 表记录ID（产物文件），回退时一并软删"
    )
    change_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="变更类型：create=新建文件，modify=修改已有文件，delete=删除文件（回退时还原）",
    )
    backup_path: Mapped[Optional[str]] = mapped_column(
        String(1000),
        nullable=True,
        comment="原文件备份路径（create 为空，modify/delete 必有），恢复时写回",
    )
    is_reverted: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="是否已随消息回退：0=未回退，1=已回退",
    )
    revert_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="回退时间"
    )

    def __repr__(self) -> str:
        return (
            f"<AgentFileChange(id={self.id}, session_id={self.session_id}, "
            f"file_path={self.file_path}, change_type={self.change_type})>"
        )

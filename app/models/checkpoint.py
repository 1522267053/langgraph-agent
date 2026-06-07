"""
LangGraph Checkpoint 持久化模型

用于存储 LangGraph 的 checkpoint 状态，支持 interrupt/resume 机制
"""

from typing import Optional
from sqlalchemy import String, Integer, Index, Text, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class CheckpointModel(DbBaseModel):
    """
    LangGraph Checkpoint 存储表

    存储流程执行的 checkpoint 快照
    """

    __tablename__ = "langgraph_checkpoint"

    thread_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="线程ID（对应 execution_id）"
    )
    checkpoint_ns: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="Checkpoint 命名空间"
    )
    checkpoint_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="Checkpoint ID（时间戳排序）"
    )
    parent_checkpoint_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="父 Checkpoint ID"
    )
    checkpoint_data: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False, comment="序列化的 Checkpoint 数据"
    )
    metadata_data: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary, nullable=True, comment="序列化的元数据"
    )

    __table_args__ = (
        Index("ix_langgraph_checkpoint_thread_ns", "thread_id", "checkpoint_ns"),
        Index(
            "ix_langgraph_checkpoint_thread_ns_id",
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
        ),
    )

    def __repr__(self) -> str:
        return f"<CheckpointModel(thread_id={self.thread_id}, checkpoint_id={self.checkpoint_id})>"


class CheckpointWrite(DbBaseModel):
    """
    LangGraph Checkpoint Write 存储表

    存储与 checkpoint 关联的中间写入
    """

    __tablename__ = "langgraph_checkpoint_write"

    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="线程ID")
    checkpoint_ns: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="Checkpoint 命名空间"
    )
    checkpoint_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="Checkpoint ID"
    )
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="任务ID")
    task_path: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="任务路径"
    )
    write_idx: Mapped[int] = mapped_column(Integer, nullable=False, comment="写入索引")
    channel: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="通道名称"
    )
    write_data: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False, comment="序列化的写入数据"
    )

    __table_args__ = (
        Index(
            "ix_langgraph_write_thread_checkpoint",
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
        ),
    )

    def __repr__(self) -> str:
        return f"<CheckpointWrite(thread_id={self.thread_id}, checkpoint_id={self.checkpoint_id}, channel={self.channel})>"


class CheckpointBlob(DbBaseModel):
    """
    LangGraph Checkpoint Blob 存储表

    存储通道的大对象数据
    """

    __tablename__ = "langgraph_checkpoint_blob"

    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="线程ID")
    checkpoint_ns: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="Checkpoint 命名空间"
    )
    channel: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="通道名称"
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False, comment="版本号")
    blob_data: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary, nullable=True, comment="序列化的 Blob 数据"
    )

    __table_args__ = (
        Index(
            "ix_langgraph_blob_thread_ns_channel",
            "thread_id",
            "checkpoint_ns",
            "channel",
            "version",
        ),
    )

    def __repr__(self) -> str:
        return f"<CheckpointBlob(thread_id={self.thread_id}, channel={self.channel}, version={self.version})>"

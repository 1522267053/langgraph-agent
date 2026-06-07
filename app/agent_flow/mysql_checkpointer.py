"""
MySQL Checkpointer for LangGraph

实现基于 MySQL 的 LangGraph Checkpoint 持久化存储
支持 interrupt/resume 机制
"""

from collections.abc import AsyncIterator, Sequence
from typing import Any
import random
import gzip

import ormsgpack
from langchain_core.runnables import RunnableConfig
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    ChannelVersions,
    get_checkpoint_id,
    WRITES_IDX_MAP,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.config.database import AsyncSessionLocal
from app.models.checkpoint import CheckpointModel, CheckpointWrite, CheckpointBlob


class AsyncMySQLSaver(BaseCheckpointSaver[str]):
    """
    MySQL 异步 Checkpointer

    使用 MySQL 数据库持久化存储 LangGraph 的 checkpoint 状态
    支持多轮人工交互的 interrupt/resume 机制

    Example:
        ```python
        from app.agent_flow.mysql_checkpointer import AsyncMySQLSaver

        checkpointer = AsyncMySQLSaver()
        graph = builder.compile(checkpointer=checkpointer)

        # 执行时传入 thread_id
        config = {"configurable": {"thread_id": str(execution_id)}}
        async for event in graph.astream(state, config):
            ...

        # 恢复执行
        from langgraph.types import Command
        async for event in graph.astream(Command(resume=user_input), config):
            ...
        ```
    """

    def __init__(self):
        super().__init__(serde=JsonPlusSerializer())

    def _serialize_value(self, value: Any) -> bytes:
        """
        序列化值：先用 serde.dumps_typed 得到 (type, data)，
        再用 msgpack 序列化整个 tuple，最后 gzip 压缩
        """
        type_and_data = self.serde.dumps_typed(value)
        packed = ormsgpack.packb(type_and_data)
        return gzip.compress(packed)

    def _deserialize_value(self, data: bytes) -> Any:
        """
        反序列化值：先检测并解压 gzip，再用 msgpack 反序列化得到 (type, data)，
        再调用 serde.loads_typed
        """
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        type_and_data = ormsgpack.unpackb(data)
        return self.serde.loads_typed(type_and_data)

    async def _load_blobs(
        self,
        db: AsyncSession,
        thread_id: str,
        checkpoint_ns: str,
        versions: ChannelVersions,
    ) -> dict[str, Any]:
        """加载 blob 数据"""
        channel_values: dict[str, Any] = {}
        for channel, version in versions.items():
            query = select(CheckpointBlob).where(
                CheckpointBlob.thread_id == thread_id,
                CheckpointBlob.checkpoint_ns == checkpoint_ns,
                CheckpointBlob.channel == channel,
                CheckpointBlob.version == version,
                CheckpointBlob.is_delete == 0,
            )
            result = await db.execute(query)
            blob = result.scalar_one_or_none()
            if blob and blob.blob_data:
                loaded = self._deserialize_value(blob.blob_data)
                channel_values[channel] = loaded
        return channel_values

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """
        异步获取 checkpoint 元组

        根据 thread_id 和可选的 checkpoint_id 查询 checkpoint
        """
        thread_id: str = config["configurable"]["thread_id"]
        checkpoint_ns: str = config["configurable"].get("checkpoint_ns", "")

        async with AsyncSessionLocal() as db:
            checkpoint_id = get_checkpoint_id(config)

            if checkpoint_id:
                # 查询指定的 checkpoint
                query = select(CheckpointModel).where(
                    CheckpointModel.thread_id == thread_id,
                    CheckpointModel.checkpoint_ns == checkpoint_ns,
                    CheckpointModel.checkpoint_id == checkpoint_id,
                    CheckpointModel.is_delete == 0,
                )
                result = await db.execute(query)
                checkpoint_row = result.scalar_one_or_none()
            else:
                # 查询最新的 checkpoint（按 checkpoint_id 降序）
                query = (
                    select(CheckpointModel)
                    .where(
                        CheckpointModel.thread_id == thread_id,
                        CheckpointModel.checkpoint_ns == checkpoint_ns,
                        CheckpointModel.is_delete == 0,
                    )
                    .order_by(CheckpointModel.checkpoint_id.desc())
                    .limit(1)
                )
                result = await db.execute(query)
                checkpoint_row = result.scalar_one_or_none()
                if checkpoint_row:
                    checkpoint_id = checkpoint_row.checkpoint_id

            if not checkpoint_row:
                return None

            # 反序列化 checkpoint 和 metadata
            checkpoint_data = self._deserialize_value(checkpoint_row.checkpoint_data)
            metadata = None
            if checkpoint_row.metadata_data:
                metadata = self._deserialize_value(checkpoint_row.metadata_data)

            # 加载 blob 数据
            channel_values = await self._load_blobs(
                db,
                thread_id,
                checkpoint_ns,
                checkpoint_data.get("channel_versions", {}),
            )

            # 查询 pending writes
            writes_query = select(CheckpointWrite).where(
                CheckpointWrite.thread_id == thread_id,
                CheckpointWrite.checkpoint_ns == checkpoint_ns,
                CheckpointWrite.checkpoint_id == checkpoint_id,
                CheckpointWrite.is_delete == 0,
            )
            writes_result = await db.execute(writes_query)
            writes_rows = writes_result.scalars().all()

            pending_writes = [
                (row.task_id, row.channel, self._deserialize_value(row.write_data))
                for row in writes_rows
            ]

            # 构建 parent_config
            parent_config = None
            if checkpoint_row.parent_checkpoint_id:
                parent_config = {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": checkpoint_row.parent_checkpoint_id,
                    }
                }

            return CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": checkpoint_id,
                    }
                },
                checkpoint={
                    **checkpoint_data,
                    "channel_values": channel_values,
                },
                metadata=metadata or {},
                parent_config=parent_config,
                pending_writes=pending_writes if pending_writes else None,
            )

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """
        异步列出匹配条件的 checkpoints
        """
        async with AsyncSessionLocal() as db:
            thread_ids = (
                (config["configurable"]["thread_id"],)
                if config and "thread_id" in config.get("configurable", {})
                else None
            )

            if thread_ids is None:
                # 获取所有 thread_id
                query = (
                    select(CheckpointModel.thread_id)
                    .distinct()
                    .where(CheckpointModel.is_delete == 0)
                )
                result = await db.execute(query)
                thread_ids = tuple(row[0] for row in result.fetchall())

            config_checkpoint_ns = (
                config["configurable"].get("checkpoint_ns") if config else None
            )
            config_checkpoint_id = get_checkpoint_id(config) if config else None
            before_checkpoint_id = get_checkpoint_id(before) if before else None

            count = 0
            for thread_id in thread_ids:
                query = select(CheckpointModel).where(
                    CheckpointModel.thread_id == thread_id,
                    CheckpointModel.is_delete == 0,
                )

                if config_checkpoint_ns is not None:
                    query = query.where(
                        CheckpointModel.checkpoint_ns == config_checkpoint_ns
                    )

                query = query.order_by(CheckpointModel.checkpoint_id.desc())

                result = await db.execute(query)
                checkpoints = result.scalars().all()

                for checkpoint_row in checkpoints:
                    # 过滤 checkpoint_id
                    if (
                        config_checkpoint_id
                        and checkpoint_row.checkpoint_id != config_checkpoint_id
                    ):
                        continue

                    # 过滤 before
                    if (
                        before_checkpoint_id
                        and checkpoint_row.checkpoint_id >= before_checkpoint_id
                    ):
                        continue

                    # 反序列化 metadata 用于过滤
                    metadata = {}
                    if checkpoint_row.metadata_data:
                        metadata = self._deserialize_value(checkpoint_row.metadata_data)

                    # 过滤 metadata
                    if filter and not all(
                        query_value == metadata.get(query_key)
                        for query_key, query_value in filter.items()
                    ):
                        continue

                    # 限制数量
                    if limit is not None and count >= limit:
                        return
                    count += 1

                    # 反序列化 checkpoint
                    checkpoint_data = self._deserialize_value(
                        checkpoint_row.checkpoint_data
                    )

                    # 加载 blobs
                    channel_values = await self._load_blobs(
                        db,
                        thread_id,
                        checkpoint_row.checkpoint_ns,
                        checkpoint_data.get("channel_versions", {}),
                    )

                    # 查询 pending writes
                    writes_query = select(CheckpointWrite).where(
                        CheckpointWrite.thread_id == thread_id,
                        CheckpointWrite.checkpoint_ns == checkpoint_row.checkpoint_ns,
                        CheckpointWrite.checkpoint_id == checkpoint_row.checkpoint_id,
                        CheckpointWrite.is_delete == 0,
                    )
                    writes_result = await db.execute(writes_query)
                    writes_rows = writes_result.scalars().all()

                    pending_writes = [
                        (
                            row.task_id,
                            row.channel,
                            self._deserialize_value(row.write_data),
                        )
                        for row in writes_rows
                    ]

                    # 构建 parent_config
                    parent_config = None
                    if checkpoint_row.parent_checkpoint_id:
                        parent_config = {
                            "configurable": {
                                "thread_id": thread_id,
                                "checkpoint_ns": checkpoint_row.checkpoint_ns,
                                "checkpoint_id": checkpoint_row.parent_checkpoint_id,
                            }
                        }

                    yield CheckpointTuple(
                        config={
                            "configurable": {
                                "thread_id": thread_id,
                                "checkpoint_ns": checkpoint_row.checkpoint_ns,
                                "checkpoint_id": checkpoint_row.checkpoint_id,
                            }
                        },
                        checkpoint={
                            **checkpoint_data,
                            "channel_values": channel_values,
                        },
                        metadata=metadata,
                        parent_config=parent_config,
                        pending_writes=pending_writes if pending_writes else None,
                    )

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """
        异步保存 checkpoint
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")

        async with AsyncSessionLocal() as db:
            # 准备 checkpoint 数据
            c = checkpoint.copy()
            channel_values = c.pop("channel_values", {})

            # 保存 blobs
            for channel, version in new_versions.items():
                if channel in channel_values:
                    blob_record = CheckpointBlob(
                        thread_id=thread_id,
                        checkpoint_ns=checkpoint_ns,
                        channel=channel,
                        version=str(version),
                        blob_data=self._serialize_value(channel_values[channel]),
                    )
                    db.add(blob_record)

            # 序列化 checkpoint 和 metadata
            checkpoint_data = self._serialize_value(c)
            metadata_data = self._serialize_value(metadata)

            # 获取父 checkpoint_id
            parent_checkpoint_id = config.get("configurable", {}).get("checkpoint_id")

            # 创建 checkpoint 记录
            checkpoint_record = CheckpointModel(
                thread_id=thread_id,
                checkpoint_ns=checkpoint_ns,
                checkpoint_id=checkpoint["id"],
                parent_checkpoint_id=parent_checkpoint_id,
                checkpoint_data=checkpoint_data,
                metadata_data=metadata_data,
            )
            db.add(checkpoint_record)
            await db.commit()

            return {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint["id"],
                }
            }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """
        异步保存 writes
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]

        async with AsyncSessionLocal() as db:
            # 查询已有的 writes
            existing_query = select(CheckpointWrite).where(
                CheckpointWrite.thread_id == thread_id,
                CheckpointWrite.checkpoint_ns == checkpoint_ns,
                CheckpointWrite.checkpoint_id == checkpoint_id,
                CheckpointWrite.task_id == task_id,
                CheckpointWrite.is_delete == 0,
            )
            existing_result = await db.execute(existing_query)
            existing_writes = {
                row.channel: row for row in existing_result.scalars().all()
            }

            for idx, (channel, value) in enumerate(writes):
                write_idx = WRITES_IDX_MAP.get(channel, idx)

                # 如果 write_idx >= 0 且已存在，跳过
                if write_idx >= 0 and channel in existing_writes:
                    continue

                serialized_data = self._serialize_value(value)

                if write_idx < 0 and channel in existing_writes:
                    # 更新已存在的特殊 write
                    existing_writes[channel].write_data = serialized_data
                else:
                    # 创建新的 write 记录
                    write_record = CheckpointWrite(
                        thread_id=thread_id,
                        checkpoint_ns=checkpoint_ns,
                        checkpoint_id=checkpoint_id,
                        task_id=task_id,
                        task_path=task_path,
                        write_idx=write_idx,
                        channel=channel,
                        write_data=serialized_data,
                    )
                    db.add(write_record)

            await db.commit()

    async def adelete_thread(self, thread_id: str) -> None:
        """
        异步删除指定线程的所有 checkpoints
        """
        async with AsyncSessionLocal() as db:
            # 删除 checkpoints（软删除）
            await db.execute(
                delete(CheckpointModel).where(CheckpointModel.thread_id == thread_id)
            )
            # 删除 writes
            await db.execute(
                delete(CheckpointWrite).where(CheckpointWrite.thread_id == thread_id)
            )
            # 删除 blobs
            await db.execute(
                delete(CheckpointBlob).where(CheckpointBlob.thread_id == thread_id)
            )
            await db.commit()

    def get_next_version(self, current: str | None, channel: None) -> str:
        """生成下一个版本号"""
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            try:
                current_v = int(current.split(".")[0])
            except (ValueError, IndexError):
                current_v = 0
        next_v = current_v + 1
        next_h = random.random()
        return f"{next_v:032}.{next_h:016}"

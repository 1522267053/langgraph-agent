"""
数据库表结构自动同步

启动时对比 SQLAlchemy 模型与实际表结构，自动为已存在的表补充缺失的列。

设计原则：
- 仅自动新增列（非破坏性操作，兼容 SQLite / MySQL）
- 不修改列类型、不删除多余列（避免数据丢失风险）
- 新增列一律可空（SQLite ADD COLUMN 限制 + 通用安全）

开发时给 model 加字段，重启即生效，无需手动迁移脚本。
"""

import logging

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.schema import MetaData, Table

logger = logging.getLogger(__name__)


def _collect_missing_columns(
    sync_conn, metadata: MetaData
) -> list[tuple[Table, object]]:
    """对比模型与实际表结构，收集缺失的列（同步函数，通过 run_sync 调用）

    两阶段处理的第一阶段：仅收集信息，不执行 DDL，避免 inspector 缓存与 ALTER 冲突。
    """
    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())
    missing: list[tuple[Table, object]] = []

    for table_name, table_obj in metadata.tables.items():
        # 仅处理已存在的表（新表由 create_all 创建）
        if table_name not in existing_tables:
            continue
        db_columns = {col["name"] for col in inspector.get_columns(table_name)}
        for column in table_obj.columns:
            if column.name not in db_columns:
                missing.append((table_obj, column))

    return missing


def _apply_missing_columns(sync_conn, missing: list[tuple[Table, object]]) -> list[str]:
    """为缺失列执行 ALTER TABLE ADD COLUMN（同步函数，通过 run_sync 调用）

    新增列一律可空（兼容 SQLite 的 ADD COLUMN 限制）。
    返回已新增的 "表名.列名" 列表。
    """
    dialect = sync_conn.dialect
    preparer = dialect.identifier_preparer
    added: list[str] = []

    for table_obj, column in missing:
        # 编译列类型 DDL（如 VARCHAR(255) / INTEGER / DATETIME）
        type_ddl = column.type.compile(dialect=dialect)
        quoted_table = preparer.quote(table_obj.name)
        quoted_col = preparer.quote(column.name)
        # 一律可空：SQLite 的 ADD COLUMN 不支持对已有数据的表加 NOT NULL 无默认值
        sql = text(
            f"ALTER TABLE {quoted_table} ADD COLUMN {quoted_col} {type_ddl} NULL"
        )
        sync_conn.execute(sql)
        added.append(f"{table_obj.name}.{column.name}")

    return added


async def validate_and_update_db_schema(
    engine: AsyncEngine, metadata: MetaData
) -> None:
    """自动同步表结构：为已存在的表补充缺失列

    Args:
        engine: 异步引擎
        metadata: SQLAlchemy 模型元数据（通常为 DbBaseModel.metadata）

    幂等：列已存在时跳过，可安全重复执行。
    """
    async with engine.begin() as conn:

        def _sync(sync_conn) -> list[str]:
            missing = _collect_missing_columns(sync_conn, metadata)
            if not missing:
                return []
            return _apply_missing_columns(sync_conn, missing)

        added = await conn.run_sync(_sync)

    if added:
        logger.info("自动同步表结构，新增 %d 列：%s", len(added), ", ".join(added))

"""
数据库连接管理
"""

import asyncio
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import and_, event
from sqlalchemy.orm import ORMExecuteState, Session

from app.config.settings import settings

# 导入 Base 类（从 base_model 导入，包含公共字段）
from app.models.base_model import DbBaseModel

# 创建异步引擎（根据数据库类型动态调整参数）
_engine_kwargs: dict = {
    "echo": settings.debug,
}
if settings.is_sqlite:
    _engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
else:
    _engine_kwargs["pool_pre_ping"] = True
    _engine_kwargs["pool_recycle"] = 3600
    _engine_kwargs["pool_size"] = settings.database_pool_size
    _engine_kwargs["max_overflow"] = settings.database_max_overflow

engine = create_async_engine(settings.database_url, **_engine_kwargs)

if settings.is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_wal(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


# 创建异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# 全局逻辑删除钩子（适用于 SQLAlchemy 2.x 异步模式）
@event.listens_for(Session, "do_orm_execute")
def apply_soft_delete_filter(execute_state: ORMExecuteState) -> None:
    """
    自动为所有 SELECT 查询添加逻辑删除过滤条件

    只会过滤未删除的记录（is_delete = 0）

    如果需要查询包含已删除的数据，可以使用以下方式：
        stmt = select(Model).execution_options(include_deleted=True)
    """
    # 只处理 SELECT 语句
    if not execute_state.is_select:
        return

    # 如果显式要求包含已删除数据，跳过过滤
    if execute_state.execution_options.get("include_deleted", False):
        return
    stmt = execute_state.statement
    where_clauses = []

    # 遍历所有查询涉及的实体（models）
    for ent in execute_state.all_mappers:
        entity = ent.class_
        # 检查实体是否有 is_delete 字段
        if not hasattr(entity, "is_delete"):
            continue
        where_clauses.append(entity.is_delete == 0)

    if not where_clauses and hasattr(stmt, "froms"):
        for from_clause in stmt.froms:  # type: ignore
            # 尝试获取底层 Table 对象
            table = None
            # 情况1: 来自 Core Table 或 select_from(Table)
            if hasattr(from_clause, "name") and hasattr(from_clause, "c"):
                table = from_clause

            # 情况2: 是 Alias（如子查询、join 别名）
            elif hasattr(from_clause, "original"):
                orig = from_clause.original
                if hasattr(orig, "name") and hasattr(orig, "c"):
                    table = orig

            # 如果找到了表，并且它启用了软删除
            if table is not None and "is_delete" in table.c:
                # 注意：必须确保该表有 is_delete 列！
                where_clauses.append(from_clause.c.is_delete == 0)

    if where_clauses:
        # 向 Select 语句添加 WHERE is_delete = 0
        execute_state.statement = stmt.where(and_(*where_clauses))  # type: ignore


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    获取数据库会话的依赖注入函数

    事务由 Service 层统一管理（commit），此函数仅负责会话生命周期。
    发生异常时自动回滚。

    Yields:
        AsyncSession: 数据库会话
    """
    session = AsyncSessionLocal()
    try:
        yield session
    except asyncio.CancelledError:
        try:
            await session.rollback()
        except Exception:
            pass
        raise
    except Exception:
        await session.rollback()
        raise
    finally:
        try:
            await session.close()
        except Exception:
            pass


async def init_db() -> None:
    """初始化数据库，创建所有表"""
    async with engine.begin() as conn:
        await conn.run_sync(DbBaseModel.metadata.create_all)

    # 校验并自动更新表结构
    # from app.utils.db_schema_validator import validate_and_update_db_schema
    # await validate_and_update_db_schema(engine, DbBaseModel)


async def close_db():
    """关闭数据库连接"""
    await engine.dispose()

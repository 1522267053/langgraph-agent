"""数据库工具函数"""

from sqlalchemy import func
from sqlalchemy.sql.expression import ColumnElement

from app.config.settings import settings


def date_trunc_expr(column: ColumnElement, grain: str = "day") -> ColumnElement:
    """跨数据库日期截断表达式（用于 GROUP BY）

    根据当前数据库类型生成兼容的日期截断 SQL 表达式。

    Args:
        column: 日期/时间列
        grain: 聚合粒度，支持 day/week/month
    """
    _FMT_MAP_SQLITE = {
        "day": "%Y-%m-%d",
        "week": "%Y-W%W",
        "month": "%Y-%m",
    }
    if settings.is_sqlite:
        return func.strftime(_FMT_MAP_SQLITE[grain], column)
    if grain == "day":
        return func.date(column)
    if grain == "week":
        return func.date_format(column, "%x-W%v")
    return func.date_format(column, "%Y-%m")

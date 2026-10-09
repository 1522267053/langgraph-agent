"""数据库工具函数"""

from typing import Any

from sqlalchemy import func
from sqlalchemy.sql.expression import ColumnElement


def date_trunc_expr(column: Any, grain: str = "day") -> ColumnElement[Any]:
    """SQLite 日期截断表达式（用于 GROUP BY）

    Args:
        column: 日期/时间列
        grain: 聚合粒度，支持 day/week/month
    """
    _FMT_MAP = {
        "day": "%Y-%m-%d",
        "week": "%Y-W%W",
        "month": "%Y-%m",
    }
    return func.strftime(_FMT_MAP[grain], column)

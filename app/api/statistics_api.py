"""Token 消耗统计 API"""

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.schemas.base_schema import ApiResponse
from app.schemas.statistics_schema import (
    TokenStatisticsQuery,
    TokenOverview,
    TokenTrendItem,
    TokenByFlowItem,
    TokenByModelItem,
)
from app.services.token_usage_service import token_usage_service

_TIME_GRAIN_OPTIONS = {"day", "week", "month"}

# 统计查询最大回看窗口（自然日）：防止日期留空退化为全表扫描，数据量增长后拖垮接口
MAX_QUERY_DAYS = 60
# 日期均留空时的默认回看窗口（自然日）
DEFAULT_QUERY_DAYS = 7


def _parse_date(value: Optional[str]) -> Optional[date]:
    """YYYY-MM-DD 字符串转 date；空串/None 返回 None"""
    return date.fromisoformat(value) if value else None


def normalize_date_range(
    start_date: Optional[str], end_date: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """日期窗口归一化，任何查询最多回看 MAX_QUERY_DAYS 天。

    - 均未填：最近 7 天（today-6 ~ today）
    - 仅填 start：end = min(today, start+59)
    - 仅填 end：start = end-59
    - 均填且跨度超窗：start = end-59（保留用户指定的结束日，截断更早数据）
    """
    s = _parse_date(start_date)
    e = _parse_date(end_date)
    today = date.today()
    max_window = timedelta(days=MAX_QUERY_DAYS - 1)
    default_window = timedelta(days=DEFAULT_QUERY_DAYS - 1)
    if s and e:
        if (e - s).days > MAX_QUERY_DAYS - 1:
            s = e - max_window
    elif s:
        e = min(today, s + max_window)
    elif e:
        s = e - max_window
    else:
        e = today
        s = e - default_window
    return s.isoformat(), e.isoformat()


class StatisticsApi:
    """Token 消耗统计 API"""

    def __init__(self):
        self.router = APIRouter(prefix="/api/statistics", tags=["Token统计"])
        self._register_routes()

    def _register_routes(self):
        """注册所有路由"""

        @self.router.post("/token-overview", summary="Token消耗概览")
        async def get_token_overview(
            query: Optional[TokenStatisticsQuery] = None,
            db: AsyncSession = Depends(get_db),
        ):
            """获取Token消耗总量概览"""
            q = query or TokenStatisticsQuery()
            start_date, end_date = normalize_date_range(q.start_date, q.end_date)
            data = await token_usage_service.aggregate_overview(
                db, start_date=start_date, end_date=end_date
            )
            return ApiResponse.success(data=TokenOverview(**data), msg="查询成功")

        @self.router.post("/token-trend", summary="Token消耗时间趋势")
        async def get_token_trend(
            query: Optional[TokenStatisticsQuery] = None,
            db: AsyncSession = Depends(get_db),
        ):
            """获取Token消耗按时间维度的趋势数据"""
            q = query or TokenStatisticsQuery()
            grain = q.time_grain if q.time_grain in _TIME_GRAIN_OPTIONS else "day"
            start_date, end_date = normalize_date_range(q.start_date, q.end_date)
            items = await token_usage_service.aggregate_trend(
                db, grain=grain, start_date=start_date, end_date=end_date
            )
            data = [TokenTrendItem(**item) for item in items]
            return ApiResponse.success(data=data, msg="查询成功")

        @self.router.post("/token-by-flow", summary="按流程/Agent统计Token")
        async def get_token_by_flow(
            query: Optional[TokenStatisticsQuery] = None,
            db: AsyncSession = Depends(get_db),
        ):
            """获取按流程/Agent维度的Token消耗排行"""
            q = query or TokenStatisticsQuery()
            start_date, end_date = normalize_date_range(q.start_date, q.end_date)
            items = await token_usage_service.aggregate_by_flow(
                db, start_date=start_date, end_date=end_date
            )
            data = [TokenByFlowItem(**item) for item in items]
            return ApiResponse.success(data=data, msg="查询成功")

        @self.router.post("/token-by-model", summary="按模型统计Token")
        async def get_token_by_model(
            query: Optional[TokenStatisticsQuery] = None,
            db: AsyncSession = Depends(get_db),
        ):
            """获取按模型维度的Token消耗统计"""
            q = query or TokenStatisticsQuery()
            start_date, end_date = normalize_date_range(q.start_date, q.end_date)
            items = await token_usage_service.aggregate_by_model(
                db, start_date=start_date, end_date=end_date
            )
            data = [TokenByModelItem(**item) for item in items]
            return ApiResponse.success(data=data, msg="查询成功")


statistics_api = StatisticsApi()
router = statistics_api.router

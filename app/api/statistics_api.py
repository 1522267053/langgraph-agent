"""Token 消耗统计 API"""

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
            data = await token_usage_service.aggregate_overview(
                db, start_date=q.start_date, end_date=q.end_date
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
            items = await token_usage_service.aggregate_trend(
                db, grain=grain, start_date=q.start_date, end_date=q.end_date
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
            items = await token_usage_service.aggregate_by_flow(
                db, start_date=q.start_date, end_date=q.end_date
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
            items = await token_usage_service.aggregate_by_model(
                db, start_date=q.start_date, end_date=q.end_date
            )
            data = [TokenByModelItem(**item) for item in items]
            return ApiResponse.success(data=data, msg="查询成功")


statistics_api = StatisticsApi()
router = statistics_api.router

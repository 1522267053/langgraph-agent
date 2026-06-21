"""Token 使用记录服务"""

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.token_usage import TokenUsage

logger = logging.getLogger(__name__)


class TokenUsageService:
    """Token 使用记录服务"""

    async def record_usage(
        self,
        db: AsyncSession,
        *,
        source_type: str,
        source_id: int,
        node_key: str,
        model: str = "",
        provider: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        usage_metadata: Optional[dict] = None,
    ) -> None:
        """记录一次 LLM 调用的 token 用量"""
        record = TokenUsage(
            source_type=source_type,
            source_id=source_id,
            node_key=node_key,
            model=model,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            reasoning_tokens=reasoning_tokens,
            usage_metadata=usage_metadata,
        )
        db.add(record)
        await db.commit()

    async def aggregate_overview(
        self,
        db: AsyncSession,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """汇总 token 用量概览"""
        query = select(
            func.coalesce(func.sum(TokenUsage.prompt_tokens), 0),
            func.coalesce(func.sum(TokenUsage.completion_tokens), 0),
            func.coalesce(func.sum(TokenUsage.total_tokens), 0),
            func.count(TokenUsage.id),
        )
        query = self._apply_date_range(query, start_date, end_date)
        result = await db.execute(query)
        row = result.one()
        return {
            "total_prompt_tokens": int(row[0]),
            "total_completion_tokens": int(row[1]),
            "total_tokens": int(row[2]),
            "llm_call_count": int(row[3]),
        }

    async def aggregate_trend(
        self,
        db: AsyncSession,
        grain: str = "day",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict]:
        """按时间维度聚合 token 趋势"""
        from app.utils.database_util import date_trunc_expr

        date_expr = date_trunc_expr(TokenUsage.create_time, grain)
        query = select(
            date_expr.label("date"),
            func.coalesce(func.sum(TokenUsage.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(TokenUsage.completion_tokens), 0).label(
                "completion_tokens"
            ),
            func.coalesce(func.sum(TokenUsage.total_tokens), 0).label("total_tokens"),
        )
        query = self._apply_date_range(query, start_date, end_date)
        query = query.group_by(date_expr).order_by(date_expr)
        result = await db.execute(query)
        return [
            {
                "date": str(row[0]),
                "prompt_tokens": int(row[1]),
                "completion_tokens": int(row[2]),
                "total_tokens": int(row[3]),
            }
            for row in result
        ]

    async def aggregate_by_flow(
        self,
        db: AsyncSession,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict]:
        """按流程/Agent 聚合 token 用量"""
        from app.models.agent_session import AgentSession
        from app.models.flow import Flow
        from app.models.flow_execution import FlowExecution

        # ---- 子查询：token_usage JOIN flow_execution/agent_session，按 flow_id 聚合 ----
        flow_subq = (
            select(
                FlowExecution.flow_id.label("flow_id"),
                func.sum(TokenUsage.prompt_tokens).label("prompt_tokens"),
                func.sum(TokenUsage.completion_tokens).label("completion_tokens"),
                func.sum(TokenUsage.total_tokens).label("total_tokens"),
                func.count(TokenUsage.id).label("call_count"),
            )
            .join(FlowExecution, TokenUsage.source_id == FlowExecution.id)
            .where(TokenUsage.source_type == "flow")
            .group_by(FlowExecution.flow_id)
        )
        flow_subq = self._apply_date_range(flow_subq, start_date, end_date)

        agent_subq = (
            select(
                AgentSession.flow_id.label("flow_id"),
                func.sum(TokenUsage.prompt_tokens).label("prompt_tokens"),
                func.sum(TokenUsage.completion_tokens).label("completion_tokens"),
                func.sum(TokenUsage.total_tokens).label("total_tokens"),
                func.count(TokenUsage.id).label("call_count"),
            )
            .join(AgentSession, TokenUsage.source_id == AgentSession.id)
            .where(TokenUsage.source_type == "agent")
            .group_by(AgentSession.flow_id)
        )
        agent_subq = self._apply_date_range(agent_subq, start_date, end_date)

        combined = flow_subq.union_all(agent_subq).subquery()

        # ---- 外层：合并 flow/agent 结果，按 flow_id 再聚合 ----
        query = (
            select(
                combined.c.flow_id,
                func.coalesce(func.sum(combined.c.prompt_tokens), 0).label(
                    "prompt_tokens"
                ),
                func.coalesce(func.sum(combined.c.completion_tokens), 0).label(
                    "completion_tokens"
                ),
                func.coalesce(func.sum(combined.c.total_tokens), 0).label(
                    "total_tokens"
                ),
                func.coalesce(func.sum(combined.c.call_count), 0).label("call_count"),
            )
            .group_by(combined.c.flow_id)
            .order_by(func.sum(combined.c.total_tokens).desc())
        )

        result = await db.execute(query)
        rows = list(result)
        flow_ids = [row[0] for row in rows]

        flow_name_map = {}
        flow_type_map = {}
        if flow_ids:
            name_query = select(Flow.id, Flow.name, Flow.flow_type).where(
                Flow.id.in_(flow_ids), Flow.is_delete == 0
            )
            name_result = await db.execute(name_query)
            for row in name_result:
                flow_name_map[row[0]] = row[1]
                flow_type_map[row[0]] = row[2]

        return [
            {
                "flow_id": row[0],
                "flow_name": flow_name_map.get(row[0]) or f"(已删除)#{row[0]}",
                "flow_type": flow_type_map.get(row[0], ""),
                "prompt_tokens": int(row[1]),
                "completion_tokens": int(row[2]),
                "total_tokens": int(row[3]),
                "call_count": int(row[4]),
            }
            for row in rows
        ]

    async def aggregate_by_model(
        self,
        db: AsyncSession,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict]:
        """按模型聚合 token 用量"""
        query = select(
            TokenUsage.model,
            TokenUsage.provider,
            func.coalesce(func.sum(TokenUsage.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(TokenUsage.completion_tokens), 0).label(
                "completion_tokens"
            ),
            func.coalesce(func.sum(TokenUsage.total_tokens), 0).label("total_tokens"),
            func.count(TokenUsage.id).label("call_count"),
            func.coalesce(func.sum(TokenUsage.cache_read_tokens), 0).label(
                "cache_read_tokens"
            ),
            func.coalesce(func.sum(TokenUsage.cache_write_tokens), 0).label(
                "cache_write_tokens"
            ),
            func.coalesce(func.sum(TokenUsage.reasoning_tokens), 0).label(
                "reasoning_tokens"
            ),
        )
        query = self._apply_date_range(query, start_date, end_date)
        query = query.group_by(TokenUsage.model, TokenUsage.provider).order_by(
            func.sum(TokenUsage.total_tokens).desc()
        )
        result = await db.execute(query)
        return [
            {
                "model": row[0] or "",
                "provider": row[1] or "",
                "prompt_tokens": int(row[2]),
                "completion_tokens": int(row[3]),
                "total_tokens": int(row[4]),
                "call_count": int(row[5]),
                "cache_read_tokens": int(row[6]),
                "cache_write_tokens": int(row[7]),
                "reasoning_tokens": int(row[8]),
            }
            for row in result
        ]

    def _apply_date_range(self, query, start_date, end_date):
        """为查询添加日期范围过滤"""
        from datetime import datetime, timedelta

        if start_date:
            try:
                query = query.where(
                    TokenUsage.create_time >= datetime.strptime(start_date, "%Y-%m-%d")
                )
            except ValueError:
                pass
        if end_date:
            try:
                query = query.where(
                    TokenUsage.create_time
                    < datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
                )
            except ValueError:
                pass
        return query


token_usage_service = TokenUsageService()

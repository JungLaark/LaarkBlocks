"""트레이스 저장소 — 적재/조회/집계/평가.

Pydantic(TraceData) ↔ ORM(TraceRow) 변환을 이 계층에서만 수행한다.
엔진과 API 는 각각 Pydantic 스키마만 다루므로 저장소 구현(SQLite/PG)이
바뀌어도 영향이 없다.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import case, func, select

from src.db.database import database
from src.db.models import EvaluationRow, SpanRow, TraceRow
from src.schemas.tracing import (
    AgentUsageStats,
    EvaluationIn,
    EvaluationOut,
    SpanData,
    TraceData,
    TraceSummary,
    UsageStats,
)


class TraceRepository:
    """실행 이력(트레이스) CRUD 및 집계."""

    # ── 적재 (tracing.emit 의 sink 로 연결됨) ────────────────────

    async def save_trace(self, trace: TraceData) -> None:
        async with database.session() as session:
            row = TraceRow(
                trace_id=trace.trace_id,
                agent_id=trace.agent_id,
                session_id=trace.session_id,
                model_name=trace.model_name,
                user_message=trace.user_message,
                final_response=trace.final_response,
                status=trace.status.value,
                error=trace.error,
                started_at=trace.started_at,
                ended_at=trace.ended_at,
                latency_ms=trace.latency_ms,
                input_tokens=trace.input_tokens,
                output_tokens=trace.output_tokens,
                cost_usd=trace.cost_usd,
                spans=[
                    SpanRow(
                        span_id=s.span_id,
                        span_type=s.span_type.value,
                        name=s.name,
                        input=s.input,
                        output=s.output,
                        is_worker=int(s.is_worker),
                        started_at=s.started_at,
                        ended_at=s.ended_at,
                        latency_ms=s.latency_ms,
                        input_tokens=s.input_tokens,
                        output_tokens=s.output_tokens,
                        cost_usd=s.cost_usd,
                    )
                    for s in trace.spans
                ],
            )
            session.add(row)
            await session.commit()

    # ── 조회 ─────────────────────────────────────────────────────

    async def list_traces(
        self,
        agent_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TraceSummary]:
        """최신순 트레이스 목록 (운영 콘솔의 실행 이력 테이블)."""
        stmt = select(TraceRow).order_by(TraceRow.started_at.desc())
        if agent_id:
            stmt = stmt.where(TraceRow.agent_id == agent_id)
        if status:
            stmt = stmt.where(TraceRow.status == status)
        stmt = stmt.limit(limit).offset(offset)

        async with database.session() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [
            TraceSummary(
                trace_id=r.trace_id,
                agent_id=r.agent_id,
                session_id=r.session_id,
                model_name=r.model_name,
                user_message=r.user_message,
                status=r.status,
                started_at=r.started_at,
                latency_ms=r.latency_ms,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                cost_usd=r.cost_usd,
            )
            for r in rows
        ]

    async def get_trace(self, trace_id: str) -> Optional[TraceData]:
        """스팬 포함 상세 조회 (트레이스 뷰어)."""
        async with database.session() as session:
            row = await session.get(TraceRow, trace_id)
            if row is None:
                return None
            return TraceData(
                trace_id=row.trace_id,
                agent_id=row.agent_id,
                session_id=row.session_id,
                model_name=row.model_name,
                user_message=row.user_message,
                final_response=row.final_response,
                status=row.status,
                error=row.error,
                started_at=row.started_at,
                ended_at=row.ended_at,
                latency_ms=row.latency_ms,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                cost_usd=row.cost_usd,
                spans=[
                    SpanData(
                        span_id=s.span_id,
                        span_type=s.span_type,
                        name=s.name,
                        input=s.input,
                        output=s.output,
                        is_worker=bool(s.is_worker),
                        started_at=s.started_at,
                        ended_at=s.ended_at,
                        latency_ms=s.latency_ms,
                        input_tokens=s.input_tokens,
                        output_tokens=s.output_tokens,
                        cost_usd=s.cost_usd,
                    )
                    for s in sorted(row.spans, key=lambda s: s.started_at)
                ],
            )

    # ── 집계 (대시보드 지표) ─────────────────────────────────────

    async def usage_stats(self, agent_id: Optional[str] = None) -> UsageStats:
        """전체 또는 특정 에이전트의 토큰/비용/지연/에러율 집계."""
        # CASE WHEN — SQLite/PostgreSQL 공통으로 동작하는 조건부 집계
        error_count = func.sum(case((TraceRow.status == "error", 1), else_=0))
        stmt = select(
            func.count(TraceRow.trace_id),
            func.sum(TraceRow.input_tokens),
            func.sum(TraceRow.output_tokens),
            func.sum(TraceRow.cost_usd),
            func.avg(TraceRow.latency_ms),
            error_count,
        )
        if agent_id:
            stmt = stmt.where(TraceRow.agent_id == agent_id)

        async with database.session() as session:
            total, in_tok, out_tok, cost, avg_lat, errors = (
                await session.execute(stmt)
            ).one()

        total = total or 0
        errors = errors or 0
        return UsageStats(
            total_traces=total,
            error_traces=errors,
            error_rate=round(errors / total, 4) if total else 0.0,
            total_input_tokens=in_tok or 0,
            total_output_tokens=out_tok or 0,
            total_cost_usd=round(cost or 0.0, 6),
            avg_latency_ms=round(avg_lat or 0.0, 1),
        )

    async def usage_stats_by_agent(self) -> list[AgentUsageStats]:
        """에이전트별 집계 (비용 상위 에이전트 파악용)."""
        stmt = (
            select(
                TraceRow.agent_id,
                func.count(TraceRow.trace_id),
                func.sum(TraceRow.input_tokens),
                func.sum(TraceRow.output_tokens),
                func.sum(TraceRow.cost_usd),
                func.avg(TraceRow.latency_ms),
                func.sum(case((TraceRow.status == "error", 1), else_=0)),
            )
            .group_by(TraceRow.agent_id)
            .order_by(func.sum(TraceRow.cost_usd).desc())
        )
        async with database.session() as session:
            rows = (await session.execute(stmt)).all()

        result = []
        for agent_id, total, in_tok, out_tok, cost, avg_lat, errors in rows:
            result.append(AgentUsageStats(
                agent_id=agent_id,
                total_traces=total,
                error_traces=errors or 0,
                error_rate=round((errors or 0) / total, 4) if total else 0.0,
                total_input_tokens=in_tok or 0,
                total_output_tokens=out_tok or 0,
                total_cost_usd=round(cost or 0.0, 6),
                avg_latency_ms=round(avg_lat or 0.0, 1),
            ))
        return result

    # ── 평가 ─────────────────────────────────────────────────────

    async def add_evaluation(
        self, trace_id: str, evaluation: EvaluationIn
    ) -> EvaluationOut:
        async with database.session() as session:
            row = EvaluationRow(
                trace_id=trace_id,
                evaluator=evaluation.evaluator,
                criteria=evaluation.criteria,
                score=evaluation.score,
                feedback=evaluation.feedback,
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _eval_to_schema(row)

    async def list_evaluations(self, trace_id: str) -> list[EvaluationOut]:
        stmt = (
            select(EvaluationRow)
            .where(EvaluationRow.trace_id == trace_id)
            .order_by(EvaluationRow.created_at)
        )
        async with database.session() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_eval_to_schema(r) for r in rows]


def _eval_to_schema(row: EvaluationRow) -> EvaluationOut:
    return EvaluationOut(
        evaluation_id=row.evaluation_id,
        trace_id=row.trace_id,
        evaluator=row.evaluator,
        criteria=row.criteria,
        score=row.score,
        feedback=row.feedback,
        created_at=row.created_at,
    )


# 앱 전역 공유 싱글턴
trace_repo = TraceRepository()

"""SQLAlchemy ORM 모델 — 실행 이력 저장 스키마.

traces (1) ─── (N) spans        : 실행 → 단계별 기록
traces (1) ─── (N) evaluations  : 실행 → 품질 평가 기록

집계 성능을 위해 토큰/비용 합계를 traces 에 비정규화 저장한다.
(대시보드의 기간별 비용 집계가 spans 조인 없이 traces 스캔만으로 가능)

SQLite(개발) ↔ PostgreSQL(운영) 겸용 타입만 사용한다.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TraceRow(Base):
    __tablename__ = "traces"

    trace_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    model_name: Mapped[str] = mapped_column(String(128))
    user_message: Mapped[str] = mapped_column(Text)
    final_response: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    spans: Mapped[list["SpanRow"]] = relationship(
        back_populates="trace", cascade="all, delete-orphan", lazy="selectin"
    )
    evaluations: Mapped[list["EvaluationRow"]] = relationship(
        back_populates="trace", cascade="all, delete-orphan", lazy="selectin"
    )


# 에이전트별 기간 조회(대시보드 기본 쿼리)를 위한 복합 인덱스
Index("ix_traces_agent_started", TraceRow.agent_id, TraceRow.started_at)


class SpanRow(Base):
    __tablename__ = "spans"

    span_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    trace_id: Mapped[str] = mapped_column(
        ForeignKey("traces.trace_id", ondelete="CASCADE"), index=True
    )
    span_type: Mapped[str] = mapped_column(String(8))  # llm | tool
    name: Mapped[str] = mapped_column(String(128))
    input: Mapped[str] = mapped_column(Text, default="")
    output: Mapped[str] = mapped_column(Text, default="")
    is_worker: Mapped[int] = mapped_column(Integer, default=0)  # bool (DB 호환)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    trace: Mapped[TraceRow] = relationship(back_populates="spans")


class EvaluationRow(Base):
    __tablename__ = "evaluations"

    evaluation_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(
        ForeignKey("traces.trace_id", ondelete="CASCADE"), index=True
    )
    evaluator: Mapped[str] = mapped_column(String(128))  # "human" | "judge:{model}"
    criteria: Mapped[str] = mapped_column(String(64), default="overall")
    score: Mapped[float] = mapped_column(Float)  # 0.0 ~ 1.0
    feedback: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    trace: Mapped[TraceRow] = relationship(back_populates="evaluations")

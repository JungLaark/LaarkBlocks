"""실행 트레이스 수집 및 비동기 적재 파이프라인.

구성
----
1. TraceCollector — 실행 '중' 스팬을 메모리에 수집하는 빌더.
   엔진의 astream_events 루프가 모델/도구 시작·종료 이벤트를 먹인다.

2. sink — 완성된 TraceData 를 저장하는 비동기 함수(플러그인).
   앱 기동 시 DB 리포지토리의 save 가 연결되고(main.py lifespan),
   테스트에서는 리스트에 수집하는 가짜 sink 로 교체된다.
   → 엔진은 DB 의 존재를 모른다. (관측이 실행을 침범하지 않는 구조)

3. emit — fire-and-forget 백그라운드 태스크로 sink 를 호출한다.
   사용자의 SSE 응답은 저장을 기다리지 않는다(지연 0).
   적재 실패는 로그만 남긴다 — 관측 실패가 서비스 실패가 되어선 안 된다.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, Optional

from src.core.pricing import calculate_cost
from src.schemas.agent import AgentConfig
from src.schemas.tracing import SpanData, SpanType, TraceData, TraceStatus

logger = logging.getLogger(__name__)

TraceSink = Callable[[TraceData], Awaitable[None]]

_sink: Optional[TraceSink] = None
# fire-and-forget 태스크가 GC 로 사라지지 않도록 참조 유지
_pending: set[asyncio.Task] = set()


def set_sink(sink: Optional[TraceSink]) -> None:
    """트레이스 저장 함수를 연결한다. None 이면 트레이싱 비활성."""
    global _sink
    _sink = sink


def emit(trace: TraceData) -> None:
    """트레이스를 백그라운드로 적재한다. (호출자를 블로킹하지 않음)"""
    if _sink is None:
        return

    async def _safe_save(t: TraceData) -> None:
        try:
            await _sink(t)
        except Exception:  # noqa: BLE001 — 관측 실패는 서비스에 전파하지 않는다
            logger.exception("트레이스 적재 실패 (trace_id=%s)", t.trace_id)

    task = asyncio.create_task(_safe_save(trace))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def drain() -> None:
    """대기 중인 적재 태스크를 모두 완료시킨다. (테스트/우아한 종료용)"""
    if _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _truncate(value: Any, limit: int = 2000) -> str:
    """스팬 입출력 저장용 요약 — 대용량 프롬프트로 DB가 부풀지 않게 제한."""
    text = value if isinstance(value, str) else str(value)
    return text[:limit]


class TraceCollector:
    """실행 1회분의 스팬을 수집해 TraceData 로 빌드하는 빌더.

    astream_events 의 run_id 를 키로 시작/종료 이벤트를 짝지어
    스팬의 지연시간을 계산한다.
    """

    def __init__(
        self,
        config: AgentConfig,
        user_message: str,
        session_id: Optional[str] = None,
    ) -> None:
        self.trace_id = uuid.uuid4().hex
        self._config = config
        self._user_message = user_message
        self._session_id = session_id
        self._started_at = _now()
        self._spans: list[SpanData] = []
        self._open: dict[str, SpanData] = {}  # run_id → 진행 중 스팬
        self._finished: Optional[TraceData] = None

    # ── 스팬 기록 ────────────────────────────────────────────────

    def start_span(
        self,
        run_id: str,
        span_type: SpanType,
        name: str,
        input_value: Any = "",
        is_worker: bool = False,
    ) -> None:
        self._open[run_id] = SpanData(
            span_id=uuid.uuid4().hex,
            span_type=span_type,
            name=name,
            input=_truncate(input_value),
            is_worker=is_worker,
            started_at=_now(),
        )

    def end_span(
        self,
        run_id: str,
        output_value: Any = "",
        usage: Optional[dict] = None,
        model_name: Optional[str] = None,
    ) -> None:
        span = self._open.pop(run_id, None)
        if span is None:
            return  # 짝 없는 종료 이벤트 — 방어적으로 무시

        span.ended_at = _now()
        span.latency_ms = int(
            (span.ended_at - span.started_at).total_seconds() * 1000
        )
        span.output = _truncate(output_value)

        if span.span_type == SpanType.LLM:
            # provider 가 정확한 모델명을 주면 갱신 (워커는 모델이 다를 수 있음)
            if model_name:
                span.name = model_name
            if usage:
                span.input_tokens = usage.get("input_tokens")
                span.output_tokens = usage.get("output_tokens")
            span.cost_usd = calculate_cost(
                span.name, span.input_tokens, span.output_tokens
            )
        self._spans.append(span)

    # ── 완성 ─────────────────────────────────────────────────────

    def finish(
        self,
        status: TraceStatus,
        final_response: str = "",
        error: Optional[str] = None,
    ) -> None:
        """트레이스 상태 확정. (astream 의 정상/오류/중단 경로에서 호출)"""
        # 아직 열려 있는 스팬(중단된 실행)도 기록에 포함
        for run_id in list(self._open):
            self.end_span(run_id, output_value="(미완료)")

        ended = _now()
        self._finished = TraceData(
            trace_id=self.trace_id,
            agent_id=self._config.agent_id,
            session_id=self._session_id,
            model_name=self._config.model_name,
            user_message=self._user_message,
            final_response=_truncate(final_response, 8000),
            status=status,
            error=_truncate(error, 2000) if error else None,
            started_at=self._started_at,
            ended_at=ended,
            latency_ms=int((ended - self._started_at).total_seconds() * 1000),
            input_tokens=sum(s.input_tokens or 0 for s in self._spans),
            output_tokens=sum(s.output_tokens or 0 for s in self._spans),
            cost_usd=round(sum(s.cost_usd for s in self._spans), 8),
            spans=self._spans,
        )

    @property
    def is_finished(self) -> bool:
        return self._finished is not None

    def build(self) -> TraceData:
        """완성된 트레이스 반환. finish 없이 종료된 실행은 ABORTED 처리."""
        if self._finished is None:
            self.finish(status=TraceStatus.ABORTED)
        return self._finished

"""트레이싱(실행 이력) 스키마.

계층 구조 (Langfuse 등 업계 표준 관측 도구와 동일한 모델):

    Trace (에이전트 실행 1회 = 사용자 요청 1건)
      └─ Span (실행을 구성하는 개별 단계)
           ├─ type="llm"  : 모델 호출 1회 (토큰/비용 발생 지점)
           └─ type="tool" : 도구 실행 1회 (내장/MCP/KB/워커 위임)

토큰·비용은 Span(llm) 단위로 기록하고 Trace 로 합산한다.
슈퍼바이저 실행 시 워커의 모델 호출도 is_worker=True 스팬으로 기록되어
멀티 에이전트의 '진짜 총비용'이 집계된다. (SSE 스트림에서는 걸러지는
워커 이벤트가 비용 추적에서는 누락되면 안 되기 때문)
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SpanType(str, Enum):
    LLM = "llm"
    TOOL = "tool"


class TraceStatus(str, Enum):
    SUCCESS = "success"  # 정상 완료
    ERROR = "error"      # 실행 중 예외
    ABORTED = "aborted"  # 클라이언트 연결 종료 등으로 중단


class SpanData(BaseModel):
    """실행을 구성하는 개별 단계 (모델 호출 또는 도구 실행)."""

    span_id: str
    span_type: SpanType
    name: str = Field(description="모델명(llm) 또는 도구명(tool)")
    input: str = Field(default="", description="입력 요약 (프롬프트/도구 인자)")
    output: str = Field(default="", description="출력 요약")
    is_worker: bool = Field(default=False, description="워커(하위 에이전트) 내부 실행 여부")
    started_at: datetime
    ended_at: Optional[datetime] = None
    latency_ms: Optional[int] = None
    # llm 스팬 전용 — provider 가 usage_metadata 를 제공하지 않으면 None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: float = Field(default=0.0, description="모델 단가표 기반 계산 비용")


class TraceData(BaseModel):
    """에이전트 실행 1회의 전체 기록."""

    trace_id: str
    agent_id: str
    session_id: Optional[str] = None
    model_name: str
    user_message: str
    final_response: str = ""
    status: TraceStatus
    error: Optional[str] = None
    started_at: datetime
    ended_at: datetime
    latency_ms: int
    # 스팬 합산 값 (조회 성능을 위해 비정규화 저장)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    spans: list[SpanData] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────────
# API 응답/요청 스키마
# ──────────────────────────────────────────────────────────────────

class TraceSummary(BaseModel):
    """목록 조회용 요약 (스팬 제외)."""

    trace_id: str
    agent_id: str
    session_id: Optional[str]
    model_name: str
    user_message: str
    status: TraceStatus
    started_at: datetime
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class UsageStats(BaseModel):
    """토큰/비용 집계 (운영 대시보드의 핵심 지표)."""

    total_traces: int
    error_traces: int
    error_rate: float = Field(description="0.0~1.0")
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    avg_latency_ms: float


class AgentUsageStats(UsageStats):
    """에이전트별 집계 행."""

    agent_id: str


class EvaluationIn(BaseModel):
    """평가 기록 요청 (수동 피드백 또는 저지 결과 직접 등록)."""

    evaluator: str = Field(..., description='평가 주체 (예: "human", "judge:ollama/qwen3:8b")')
    criteria: str = Field(default="overall", description="평가 기준 (helpfulness, accuracy 등)")
    score: float = Field(..., ge=0.0, le=1.0, description="0.0(최악)~1.0(최고)")
    feedback: str = Field(default="", description="평가 코멘트")


class EvaluationOut(EvaluationIn):
    """저장된 평가."""

    evaluation_id: int
    trace_id: str
    created_at: datetime


class JudgeRequest(BaseModel):
    """LLM-as-judge 평가 실행 요청."""

    judge_model: str = Field(
        default="ollama/qwen3:8b",
        description='평가자로 사용할 모델 ("provider/model")',
    )
    criteria: str = Field(
        default="helpfulness",
        description="평가 기준 — 저지 프롬프트에 그대로 주입된다",
    )

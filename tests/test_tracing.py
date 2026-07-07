"""트레이싱(실행 이력 수집) 테스트 — 엔진 계측과 비용 계산 검증."""

import pytest
from langchain_core.messages import AIMessage

from src.core.engine import engine
from src.core.pricing import calculate_cost
from src.schemas.agent import AgentConfig
from src.schemas.tracing import SpanType, TraceStatus
from tests.conftest import collect, make_config


class TestPricing:
    """모델 단가표 기반 비용 계산."""

    def test_known_model_cost(self):
        # gpt-4o: input $2.5/1M, output $10/1M
        cost = calculate_cost("openai/gpt-4o", 1_000_000, 100_000)
        assert cost == pytest.approx(2.5 + 1.0)

    def test_local_model_is_free(self):
        """로컬 sLLM(단가표에 없음)은 0 — 온프레미스 절감 스토리의 근거."""
        assert calculate_cost("ollama/qwen3:8b", 999_999, 999_999) == 0.0

    def test_none_tokens_safe(self):
        assert calculate_cost("openai/gpt-4o", None, None) == 0.0


class TestTraceCollection:
    """엔진 실행 → 트레이스 자동 수집."""

    async def test_simple_run_creates_success_trace(
        self, use_scripted_model, capture_traces
    ):
        use_scripted_model([AIMessage(content="트레이스 응답")])
        await collect(engine.astream(make_config(agent_id="traced"), "질문"))

        traces = await capture_traces()
        assert len(traces) == 1
        t = traces[0]
        assert t.agent_id == "traced"
        assert t.status == TraceStatus.SUCCESS
        assert t.final_response == "트레이스 응답"
        assert t.latency_ms >= 0
        # 모델 호출 1회 → llm 스팬 1개
        llm_spans = [s for s in t.spans if s.span_type == SpanType.LLM]
        assert len(llm_spans) == 1
        assert llm_spans[0].latency_ms is not None

    async def test_tool_loop_records_llm_and_tool_spans(
        self, use_scripted_model, capture_traces
    ):
        use_scripted_model([
            AIMessage(content="", tool_calls=[{
                "name": "calculator", "args": {"expression": "6*7"},
                "id": "c1", "type": "tool_call",
            }]),
            AIMessage(content="42 입니다."),
        ])
        await collect(
            engine.astream(make_config(tools=["calculator"]), "6 곱하기 7은?")
        )

        (trace,) = await capture_traces()
        llm_spans = [s for s in trace.spans if s.span_type == SpanType.LLM]
        tool_spans = [s for s in trace.spans if s.span_type == SpanType.TOOL]
        # ReAct 루프: 모델 2회(도구 요청 + 최종 답변), 도구 1회
        assert len(llm_spans) == 2
        assert len(tool_spans) == 1
        assert tool_spans[0].name == "calculator"
        assert "42" in tool_spans[0].output

    async def test_usage_metadata_is_aggregated(
        self, use_scripted_model, capture_traces
    ):
        """provider 의 usage_metadata 가 스팬→트레이스로 합산되어야 한다."""
        use_scripted_model([
            AIMessage(
                content="토큰 계측 응답",
                usage_metadata={
                    "input_tokens": 120, "output_tokens": 30, "total_tokens": 150,
                },
            ),
        ])
        await collect(engine.astream(make_config(), "질문"))

        (trace,) = await capture_traces()
        assert trace.input_tokens == 120
        assert trace.output_tokens == 30

    async def test_supervisor_records_worker_cost(
        self, use_scripted_model, capture_traces
    ):
        """SSE 에서 걸러지는 워커 실행도 비용 추적에는 포함되어야 한다."""
        use_scripted_model([
            AIMessage(content="", tool_calls=[{
                "name": "delegate_to__worker", "args": {"task": "조사"},
                "id": "d1", "type": "tool_call",
            }]),
            AIMessage(content="워커 결과"),   # 워커 모델 호출
            AIMessage(content="최종 답변"),   # 슈퍼바이저 종합
        ])
        supervisor = make_config(
            agent_id="sup",
            workers=[AgentConfig(
                agent_id="worker", name="워커", description="조사 담당",
                model_name="fake/scripted", system_prompt="_",
            )],
        )
        await collect(engine.astream(supervisor, "조사해줘"))

        (trace,) = await capture_traces()
        llm_spans = [s for s in trace.spans if s.span_type == SpanType.LLM]
        # 슈퍼바이저 2회 + 워커 1회 = llm 스팬 3개
        assert len(llm_spans) == 3
        # 워커의 모델 호출이 is_worker 로 표시되어 기록되어야 한다
        assert any(s.is_worker for s in llm_spans)

    async def test_error_run_creates_error_trace(
        self, use_scripted_model, capture_traces
    ):
        """설정 오류(미등록 도구)도 error 트레이스로 남아야 한다."""
        use_scripted_model([AIMessage(content="x")])
        config = make_config(tools=["no_such_tool"])

        with pytest.raises(ValueError):
            await collect(engine.astream(config, "질문"))

        (trace,) = await capture_traces()
        assert trace.status == TraceStatus.ERROR
        assert "no_such_tool" in trace.error

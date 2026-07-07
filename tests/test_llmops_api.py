"""운영 콘솔(LLMOps) API 테스트 — 실제 SQLite 파일 DB 로 저장까지 검증."""

import httpx
import pytest
from langchain_core.messages import AIMessage

from src.core import tracing
from src.core.engine import engine
from src.db.database import database
from src.db.repository import trace_repo
from src.main import create_app
from tests.conftest import collect, make_config


@pytest.fixture
async def llmops(tmp_path):
    """DB 초기화 + 트레이스 sink 연결 + API 클라이언트 준비."""
    await database.init(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    tracing.set_sink(trace_repo.save_trace)

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await tracing.drain()
    tracing.set_sink(None)
    await database.dispose()


async def run_and_flush(config, message: str) -> None:
    """에이전트를 실행하고 백그라운드 적재까지 완료시키는 헬퍼."""
    await collect(engine.astream(config, message))
    await tracing.drain()


async def test_trace_lifecycle_via_api(llmops, use_scripted_model):
    """실행 → DB 적재 → 목록/상세 조회의 전체 수명주기."""
    use_scripted_model([AIMessage(
        content="저장될 응답",
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )])
    await run_and_flush(make_config(agent_id="api-traced"), "저장 테스트")

    # 목록 조회
    res = await llmops.get("/api/v1/traces", params={"agent_id": "api-traced"})
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 1
    summary = items[0]
    assert summary["status"] == "success"
    assert summary["input_tokens"] == 10

    # 상세 조회 — 스팬 타임라인 포함
    res = await llmops.get(f"/api/v1/traces/{summary['trace_id']}")
    assert res.status_code == 200
    detail = res.json()
    assert detail["final_response"] == "저장될 응답"
    assert len(detail["spans"]) == 1
    assert detail["spans"][0]["span_type"] == "llm"

    # 없는 트레이스는 404
    res = await llmops.get("/api/v1/traces/nonexistent")
    assert res.status_code == 404


async def test_usage_stats_aggregation(llmops, use_scripted_model):
    """집계 API — 토큰 합산과 에러율 계산."""
    use_scripted_model([
        AIMessage(content="첫 실행", usage_metadata={
            "input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
        }),
        AIMessage(content="둘째 실행", usage_metadata={
            "input_tokens": 50, "output_tokens": 10, "total_tokens": 60,
        }),
    ])
    await run_and_flush(make_config(agent_id="stats-agent"), "1")
    await run_and_flush(make_config(agent_id="stats-agent"), "2")

    # 에러 실행 1건 추가 (미등록 도구)
    with pytest.raises(ValueError):
        await collect(engine.astream(
            make_config(agent_id="stats-agent", tools=["bad_tool"]), "3"
        ))
    await tracing.drain()

    res = await llmops.get("/api/v1/stats/usage", params={"agent_id": "stats-agent"})
    assert res.status_code == 200
    stats = res.json()
    assert stats["total_traces"] == 3
    assert stats["error_traces"] == 1
    assert stats["error_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert stats["total_input_tokens"] == 150
    assert stats["total_output_tokens"] == 30

    # 에이전트별 집계에도 반영
    res = await llmops.get("/api/v1/stats/usage/by-agent")
    rows = res.json()
    assert any(r["agent_id"] == "stats-agent" and r["total_traces"] == 3 for r in rows)


async def test_manual_evaluation(llmops, use_scripted_model):
    """평가 수동 등록 + 조회."""
    use_scripted_model([AIMessage(content="평가 대상 응답")])
    await run_and_flush(make_config(agent_id="eval-agent"), "평가해줘")

    trace_id = (await llmops.get("/api/v1/traces")).json()[0]["trace_id"]

    res = await llmops.post(
        f"/api/v1/traces/{trace_id}/evaluations",
        json={"evaluator": "human", "criteria": "accuracy",
              "score": 0.8, "feedback": "정확했음"},
    )
    assert res.status_code == 201
    assert res.json()["score"] == 0.8

    res = await llmops.get(f"/api/v1/traces/{trace_id}/evaluations")
    assert len(res.json()) == 1
    assert res.json()[0]["evaluator"] == "human"


async def test_llm_as_judge(llmops, use_scripted_model):
    """LLM-as-judge — 저지 모델의 JSON 평가가 파싱·저장되어야 한다."""
    use_scripted_model([
        # 1) 평가 대상 에이전트의 응답
        AIMessage(content="서울은 대한민국의 수도입니다."),
        # 2) 저지 모델의 평가 (JSON 앞뒤 사족 포함 — 방어적 파싱 검증)
        AIMessage(content='평가 결과입니다: {"score": 0.9, "feedback": "정확하고 간결한 답변"} 이상입니다.'),
    ])
    await run_and_flush(make_config(agent_id="judged-agent"), "한국의 수도는?")

    trace_id = (await llmops.get("/api/v1/traces")).json()[0]["trace_id"]

    res = await llmops.post(
        f"/api/v1/traces/{trace_id}/evaluate",
        json={"judge_model": "fake/judge", "criteria": "helpfulness"},
    )
    assert res.status_code == 201
    evaluation = res.json()
    assert evaluation["score"] == 0.9
    assert evaluation["evaluator"] == "judge:fake/judge"
    assert "정확" in evaluation["feedback"]


async def test_judge_failure_returns_502(llmops, use_scripted_model):
    """저지 모델이 JSON 을 반환하지 못하면 502 로 응답해야 한다."""
    use_scripted_model([
        AIMessage(content="응답"),
        AIMessage(content="죄송합니다, 평가할 수 없습니다."),  # JSON 없음
    ])
    await run_and_flush(make_config(agent_id="judge-fail"), "질문")

    trace_id = (await llmops.get("/api/v1/traces")).json()[0]["trace_id"]
    res = await llmops.post(
        f"/api/v1/traces/{trace_id}/evaluate",
        json={"judge_model": "fake/judge"},
    )
    assert res.status_code == 502

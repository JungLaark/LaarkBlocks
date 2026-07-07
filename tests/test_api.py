"""API 계층 테스트 — SSE 스트리밍 엔드포인트를 HTTP 레벨에서 검증한다."""

import json

import httpx
import pytest
from langchain_core.messages import AIMessage

from src.main import create_app


@pytest.fixture
async def client():
    """ASGI 인프로세스 테스트 클라이언트 (실서버 기동 불필요)."""
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """SSE 응답 본문을 (event, data) 튜플 목록으로 파싱하는 헬퍼."""
    events: list[tuple[str, dict]] = []
    current_event = "message"
    for line in text.splitlines():
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            payload = line.split(":", 1)[1].strip()
            if payload:  # ping 등 빈 데이터 제외
                try:
                    events.append((current_event, json.loads(payload)))
                except json.JSONDecodeError:
                    pass  # ping 의 data 는 JSON 이 아닐 수 있음
    return events


async def test_health(client):
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


async def test_list_tools(client):
    res = await client.get("/api/v1/tools")
    assert res.status_code == 200
    names = [t["name"] for t in res.json()]
    assert "calculator" in names
    assert "get_current_time" in names


async def test_list_providers(client):
    res = await client.get("/api/v1/providers")
    assert res.status_code == 200
    assert "ollama" in res.json()


async def test_run_agent_sse_stream(client, use_scripted_model):
    """POST /agents/run — SSE 이벤트 계약(token → done)을 HTTP 로 검증."""
    answer = "SSE 스트리밍 응답입니다."
    use_scripted_model([AIMessage(content=answer)])

    body = {
        "agent_config": {
            "agent_id": "sse-test",
            "name": "SSE 테스트",
            "model_name": "fake/scripted",
            "system_prompt": "테스트",
            "tools": [],
        },
        "user_message": "안녕",
    }

    async with client.stream("POST", "/api/v1/agents/run", json=body) as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        text = "".join([chunk async for chunk in res.aiter_text()])

    events = parse_sse(text)
    event_types = [e for e, _ in events]

    assert "token" in event_types
    assert event_types[-1] == "done"

    # 토큰 이어붙임 == done 의 전체 응답
    tokens = "".join(d["content"] for e, d in events if e == "token")
    done_payload = next(d for e, d in events if e == "done")
    assert tokens == answer
    assert done_payload["content"] == answer
    assert done_payload["agent_id"] == "sse-test"


async def test_run_agent_error_event(client):
    """미등록 provider → HTTP 200 + error 이벤트로 전달되어야 한다.

    (SSE 는 스트림 시작 후 HTTP 상태를 바꿀 수 없으므로,
     오류도 이벤트 계약의 일부로 내려보내는 것이 올바른 동작이다)
    """
    body = {
        "agent_config": {
            "agent_id": "bad-provider",
            "name": "오류 테스트",
            "model_name": "unknown/model",
            "system_prompt": "테스트",
            "tools": [],
        },
        "user_message": "안녕",
    }

    async with client.stream("POST", "/api/v1/agents/run", json=body) as res:
        assert res.status_code == 200
        text = "".join([chunk async for chunk in res.aiter_text()])

    events = parse_sse(text)
    assert events, "최소 1개의 이벤트가 와야 한다"
    event_type, payload = events[-1]
    assert event_type == "error"
    assert "지원하지 않는 provider" in payload["message"]


async def test_session_history_api(client, use_scripted_model):
    """세션으로 실행 후 히스토리 API 로 대화 이력을 조회할 수 있어야 한다."""
    use_scripted_model([AIMessage(content="세션 응답입니다.")])

    body = {
        "agent_config": {
            "agent_id": "hist-api",
            "name": "히스토리 테스트",
            "model_name": "fake/scripted",
            "system_prompt": "테스트",
            "tools": [],
        },
        "user_message": "기억해줘",
        "session_id": "api-s1",
    }
    async with client.stream("POST", "/api/v1/agents/run", json=body) as res:
        assert res.status_code == 200
        async for _ in res.aiter_text():
            pass  # 스트림 소진 (실행 완료 대기)

    res = await client.get("/api/v1/agents/hist-api/sessions/api-s1/history")
    assert res.status_code == 200
    history = res.json()
    assert history[0] == {"role": "user", "content": "기억해줘"}
    assert history[1]["role"] == "assistant"

    # 존재하지 않는 세션은 404
    res = await client.get("/api/v1/agents/hist-api/sessions/no-such/history")
    assert res.status_code == 404


async def test_run_agent_validation_error(client):
    """스키마 위반(빈 메시지)은 SSE 시작 전 422 로 거부되어야 한다."""
    body = {
        "agent_config": {
            "agent_id": "v-test",
            "name": "검증 테스트",
            "model_name": "fake/scripted",
            "system_prompt": "테스트",
        },
        "user_message": "",  # min_length=1 위반
    }
    res = await client.post("/api/v1/agents/run", json=body)
    assert res.status_code == 422

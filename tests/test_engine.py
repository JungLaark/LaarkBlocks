"""실행 엔진 단위 테스트 — 실제 LLM 서버 없이 전체 경로를 검증한다."""

import pytest
from langchain_core.messages import AIMessage

from src.core.engine import engine
from src.schemas.agent import AgentConfig, StreamEventType
from tests.conftest import collect, make_config


class TestSchemaValidation:
    """AgentConfig 스키마 검증 — 잘못된 설정은 실행 전에 차단되어야 한다."""

    def test_invalid_model_name_without_slash(self):
        with pytest.raises(ValueError, match="provider/model"):
            make_config(model_name="qwen3:8b")  # provider 누락

    def test_invalid_agent_id_pattern(self):
        with pytest.raises(ValueError):
            make_config(agent_id="한글ID")  # 허용되지 않는 문자

    def test_provider_and_model_properties(self):
        config = make_config(model_name="ollama/qwen3:8b")
        assert config.provider == "ollama"
        assert config.model == "qwen3:8b"


class TestGraphBuild:
    """설정 → StateGraph 동적 빌드 검증."""

    def test_build_without_tools(self, use_scripted_model):
        use_scripted_model([AIMessage(content="hi")])
        graph = engine.build_graph(make_config())
        # 도구가 없으면 agent 단일 노드 그래프
        assert "agent" in graph.get_graph().nodes
        assert "tools" not in graph.get_graph().nodes

    def test_build_with_tools(self, use_scripted_model):
        use_scripted_model([AIMessage(content="hi")])
        graph = engine.build_graph(make_config(tools=["calculator"]))
        # 도구가 있으면 ReAct 루프(agent + tools) 그래프
        assert "agent" in graph.get_graph().nodes
        assert "tools" in graph.get_graph().nodes

    def test_unknown_tool_rejected(self, use_scripted_model):
        use_scripted_model([AIMessage(content="hi")])
        with pytest.raises(ValueError, match="등록되지 않은 도구"):
            engine.build_graph(make_config(tools=["no_such_tool"]))

    def test_unknown_provider_rejected(self):
        with pytest.raises(ValueError, match="지원하지 않는 provider"):
            engine.build_graph(make_config(model_name="unknown/model"))


class TestStreaming:
    """astream 이벤트 계약 검증 — 토큰 / 도구 / 종료 이벤트."""

    async def test_token_stream_and_done(self, use_scripted_model):
        """토큰 이벤트를 이어 붙이면 done 의 전체 응답과 일치해야 한다."""
        answer = "안녕하세요, LaarkBlocks 입니다."
        use_scripted_model([AIMessage(content=answer)])

        events = await collect(engine.astream(make_config(), "안녕"))

        token_events = [e for e in events if e["type"] == StreamEventType.TOKEN]
        done_events = [e for e in events if e["type"] == StreamEventType.DONE]

        assert len(token_events) > 1, "토큰이 여러 청크로 스트리밍되어야 한다"
        assert "".join(e["content"] for e in token_events) == answer
        assert len(done_events) == 1
        assert done_events[0]["content"] == answer
        assert done_events[0]["agent_id"] == "test-agent"

    async def test_react_tool_loop(self, use_scripted_model):
        """도구 호출 → 결과 반영 → 최종 답변의 ReAct 루프 전체 검증."""
        use_scripted_model([
            # 1턴: 모델이 calculator 도구 호출을 요청
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "calculator",
                    "args": {"expression": "6 * 7"},
                    "id": "call_1",
                    "type": "tool_call",
                }],
            ),
            # 2턴: 도구 결과(42)를 본 뒤 최종 답변
            AIMessage(content="계산 결과는 42 입니다."),
        ])

        config = make_config(tools=["calculator"])
        events = await collect(engine.astream(config, "6 곱하기 7은?"))
        types = [e["type"] for e in events]

        # 도구 시작/종료 이벤트가 정확히 한 번씩 발생
        assert types.count(StreamEventType.TOOL_START) == 1
        assert types.count(StreamEventType.TOOL_END) == 1

        tool_end = next(e for e in events if e["type"] == StreamEventType.TOOL_END)
        assert tool_end["tool"] == "calculator"
        assert tool_end["output"] == "42"  # 실제 내장 도구가 계산한 값

        done = next(e for e in events if e["type"] == StreamEventType.DONE)
        assert "42" in done["content"]

        # 이벤트 순서: 도구 호출이 최종 토큰보다 먼저 와야 한다
        assert types.index(StreamEventType.TOOL_END) < types.index(StreamEventType.DONE)


class TestBuiltinTools:
    """내장 도구 자체 검증."""

    def test_calculator_basic(self):
        from src.core.tool_registry import calculator
        assert calculator.invoke({"expression": "(3 + 4) * 2 ** 3"}) == "56"

    def test_calculator_rejects_code_injection(self):
        """eval 인젝션 방어 — 산술 이외 표현식은 거부되어야 한다."""
        from src.core.tool_registry import calculator
        result = calculator.invoke({"expression": "__import__('os').system('dir')"})
        assert "계산 오류" in result

    def test_get_current_time_returns_iso8601(self):
        from datetime import datetime
        from src.core.tool_registry import get_current_time
        # ISO-8601 파싱이 가능해야 한다
        datetime.fromisoformat(get_current_time.invoke({}))

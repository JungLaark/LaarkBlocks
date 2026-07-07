"""슈퍼바이저 멀티 에이전트(agent-as-tool) 테스트.

각본 순서에 주의:
공유 Script 를 슈퍼바이저와 워커가 함께 소비하므로, 호출 순서대로
[슈퍼바이저: 위임 tool_call] → [워커: 답변] → [슈퍼바이저: 최종 답변]
으로 준비한다.
"""

import pytest
from langchain_core.messages import AIMessage

from src.core.engine import engine
from src.schemas.agent import AgentConfig, StreamEventType
from tests.conftest import collect, make_config


def make_supervisor() -> AgentConfig:
    """리서처 워커 1명을 둔 슈퍼바이저 설정."""
    return make_config(
        agent_id="supervisor",
        name="슈퍼바이저",
        workers=[AgentConfig(
            agent_id="researcher",
            name="리서처",
            description="자료 조사를 담당한다",
            model_name="fake/scripted",
            system_prompt="당신은 리서처입니다.",
        )],
    )


class TestSchema:
    def test_nested_workers_rejected(self):
        """워커의 워커(2단계 중첩)는 스키마에서 차단되어야 한다."""
        grandchild = dict(
            agent_id="gc", name="손자", model_name="fake/x", system_prompt="_"
        )
        child = dict(
            agent_id="c", name="자식", model_name="fake/x", system_prompt="_",
            workers=[grandchild],
        )
        with pytest.raises(ValueError, match="중첩은 1단계"):
            make_config(workers=[child])


class TestGraphBuild:
    def test_supervisor_gets_delegation_tool(self, use_scripted_model):
        """워커가 있으면 delegate_to__* 도구가 바인딩된 그래프가 빌드된다."""
        use_scripted_model([AIMessage(content="x")])
        graph = engine.build_graph(make_supervisor())
        # 위임 도구가 있으므로 ReAct 루프(tools 노드) 그래프여야 한다
        assert "tools" in graph.get_graph().nodes


async def test_supervisor_delegates_and_synthesizes(use_scripted_model):
    """위임 → 워커 실행 → 결과 종합의 전체 흐름 + 스트림 분리 검증."""
    script = use_scripted_model([
        # 1. 슈퍼바이저: 리서처에게 위임
        AIMessage(
            content="",
            tool_calls=[{
                "name": "delegate_to__researcher",
                "args": {"task": "정산기 배포 방식을 조사해줘"},
                "id": "call_d1",
                "type": "tool_call",
            }],
        ),
        # 2. 워커(리서처): 조사 결과 응답
        AIMessage(content="조사 결과: 중앙 서버에서 일괄 배포합니다."),
        # 3. 슈퍼바이저: 워커 결과를 종합한 최종 답변
        AIMessage(content="정리하면, 중앙 일괄 배포 방식입니다."),
    ])

    events = await collect(
        engine.astream(make_supervisor(), "정산기 배포 방식 알려줘")
    )
    types = [e["type"] for e in events]

    # 위임이 도구 호출로 표면화되어야 한다
    tool_end = next(e for e in events if e["type"] == StreamEventType.TOOL_END)
    assert tool_end["tool"] == "delegate_to__researcher"
    assert "일괄 배포" in tool_end["output"]

    # 최종 응답은 슈퍼바이저의 종합 답변
    done = next(e for e in events if e["type"] == StreamEventType.DONE)
    assert done["content"] == "정리하면, 중앙 일괄 배포 방식입니다."

    # ★ 스트림 분리: 워커의 내부 토큰("조사 결과...")이 슈퍼바이저의
    #   token 이벤트로 새어 나오면 안 된다 (이중 출력 방지)
    token_text = "".join(
        e["content"] for e in events if e["type"] == StreamEventType.TOKEN
    )
    assert "조사 결과" not in token_text
    assert "정리하면" in token_text

    # 모델은 정확히 3회 호출되어야 한다 (슈퍼바이저 2회 + 워커 1회)
    assert len(script.captured) == 3
    # 워커(2번째 호출)의 입력에는 위임 task 가 들어가야 한다
    worker_input_contents = [str(m.content) for m in script.captured[1]]
    assert any("조사해줘" in c for c in worker_input_contents)

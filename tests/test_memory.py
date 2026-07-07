"""세션 메모리(체크포인터) 테스트.

핵심 검증 포인트:
1. 같은 session_id 로 두 번 실행하면, 두 번째 모델 호출의 입력에
   이전 대화(사용자 질문 + 모델 답변)가 포함되어야 한다.
2. 다른 session_id / 다른 agent_id 는 서로의 대화를 볼 수 없어야 한다(격리).
3. session_id 미지정 실행은 아무 상태도 남기지 않아야 한다(무상태).
"""

from langchain_core.messages import AIMessage

from src.core.engine import engine
from tests.conftest import collect, make_config


async def test_same_session_carries_context(use_scripted_model):
    """같은 세션의 2번째 호출은 이전 대화를 모델 입력으로 받아야 한다."""
    script = use_scripted_model(
        [AIMessage(content="1턴 응답"), AIMessage(content="2턴 응답")]
    )
    config = make_config()

    await collect(engine.astream(config, "첫 번째 질문", session_id="s1"))
    await collect(engine.astream(config, "두 번째 질문", session_id="s1"))

    # 1턴 입력: [system, human] = 2개
    assert len(script.captured[0]) == 2
    # 2턴 입력: [system, human1, ai1, human2] = 4개 → 맥락이 이어졌다는 증거
    assert len(script.captured[1]) == 4
    contents = [m.content for m in script.captured[1]]
    assert "첫 번째 질문" in contents
    assert "1턴 응답" in contents


async def test_different_sessions_are_isolated(use_scripted_model):
    """세션이 다르면 대화가 섞이지 않아야 한다."""
    script = use_scripted_model([AIMessage(content="A"), AIMessage(content="B")])
    config = make_config()

    await collect(engine.astream(config, "세션1 질문", session_id="iso-1"))
    await collect(engine.astream(config, "세션2 질문", session_id="iso-2"))

    # 두 번째 실행도 새 세션이므로 입력은 [system, human] 2개뿐이어야 한다
    assert len(script.captured[1]) == 2
    assert all("세션1" not in str(m.content) for m in script.captured[1])


async def test_same_session_different_agents_are_isolated(use_scripted_model):
    """thread_id 가 agent_id 로 네임스페이스되어 에이전트 간 격리되어야 한다."""
    script = use_scripted_model([AIMessage(content="A"), AIMessage(content="B")])

    await collect(engine.astream(
        make_config(agent_id="agent-a"), "질문", session_id="shared"
    ))
    await collect(engine.astream(
        make_config(agent_id="agent-b"), "질문", session_id="shared"
    ))

    # 같은 session_id 라도 agent 가 다르면 이전 대화가 보이면 안 된다
    assert len(script.captured[1]) == 2


async def test_stateless_run_leaves_no_history(use_scripted_model):
    """session_id 없는 실행은 이력을 남기지 않아야 한다."""
    use_scripted_model([AIMessage(content="무상태 응답")])
    config = make_config(agent_id="stateless-agent")

    events = await collect(engine.astream(config, "질문"))
    done = next(e for e in events if e["type"].value == "done")
    assert done["session_id"] is None

    # 어떤 세션 키로도 이력이 조회되지 않아야 한다
    assert await engine.get_history(config, "any") == []


async def test_get_history_returns_role_content(use_scripted_model):
    """이력 조회 API 계약: role/content dict 목록."""
    use_scripted_model([AIMessage(content="히스토리 응답")])
    config = make_config(agent_id="history-agent")

    await collect(engine.astream(config, "히스토리 질문", session_id="h1"))
    history = await engine.get_history(config, "h1")

    assert history == [
        {"role": "user", "content": "히스토리 질문"},
        {"role": "assistant", "content": "히스토리 응답"},
    ]

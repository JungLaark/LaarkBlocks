"""테스트 공용 픽스처.

핵심 아이디어: 모델 추상화 레이어(register_provider)에 '각본(scripted)
기반 Fake 모델'을 "fake" provider 로 주입한다.
→ 실제 LLM 서버(Ollama 등) 없이도 그래프 빌드, ReAct 도구 루프,
  SSE 스트리밍까지 엔진 전체 경로를 CI 에서 검증할 수 있다.
"""

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any, Optional

import pytest
from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.embeddings import Embeddings
from pydantic import ConfigDict

from src.core import embedding_factory, llm_factory
from src.schemas.agent import AgentConfig


class Script:
    """여러 모델 인스턴스가 공유하는 각본 상태 홀더.

    주의: pydantic 모델 필드에 list 를 직접 넘기면 검증 과정에서
    '복사본'이 만들어져 인스턴스 간 상태 공유가 끊긴다.
    pydantic 이 검증하지 않는 임의 타입(이 클래스)으로 감싸면
    참조가 그대로 유지되어, 멀티턴 각본 소비와 호출 기록이
    엔진의 매 요청 모델 재생성과 무관하게 이어진다.
    """

    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses: list[AIMessage] = list(responses)
        # 매 모델 호출마다 모델이 받은 입력 메시지 목록을 기록
        self.captured: list[list[BaseMessage]] = []


class ScriptedChatModel(BaseChatModel):
    """미리 정의된 AIMessage 를 호출 순서대로 반환하는 테스트용 모델.

    - _stream 을 구현하므로 astream_events 환경에서 토큰 스트리밍 경로를 탄다.
    - bind_tools 는 자기 자신을 반환 (도구 스키마 바인딩은 각본이 대신한다).
    - Script 홀더를 통해 인스턴스 간 각본 소비/호출 기록을 공유한다.
      (엔진이 요청마다 모델을 새로 만들어도 멀티턴 각본이 이어진다)
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    script: Script

    def _record(self, messages: list[BaseMessage]) -> None:
        self.script.captured.append(list(messages))

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _next(self) -> AIMessage:
        if not self.script.responses:
            raise RuntimeError("각본에 준비된 응답이 더 이상 없습니다.")
        return self.script.responses.pop(0)

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        """도구 바인딩은 각본으로 대체되므로 자기 자신을 반환."""
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        self._record(messages)
        return ChatResult(generations=[ChatGeneration(message=self._next())])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        """각본의 다음 응답을 청크 단위로 방출.

        - 일반 텍스트: 2글자씩 잘라 여러 토큰 청크로 방출 (스트리밍 재현)
        - tool_calls 포함: tool_call_chunks 를 담은 단일 청크로 방출
        """
        self._record(messages)
        message = self._next()

        if message.tool_calls:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": tc["name"],
                            "args": json.dumps(tc["args"], ensure_ascii=False),
                            "id": tc["id"],
                            "index": i,
                            "type": "tool_call_chunk",
                        }
                        for i, tc in enumerate(message.tool_calls)
                    ],
                )
            )
            return

        text = message.content if isinstance(message.content, str) else ""
        for i in range(0, len(text), 2):
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=text[i : i + 2]))
            if run_manager:
                # 콜백에 토큰을 알려야 astream_events 가 stream 이벤트를 방출한다
                run_manager.on_llm_new_token(chunk.text, chunk=chunk)
            yield chunk


@pytest.fixture
def use_scripted_model():
    """각본(responses)을 'fake' provider 로 등록하는 헬퍼 픽스처.

    사용::

        def test_x(use_scripted_model):
            script = use_scripted_model([AIMessage(content="안녕하세요")])
            ...
            assert len(script.captured) == 1  # 모델 호출 기록 검증
    """
    registered: list[str] = []

    def _register(responses: list[AIMessage]) -> Script:
        script = Script(responses)

        @llm_factory.register_provider("fake")
        def _build_fake(config: AgentConfig) -> BaseChatModel:
            # Script 홀더 공유 → 모델이 매번 재생성되어도 각본이 이어진다
            return ScriptedChatModel(script=script)

        registered.append("fake")
        return script

    yield _register

    # 테스트 종료 후 fake provider 제거 (레지스트리 원상 복구)
    for name in registered:
        llm_factory._PROVIDER_BUILDERS.pop(name, None)


class HashEmbeddings(Embeddings):
    """토큰 해시 기반 결정론적 임베딩 (테스트용).

    같은 단어를 공유하는 텍스트일수록 코사인 유사도가 높아지므로,
    실제 임베딩 서버 없이 벡터 검색의 상대 순위를 검증할 수 있다.
    """

    DIM = 64

    def _vec(self, text: str) -> list[float]:
        import re

        vec = [0.0] * self.DIM
        for token in re.findall(r"[가-힣a-zA-Z0-9_.]+", text.lower()):
            vec[hash(token) % self.DIM] += 1.0
        return vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


@pytest.fixture
def use_fake_embeddings():
    """'fake' 임베딩 provider 를 등록하는 픽스처. (임베딩 서버 불필요)"""

    @embedding_factory.register_provider("fake")
    def _build(model: str) -> Embeddings:
        return HashEmbeddings()

    yield
    embedding_factory._EMBEDDING_BUILDERS.pop("fake", None)


def make_config(**overrides: Any) -> AgentConfig:
    """테스트용 AgentConfig 생성 헬퍼."""
    base: dict[str, Any] = {
        "agent_id": "test-agent",
        "name": "테스트 에이전트",
        "description": "테스트용",
        "model_name": "fake/scripted",
        "system_prompt": "당신은 테스트 에이전트입니다.",
        "tools": [],
    }
    base.update(overrides)
    return AgentConfig(**base)


async def collect(stream: AsyncIterator[dict]) -> list[dict]:
    """비동기 이벤트 스트림을 리스트로 수집."""
    return [ev async for ev in stream]

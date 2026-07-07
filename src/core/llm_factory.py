"""모델 추상화 레이어 (LLM Factory).

설계 원칙
---------
1. 엔진(core/engine.py)은 LangChain 의 `BaseChatModel` 인터페이스에만
   의존한다. 어떤 provider 가 뒤에 있는지 엔진은 알 필요가 없다.
2. provider 별 구현체는 "빌더 함수 레지스트리"에 등록하며, 모듈 import 는
   빌더 함수 내부에서 lazy 하게 수행한다.
   → langchain-openai 등이 설치되지 않아도 Ollama 사용에는 영향이 없고,
     새 provider 추가 시 이 파일에 빌더 함수 하나만 추가하면 된다.
3. 테스트에서는 `register_provider()` 로 Fake 모델을 주입하여
   실제 LLM 서버 없이 엔진 전체를 검증한다. (tests/conftest.py 참고)
"""

from typing import Callable

from langchain_core.language_models.chat_models import BaseChatModel

from src.config import get_settings
from src.schemas.agent import AgentConfig

# provider 이름 → 빌더 함수(AgentConfig 를 받아 BaseChatModel 반환)
_PROVIDER_BUILDERS: dict[str, Callable[[AgentConfig], BaseChatModel]] = {}


def register_provider(
    name: str,
) -> Callable[[Callable[[AgentConfig], BaseChatModel]], Callable[[AgentConfig], BaseChatModel]]:
    """provider 빌더 등록 데코레이터.

    사용 예::

        @register_provider("ollama")
        def _build_ollama(config: AgentConfig) -> BaseChatModel: ...
    """

    def decorator(fn: Callable[[AgentConfig], BaseChatModel]):
        _PROVIDER_BUILDERS[name] = fn
        return fn

    return decorator


def available_providers() -> list[str]:
    """현재 등록된 provider 이름 목록 (운영 콘솔 노출용)."""
    return sorted(_PROVIDER_BUILDERS.keys())


def create_chat_model(config: AgentConfig) -> BaseChatModel:
    """AgentConfig 의 model_name('provider/model')을 해석해 모델 인스턴스 생성.

    Raises:
        ValueError: 등록되지 않은 provider 인 경우.
        RuntimeError: provider 패키지가 설치되지 않은 경우.
    """
    builder = _PROVIDER_BUILDERS.get(config.provider)
    if builder is None:
        raise ValueError(
            f"지원하지 않는 provider 입니다: '{config.provider}'. "
            f"사용 가능: {available_providers()}"
        )
    return builder(config)


# ──────────────────────────────────────────────────────────────────
# 기본 provider 구현
# ──────────────────────────────────────────────────────────────────

@register_provider("ollama")
def _build_ollama(config: AgentConfig) -> BaseChatModel:
    """로컬 sLLM — Ollama. LaarkBlocks 의 기본 provider.

    온프레미스/폐쇄망 환경(금융·공공)을 1순위 타겟으로 하므로
    로컬 모델을 기본값으로 둔다.
    """
    from langchain_ollama import ChatOllama  # lazy import

    return ChatOllama(
        model=config.model,
        base_url=get_settings().ollama_base_url,
        temperature=config.model_params.temperature,
        num_predict=config.model_params.max_tokens,  # Ollama 의 max_tokens 대응 옵션
    )


@register_provider("openai")
def _build_openai(config: AgentConfig) -> BaseChatModel:
    """OpenAI API. `pip install langchain-openai` + OPENAI_API_KEY 필요."""
    try:
        from langchain_openai import ChatOpenAI  # lazy import
    except ImportError as e:  # pragma: no cover - 옵션 의존성
        raise RuntimeError(
            "openai provider 를 사용하려면 'pip install langchain-openai' 가 필요합니다."
        ) from e

    return ChatOpenAI(
        model=config.model,
        temperature=config.model_params.temperature,
        max_tokens=config.model_params.max_tokens,
    )


@register_provider("anthropic")
def _build_anthropic(config: AgentConfig) -> BaseChatModel:
    """Anthropic API. `pip install langchain-anthropic` + ANTHROPIC_API_KEY 필요."""
    try:
        from langchain_anthropic import ChatAnthropic  # lazy import
    except ImportError as e:  # pragma: no cover - 옵션 의존성
        raise RuntimeError(
            "anthropic provider 를 사용하려면 'pip install langchain-anthropic' 이 필요합니다."
        ) from e

    return ChatAnthropic(
        model=config.model,
        temperature=config.model_params.temperature,
        max_tokens=config.model_params.max_tokens or 4096,
    )

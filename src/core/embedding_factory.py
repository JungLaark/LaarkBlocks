"""임베딩 모델 추상화 레이어.

llm_factory 와 동일한 설계: "provider/model" 문자열을 해석해
LangChain `Embeddings` 인터페이스 구현체를 반환한다.
지식베이스(knowledge.py)는 provider 가 무엇인지 알 필요가 없다.

예) "ollama/nomic-embed-text", "openai/text-embedding-3-small"
"""

from typing import Callable

from langchain_core.embeddings import Embeddings

from src.config import get_settings

# provider 이름 → 빌더 함수(모델명을 받아 Embeddings 반환)
_EMBEDDING_BUILDERS: dict[str, Callable[[str], Embeddings]] = {}


def register_provider(
    name: str,
) -> Callable[[Callable[[str], Embeddings]], Callable[[str], Embeddings]]:
    """임베딩 provider 빌더 등록 데코레이터. (테스트의 fake 주입 지점)"""

    def decorator(fn: Callable[[str], Embeddings]):
        _EMBEDDING_BUILDERS[name] = fn
        return fn

    return decorator


def create_embeddings(model_name: str) -> Embeddings:
    """'provider/model' 형식의 임베딩 모델명을 해석해 인스턴스 생성."""
    provider, _, model = model_name.partition("/")
    if not provider or not model:
        raise ValueError(
            f'임베딩 모델명은 "provider/model" 형식이어야 합니다. (입력값: "{model_name}")'
        )
    builder = _EMBEDDING_BUILDERS.get(provider)
    if builder is None:
        raise ValueError(
            f"지원하지 않는 임베딩 provider 입니다: '{provider}'. "
            f"사용 가능: {sorted(_EMBEDDING_BUILDERS)}"
        )
    return builder(model)


@register_provider("ollama")
def _build_ollama(model: str) -> Embeddings:
    """로컬 임베딩 — Ollama. (예: nomic-embed-text, bge-m3)"""
    from langchain_ollama import OllamaEmbeddings  # lazy import

    return OllamaEmbeddings(model=model, base_url=get_settings().ollama_base_url)


@register_provider("openai")
def _build_openai(model: str) -> Embeddings:
    """OpenAI 임베딩 API. langchain-openai 설치 필요."""
    try:
        from langchain_openai import OpenAIEmbeddings  # lazy import
    except ImportError as e:  # pragma: no cover - 옵션 의존성
        raise RuntimeError(
            "openai 임베딩을 사용하려면 'pip install langchain-openai' 가 필요합니다."
        ) from e

    return OpenAIEmbeddings(model=model)

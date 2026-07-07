"""지식베이스(RAG) 스키마."""

from pydantic import BaseModel, Field


class KnowledgeBaseConfig(BaseModel):
    """지식베이스 정의 — 에이전트와 마찬가지로 '설정'이 곧 정의다."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
        description="지식베이스 식별자. 도구 이름 'kb__{name}' 으로 노출된다",
        examples=["deploy-manual"],
    )
    description: str = Field(
        default="",
        max_length=500,
        description="지식베이스 설명 — 에이전트(모델)가 검색 도구를 쓸지 판단하는 근거가 되므로 구체적으로 쓸 것",
    )
    embedding_model: str = Field(
        default="ollama/nomic-embed-text",
        description='임베딩 모델 ("provider/model" 형식)',
    )
    chunk_size: int = Field(default=500, ge=50, le=4000, description="청크 크기(문자)")
    chunk_overlap: int = Field(default=100, ge=0, le=1000, description="청크 간 중첩(문자)")
    top_k: int = Field(default=4, ge=1, le=20, description="검색 시 반환할 청크 수")


class DocumentIn(BaseModel):
    """문서 등록 요청."""

    text: str = Field(..., min_length=1, description="문서 본문")
    source: str = Field(default="unknown", description="출처(파일명/URL 등) — 답변 근거 표시용")


class SearchRequest(BaseModel):
    """검색 요청 (디버그/운영 콘솔용)."""

    query: str = Field(..., min_length=1)
    top_k: int | None = Field(default=None, description="미지정 시 지식베이스 기본값")


class SearchResult(BaseModel):
    """검색 결과 청크."""

    content: str
    source: str
    score: float = Field(description="RRF 융합 점수 (하이브리드 순위 근거)")

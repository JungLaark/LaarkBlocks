"""지식베이스(RAG) 코어 — 청킹, 하이브리드 검색, 도구 주입.

하이브리드 검색 설계
--------------------
벡터 검색(의미 유사도)과 BM25(키워드 정확도)는 상호 보완적이다:
  - "정산기 재부팅 절차" 같은 의미 질의 → 벡터가 강함
  - "config.yml", "APS-3000" 같은 고유명사/코드 질의 → BM25 가 강함

두 랭킹을 RRF(Reciprocal Rank Fusion)로 융합한다.
RRF 는 점수 스케일이 다른 두 랭커를 정규화 없이 결합할 수 있어
(순위만 사용: score = Σ 1/(k + rank)) 실무에서 가장 견고한 융합 방식이다.

저장소는 1단계로 인메모리 구현이다. 운영 전환 시 pgvector 로 교체하되,
KnowledgeBase 의 공개 인터페이스(add_document/search)는 유지한다.

도구 주입
---------
지식베이스를 생성하면 'kb__{name}' 검색 도구가 tool_registry 에 등록된다.
MCP 와 동일한 패턴 — 엔진은 도구의 출처(내장/MCP/KB)를 구분하지 않는다.
"""

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field

from langchain_core.embeddings import Embeddings
from langchain_core.tools import StructuredTool

from src.core.embedding_factory import create_embeddings
from src.core.tool_registry import register_tool, unregister_tool
from src.schemas.knowledge import KnowledgeBaseConfig, SearchResult

logger = logging.getLogger(__name__)

_KB_TOOL_FMT = "kb__{name}"
_RRF_K = 60  # RRF 상수 — 표준값 60 (상위 순위 간 점수 차를 완만하게 만든다)


# ──────────────────────────────────────────────────────────────────
# 청킹
# ──────────────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """문단 경계를 우선 존중하는 슬라이딩 윈도우 청킹.

    문단(빈 줄 기준)을 모으다가 chunk_size 를 넘으면 청크를 확정하고,
    직전 청크의 꼬리(overlap)를 다음 청크 머리에 이어붙여 문맥 단절을 줄인다.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        # 한 문단이 chunk_size 를 초과하면 문자 단위로 강제 분할
        while len(para) > chunk_size:
            head, para = para[:chunk_size], para[chunk_size - overlap :]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(head)

        if len(current) + len(para) + 1 > chunk_size and current:
            chunks.append(current)
            # 중첩: 직전 청크의 꼬리를 새 청크의 머리로
            current = (current[-overlap:] + "\n" + para) if overlap else para
        else:
            current = f"{current}\n{para}" if current else para

    if current:
        chunks.append(current)
    return chunks


# ──────────────────────────────────────────────────────────────────
# BM25 (직접 구현 — 외부 의존성 없이 ~40줄)
# ──────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """한글/영문/숫자 토큰화. (형태소 분석은 고도화 항목 — 현재는 어절 수준)"""
    return re.findall(r"[가-힣a-zA-Z0-9_.]+", text.lower())


class BM25:
    """Okapi BM25 랭커.

    score(q, d) = Σ IDF(t) · (tf · (k1+1)) / (tf + k1 · (1 - b + b · |d|/avgdl))
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self._docs: list[list[str]] = []
        self._df: Counter = Counter()  # 토큰 → 등장 문서 수
        self._avgdl: float = 0.0

    def add(self, text: str) -> None:
        tokens = _tokenize(text)
        self._docs.append(tokens)
        self._df.update(set(tokens))
        self._avgdl = sum(len(d) for d in self._docs) / len(self._docs)

    def scores(self, query: str) -> list[float]:
        """모든 문서에 대한 질의 점수. 인덱스는 add() 순서와 일치."""
        n = len(self._docs)
        result: list[float] = []
        q_tokens = _tokenize(query)

        for doc in self._docs:
            tf = Counter(doc)
            score = 0.0
            for t in q_tokens:
                if t not in tf:
                    continue
                idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                denom = tf[t] + self.k1 * (1 - self.b + self.b * len(doc) / self._avgdl)
                score += idf * tf[t] * (self.k1 + 1) / denom
            result.append(score)
        return result


# ──────────────────────────────────────────────────────────────────
# 지식베이스
# ──────────────────────────────────────────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class _Chunk:
    content: str
    source: str
    vector: list[float]


@dataclass
class KnowledgeBase:
    """단일 지식베이스 — 청크 저장소 + 하이브리드 검색."""

    config: KnowledgeBaseConfig
    embeddings: Embeddings
    chunks: list[_Chunk] = field(default_factory=list)
    bm25: BM25 = field(default_factory=BM25)
    document_count: int = 0

    def add_document(self, text: str, source: str = "unknown") -> int:
        """문서를 청킹→임베딩→색인. 추가된 청크 수를 반환."""
        pieces = chunk_text(text, self.config.chunk_size, self.config.chunk_overlap)
        if not pieces:
            return 0
        # embed_documents 는 배치 처리 — 청크별 개별 호출 대비 효율적
        vectors = self.embeddings.embed_documents(pieces)
        for content, vector in zip(pieces, vectors):
            self.chunks.append(_Chunk(content=content, source=source, vector=vector))
            self.bm25.add(content)
        self.document_count += 1
        return len(pieces)

    def search(self, query: str, top_k: int | None = None) -> list[SearchResult]:
        """하이브리드 검색: 벡터 랭킹 + BM25 랭킹 → RRF 융합."""
        if not self.chunks:
            return []
        k = top_k or self.config.top_k

        # 1) 벡터 랭킹 (의미 유사도)
        q_vec = self.embeddings.embed_query(query)
        vec_ranked = sorted(
            range(len(self.chunks)),
            key=lambda i: _cosine(q_vec, self.chunks[i].vector),
            reverse=True,
        )

        # 2) BM25 랭킹 (키워드 매칭)
        bm25_scores = self.bm25.scores(query)
        bm25_ranked = sorted(
            range(len(self.chunks)), key=lambda i: bm25_scores[i], reverse=True
        )

        # 3) RRF 융합 — 점수 스케일 정규화 없이 순위만으로 결합
        fused: dict[int, float] = {}
        for ranked in (vec_ranked, bm25_ranked):
            for rank, idx in enumerate(ranked):
                fused[idx] = fused.get(idx, 0.0) + 1.0 / (_RRF_K + rank + 1)

        top = sorted(fused, key=fused.get, reverse=True)[:k]
        return [
            SearchResult(
                content=self.chunks[i].content,
                source=self.chunks[i].source,
                score=round(fused[i], 6),
            )
            for i in top
        ]


# ──────────────────────────────────────────────────────────────────
# 매니저 (수명주기 + 도구 주입)
# ──────────────────────────────────────────────────────────────────

class KnowledgeBaseManager:
    """지식베이스 생성/삭제와 'kb__{name}' 검색 도구의 수명주기를 관리."""

    def __init__(self) -> None:
        self._bases: dict[str, KnowledgeBase] = {}

    def create(self, config: KnowledgeBaseConfig) -> KnowledgeBase:
        if config.name in self._bases:
            raise ValueError(f"이미 존재하는 지식베이스입니다: {config.name}")

        kb = KnowledgeBase(config=config, embeddings=create_embeddings(config.embedding_model))
        self._bases[config.name] = kb

        # 검색 도구를 레지스트리에 주입 — 에이전트 설정(tools)에서
        # "kb__{name}" 으로 참조하면 이 지식베이스를 검색하게 된다.
        def _search(query: str) -> str:
            """(클로저) 이 지식베이스에 대한 하이브리드 검색."""
            results = kb.search(query)
            if not results:
                return "관련 문서를 찾지 못했습니다."
            return "\n\n".join(
                f"[출처: {r.source}]\n{r.content}" for r in results
            )

        register_tool(
            StructuredTool.from_function(
                func=_search,
                name=_KB_TOOL_FMT.format(name=config.name),
                description=(
                    f"지식베이스 '{config.name}' 검색. {config.description} "
                    "관련 정보가 필요하면 이 도구로 검색한 뒤 결과를 근거로 답하세요."
                ),
            )
        )
        logger.info("지식베이스 생성됨: %s (tool=kb__%s)", config.name, config.name)
        return kb

    def get(self, name: str) -> KnowledgeBase:
        if name not in self._bases:
            raise KeyError(f"지식베이스를 찾을 수 없습니다: {name}")
        return self._bases[name]

    def delete(self, name: str) -> None:
        self._bases.pop(name, None)
        unregister_tool(_KB_TOOL_FMT.format(name=name))

    def status(self) -> dict[str, dict]:
        """운영 콘솔용 현황: 이름 → 문서/청크 수."""
        return {
            name: {
                "documents": kb.document_count,
                "chunks": len(kb.chunks),
                "embedding_model": kb.config.embedding_model,
                "tool": _KB_TOOL_FMT.format(name=name),
            }
            for name, kb in self._bases.items()
        }


# 앱 전역 공유 싱글턴
kb_manager = KnowledgeBaseManager()

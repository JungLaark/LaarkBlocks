"""지식베이스(RAG) 테스트 — 청킹, 하이브리드 검색, 도구 주입, 에이전트 연동."""

import pytest
from langchain_core.messages import AIMessage

from src.core.engine import engine
from src.core.knowledge import BM25, KnowledgeBaseManager, chunk_text
from src.core.tool_registry import available_tools
from src.schemas.agent import StreamEventType
from src.schemas.knowledge import KnowledgeBaseConfig
from tests.conftest import collect, make_config

DOC_DEPLOY = """정산기 배포 매뉴얼.

APS 관리 시스템의 일괄다운로드 메뉴에서 배포할 기기를 다중 선택한다.
SW 버전을 입력하고 파일을 등록하면 정산기 재부팅 시 FileAgent 가
버전을 비교하여 신규 파일을 자동 다운로드한다.

FileAgent 는 config.yml 로 소켓 주소와 점포코드를 설정한다."""

DOC_COOKING = """김치찌개 레시피.

돼지고기를 볶다가 김치를 넣고 함께 볶는다.
물을 붓고 두부와 파를 넣어 끓인다."""


@pytest.fixture
def kb(use_fake_embeddings):
    """fake 임베딩 기반 지식베이스 + 문서 2건 색인. 종료 시 도구 정리."""
    manager = KnowledgeBaseManager()
    base = manager.create(KnowledgeBaseConfig(
        name="test-kb",
        description="정산기 배포 매뉴얼 지식베이스",
        embedding_model="fake/hash",
        chunk_size=200,
        chunk_overlap=30,
    ))
    base.add_document(DOC_DEPLOY, source="deploy.md")
    base.add_document(DOC_COOKING, source="recipe.md")
    yield manager, base
    manager.delete("test-kb")


class TestChunking:
    def test_respects_chunk_size(self):
        chunks = chunk_text("가나다라마" * 100, chunk_size=100, overlap=20)
        assert all(len(c) <= 100 for c in chunks)
        assert len(chunks) > 1

    def test_keeps_short_text_single_chunk(self):
        assert chunk_text("짧은 문서", chunk_size=500, overlap=50) == ["짧은 문서"]


class TestBM25:
    def test_keyword_ranking(self):
        bm25 = BM25()
        bm25.add("정산기 배포 매뉴얼과 FileAgent 설정")
        bm25.add("김치찌개 끓이는 방법")
        scores = bm25.scores("FileAgent 설정 방법")
        # 키워드가 겹치는 첫 문서가 더 높아야 한다
        assert scores[0] > scores[1]


class TestHybridSearch:
    def test_semantic_query_finds_deploy_doc(self, kb):
        _, base = kb
        results = base.search("정산기 파일 배포 절차")
        assert results, "검색 결과가 있어야 한다"
        assert results[0].source == "deploy.md"

    def test_keyword_query_finds_exact_term(self, kb):
        """'config.yml' 같은 고유 키워드 — BM25 가 주도하는 케이스."""
        _, base = kb
        results = base.search("config.yml")
        assert results[0].source == "deploy.md"
        assert "config.yml" in results[0].content

    def test_off_topic_query_ranks_cooking_doc(self, kb):
        _, base = kb
        results = base.search("김치찌개 레시피")
        assert results[0].source == "recipe.md"

    def test_empty_kb_returns_empty(self, use_fake_embeddings):
        manager = KnowledgeBaseManager()
        base = manager.create(KnowledgeBaseConfig(
            name="empty-kb", embedding_model="fake/hash"
        ))
        assert base.search("아무거나") == []
        manager.delete("empty-kb")


class TestToolInjection:
    def test_kb_tool_registered_and_removed(self, kb):
        manager, _ = kb
        names = [t["name"] for t in available_tools()]
        assert "kb__test-kb" in names

        manager.delete("test-kb")
        names = [t["name"] for t in available_tools()]
        assert "kb__test-kb" not in names

    def test_duplicate_name_rejected(self, kb):
        manager, _ = kb
        with pytest.raises(ValueError, match="이미 존재"):
            manager.create(KnowledgeBaseConfig(
                name="test-kb", embedding_model="fake/hash"
            ))


async def test_agent_uses_kb_tool_e2e(kb, use_scripted_model):
    """에이전트가 kb 도구로 검색하고 그 결과를 받는 전체 흐름."""
    use_scripted_model([
        AIMessage(
            content="",
            tool_calls=[{
                "name": "kb__test-kb",
                "args": {"query": "FileAgent config.yml 설정"},
                "id": "call_kb_1",
                "type": "tool_call",
            }],
        ),
        AIMessage(content="FileAgent 는 config.yml 로 설정합니다."),
    ])

    config = make_config(tools=["kb__test-kb"])
    events = await collect(engine.astream(config, "FileAgent 설정 방법은?"))

    tool_end = next(e for e in events if e["type"] == StreamEventType.TOOL_END)
    assert tool_end["tool"] == "kb__test-kb"
    # 검색 도구가 출처 표기와 함께 실제 문서 내용을 반환했는지
    assert "deploy.md" in tool_end["output"]
    assert "config.yml" in tool_end["output"]

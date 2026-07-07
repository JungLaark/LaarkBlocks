# LaarkBlocks (라크블록스)

**설정 기반 AI 에이전트 플랫폼** — 에이전트를 코드가 아닌 JSON 설정으로 정의하면,
실행 엔진이 런타임에 LangGraph 그래프로 동적 빌드하여 SSE로 스트리밍 실행합니다.

> 분산된 실행 단위를 **등록 → 배포 → 모니터링**하는 플랫폼 설계를,
> 그 대상만 "기기"에서 "AI 에이전트"로 바꾼 프로젝트입니다.

## 핵심 개념

```
  AgentConfig (JSON)          AgentEngine                클라이언트
 ┌──────────────────┐   ┌──────────────────────┐   ┌────────────────┐
 │ agent_id         │   │ 1. LLM Factory       │   │  SSE Stream    │
 │ model_name ──────┼──▶│    (provider 추상화)  │   │  event: token  │
 │ system_prompt    │   │ 2. Tool Registry     │──▶│  event: tool_* │
 │ tools: [...]     │   │    (이름→구현체 해석)  │   │  event: done   │
 │ model_params     │   │ 3. StateGraph 빌드    │   │  event: error  │
 └──────────────────┘   │    (ReAct 루프 조립)  │   └────────────────┘
                        └──────────────────────┘
```

- **에이전트 = 설정**: 새 에이전트 추가에 코드 배포가 필요 없다
- **모델 추상화**: `"ollama/qwen3:8b"` ↔ `"openai/gpt-4o"` — 설정 한 줄로 로컬 sLLM/외부 API 전환
- **이벤트 계약**: token / tool_start / tool_end / done / error — 빌더 스튜디오와 운영 콘솔이 같은 계약을 소비

## 빠른 시작

```bash
# 1. 의존성 설치
python -m venv .venv && .venv\Scripts\activate   # (Windows)
pip install -r requirements.txt

# 2. 서버 실행
uvicorn src.main:app --reload
# → Swagger UI: http://localhost:8000/docs

# 3. (로컬 모델 사용 시) Ollama 실행 및 모델 준비
ollama pull qwen3:8b

# 4. 스트리밍 데모
python scripts/run_client.py "(6 + 4) * 7 을 계산해줘"
```

### API 한눈에

| Method | Path | 설명 |
|---|---|---|
| POST | `/api/v1/agents/run` | 에이전트 실행 (SSE 스트리밍, `session_id`로 멀티턴) |
| GET | `/api/v1/agents/{id}/sessions/{sid}/history` | 세션 대화 이력 조회 |
| GET | `/api/v1/agents/presets` | 에이전트 프리셋 목록 |
| GET | `/api/v1/tools` | 사용 가능한 도구 목록 (내장 + MCP) |
| GET | `/api/v1/providers` | 모델 provider 목록 |
| GET | `/api/v1/mcp/servers` | 연결된 MCP 서버/도구 현황 |
| POST | `/api/v1/mcp/refresh` | MCP 설정 무중단 재로드 |
| POST | `/api/v1/knowledge` | 지식베이스 생성 (`kb__이름` 도구 자동 등록) |
| POST | `/api/v1/knowledge/{name}/documents` | 문서 등록 (청킹→임베딩→색인) |
| POST | `/api/v1/knowledge/{name}/search` | 하이브리드 검색 (디버그) |
| GET | `/api/v1/traces` | 실행 이력 목록 (필터/페이지네이션) |
| GET | `/api/v1/traces/{id}` | 트레이스 상세 — LLM/도구 스팬 타임라인 |
| GET | `/api/v1/stats/usage` | 토큰·비용·지연·에러율 집계 |
| GET | `/api/v1/stats/usage/by-agent` | 에이전트별 비용 집계 |
| POST | `/api/v1/traces/{id}/evaluate` | LLM-as-judge 품질 평가 실행 |
| POST | `/api/v1/traces/{id}/evaluations` | 평가 수동 등록 (사람 피드백) |
| GET | `/health` | 헬스체크 |

### 실행 요청 예시

```json
POST /api/v1/agents/run
{
  "agent_config": {
    "agent_id": "tool-demo",
    "name": "도구 사용 데모",
    "model_name": "ollama/qwen3:8b",
    "system_prompt": "계산이 필요하면 calculator 도구를 사용하세요.",
    "tools": ["calculator"]
  },
  "user_message": "(6 + 4) * 7 은?"
}
```

## 프로젝트 구조

```
src/
├── main.py               # FastAPI 앱 팩토리 (lifespan: MCP 자동 연결)
├── config.py             # 환경변수 기반 설정 (LAARK_ 접두어)
├── schemas/agent.py      # AgentConfig — "에이전트는 설정이다"의 계약
├── core/
│   ├── llm_factory.py        # 모델 추상화 (provider 레지스트리, lazy import)
│   ├── embedding_factory.py  # 임베딩 추상화 (동일 패턴)
│   ├── tool_registry.py      # 도구 레지스트리 (이름 → 구현체)
│   ├── mcp_manager.py        # MCP 서버 연결/도구 동적 주입 (mcp__서버__도구)
│   ├── knowledge.py          # RAG — 청킹, BM25+벡터 RRF 하이브리드 (kb__이름)
│   ├── tracing.py            # TraceCollector + 비동기 적재 파이프라인(sink)
│   ├── pricing.py            # 모델 토큰 단가표 → 비용 계산
│   ├── judge.py              # LLM-as-judge 품질 평가
│   └── engine.py             # 실행 엔진 — 동적 빌드, 세션 메모리, 슈퍼바이저, 계측
├── db/
│   ├── models.py             # ORM — traces / spans / evaluations
│   ├── database.py           # async 엔진 (SQLite ↔ PostgreSQL 겸용)
│   └── repository.py         # 적재/조회/집계 (Pydantic ↔ ORM 경계)
└── api/v1/endpoints.py       # SSE API + 세션/MCP/지식베이스/운영 콘솔
tests/                    # Fake provider 주입으로 LLM 서버 없이 전 경로 검증
configs/agents/           # 에이전트 프리셋 (JSON)
scripts/run_client.py     # SSE 수동 테스트 클라이언트
```

## 테스트

실제 LLM 서버 없이 CI에서 전체 경로를 검증합니다.
모델 추상화 레이어에 **각본(scripted) 기반 Fake 모델을 provider로 주입**하여
그래프 빌드 → ReAct 도구 루프 → SSE 이벤트 계약까지 커버합니다.

```bash
pytest -v    # 18 passed
```

## 로드맵

- [x] **1단계 — 실행 엔진 코어**: 설정 기반 동적 그래프 빌드, 모델 추상화, SSE 스트리밍
- [x] **2단계(a) — 세션 메모리 & MCP**: 체크포인터 기반 멀티턴 대화(에이전트/세션 격리), MCP 서버 도구 동적 주입(`mcp__서버__도구`), 무중단 재로드
- [x] **2단계(b) — 지식 레이어 & 멀티 에이전트**: RAG 지식베이스(벡터 + 직접 구현한 BM25 → RRF 하이브리드 검색, `kb__이름` 도구 자동 주입), 슈퍼바이저 멀티 에이전트(agent-as-tool 위임, 워커 스트림 분리)
- [x] **3단계 — 운영 콘솔(LLMOps) 백엔드**: Trace/Span 실행 이력 자동 수집(워커 비용 포함), 토큰·비용 집계(모델 단가표), 비동기 적재 파이프라인(sink, 응답 지연 0), LLM-as-judge 품질 평가. SQLite ↔ PostgreSQL 겸용(SQLAlchemy async)
- [ ] **4단계 — 빌더 스튜디오 (React)**: 에이전트 편집 UI, 플레이그라운드, 트레이스 뷰어, 비용 대시보드
- [ ] **고도화**: pgvector 영속화, 크로스인코더 리랭킹, RAGAS 평가, sLLM 파인튜닝 모델 등록
- [ ] **3단계 — 빌더 스튜디오 (React)**: 에이전트 편집 UI, 버전 저장/롤백, 테스트 플레이그라운드
- [ ] **4단계 — 운영 콘솔 (LLMOps)**: 실행 이력/트레이스 뷰어, 토큰·비용 집계, LLM-as-judge 품질 평가
- [ ] **5단계 — 모델 확장**: LoRA 파인튜닝 sLLM 등록, GGUF 양자화 서빙
- [ ] **6단계 — 데모 3종**: 문서 QA(RAG) / 리서치(멀티 에이전트) / SQL 분석(MCP 도구)

## 기술 스택

Python 3.12 · FastAPI · LangGraph · LangChain Core · sse-starlette · Pydantic v2 · Ollama (로컬 sLLM) · pytest

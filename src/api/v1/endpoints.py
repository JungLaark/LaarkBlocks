"""API v1 — 에이전트 실행 및 플랫폼 메타 조회 엔드포인트.

SSE 이벤트 계약 (클라이언트 ↔ 서버)
-----------------------------------
모든 이벤트는 `event:` 필드에 StreamEventType 값을,
`data:` 필드에 JSON 페이로드를 담는다.

  event: token       data: {"content": "안녕"}
  event: tool_start  data: {"tool": "calculator", "input": {...}}
  event: tool_end    data: {"tool": "calculator", "output": "56"}
  event: done        data: {"agent_id": "...", "content": "<전체 응답>"}
  event: error       data: {"message": "..."}

빌더 스튜디오(React)의 테스트 플레이그라운드와 운영 콘솔 트레이스 뷰어가
이 계약을 그대로 소비한다.
"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from src.config import get_settings
from src.core.engine import engine
from src.core.knowledge import kb_manager
from src.core.llm_factory import available_providers
from src.core.mcp_manager import mcp_manager
from src.core.tool_registry import available_tools
from src.schemas.agent import AgentConfig, AgentRunRequest, StreamEventType
from src.schemas.knowledge import (
    DocumentIn,
    KnowledgeBaseConfig,
    SearchRequest,
    SearchResult,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ──────────────────────────────────────────────────────────────────
# 에이전트 실행 (SSE 스트리밍)
# ──────────────────────────────────────────────────────────────────

@router.post("/agents/run", summary="에이전트 실행 (SSE 스트리밍)")
async def run_agent(request: Request, body: AgentRunRequest) -> EventSourceResponse:
    """에이전트 설정(JSON) + 사용자 메시지를 받아 실행하고,
    모델 토큰/도구 호출 과정을 SSE 로 실시간 스트리밍한다.

    설정 기반 실행이므로 클라이언트(빌더 스튜디오)는 저장하지 않은
    편집 중인 에이전트도 즉시 테스트할 수 있다.
    """

    async def event_generator():
        try:
            async for ev in engine.astream(
                body.agent_config, body.user_message, session_id=body.session_id
            ):
                # 클라이언트가 연결을 끊으면 즉시 실행을 중단하여
                # 모델 서버 리소스 낭비를 막는다.
                if await request.is_disconnected():
                    logger.info(
                        "client disconnected — aborting agent run (agent_id=%s)",
                        body.agent_config.agent_id,
                    )
                    break

                # dict 에서 type 을 분리해 SSE 의 event 필드로 승격
                ev_type: StreamEventType = ev.pop("type")
                yield {
                    "event": ev_type.value,
                    "data": json.dumps(ev, ensure_ascii=False),
                }
        except Exception as e:  # noqa: BLE001 — 스트림 중 오류는 이벤트로 전달
            # SSE 는 이미 200 응답이 시작된 상태이므로 HTTP 에러를 던질 수 없다.
            # 오류도 이벤트 계약의 일부(error)로 클라이언트에 전달한다.
            logger.exception(
                "agent run failed (agent_id=%s)", body.agent_config.agent_id
            )
            yield {
                "event": StreamEventType.ERROR.value,
                "data": json.dumps({"message": str(e)}, ensure_ascii=False),
            }

    # ping: 프록시/로드밸런서의 유휴 커넥션 종료를 막는 keep-alive 간격(초)
    return EventSourceResponse(event_generator(), ping=15)


# ──────────────────────────────────────────────────────────────────
# 플랫폼 메타 조회 (빌더 스튜디오 UI 데이터 소스)
# ──────────────────────────────────────────────────────────────────

@router.get("/tools", summary="사용 가능한 도구 목록")
async def list_tools() -> list[dict[str, str]]:
    """도구 레지스트리에 등록된 도구 목록. 빌더 스튜디오의 도구 선택 UI 용."""
    return available_tools()


@router.get("/providers", summary="사용 가능한 모델 provider 목록")
async def list_providers() -> list[str]:
    """모델 추상화 레이어에 등록된 provider 목록."""
    return available_providers()


@router.get("/agents/presets", summary="에이전트 프리셋 목록")
async def list_agent_presets() -> list[AgentConfig]:
    """configs/agents/*.json 프리셋을 로드해 반환.

    1단계에서는 파일 기반이지만, 3단계(버전/이력 관리)에서
    DB 저장소로 교체된다. (엔드포인트 계약은 유지)
    """
    preset_dir = Path(get_settings().agent_preset_dir)
    if not preset_dir.is_dir():
        return []

    presets: list[AgentConfig] = []
    for path in sorted(preset_dir.glob("*.json")):
        try:
            presets.append(AgentConfig.model_validate_json(path.read_text("utf-8")))
        except ValueError as e:
            # 깨진 프리셋 하나가 전체 목록 조회를 막지 않도록 스킵 + 경고
            logger.warning("invalid preset skipped: %s (%s)", path.name, e)
    return presets


@router.get("/agents/presets/{agent_id}", summary="에이전트 프리셋 단건 조회")
async def get_agent_preset(agent_id: str) -> AgentConfig:
    """agent_id 로 프리셋 단건 조회. 빌더 스튜디오의 편집 화면 진입용."""
    for preset in await list_agent_presets():
        if preset.agent_id == agent_id:
            return preset
    raise HTTPException(status_code=404, detail=f"프리셋을 찾을 수 없습니다: {agent_id}")


# ──────────────────────────────────────────────────────────────────
# 세션 (대화 이력)
# ──────────────────────────────────────────────────────────────────

@router.get(
    "/agents/{agent_id}/sessions/{session_id}/history",
    summary="세션 대화 이력 조회",
)
async def get_session_history(agent_id: str, session_id: str) -> list[dict[str, str]]:
    """특정 에이전트-세션의 대화 이력을 조회한다.

    체크포인터의 thread_id 가 agent_id 로 네임스페이스되어 있으므로
    두 값이 모두 필요하다. 운영 콘솔의 대화 뷰어가 이 API 를 사용한다.
    """
    # 이력 조회는 thread_id 계산에 agent_id 만 필요하므로 최소 설정으로 충분
    stub = AgentConfig(
        agent_id=agent_id,
        name=agent_id,
        model_name="ollama/_",  # 사용되지 않음 (모델 호출 없음)
        system_prompt="_",
    )
    history = await engine.get_history(stub, session_id)
    if not history:
        raise HTTPException(
            status_code=404,
            detail=f"세션 이력이 없습니다: agent={agent_id}, session={session_id}",
        )
    return history


# ──────────────────────────────────────────────────────────────────
# MCP (외부 도구 서버)
# ──────────────────────────────────────────────────────────────────

@router.get("/mcp/servers", summary="연결된 MCP 서버 및 도구 현황")
async def list_mcp_servers() -> dict[str, list[str]]:
    """서버명 → 주입된 도구 이름 목록."""
    return mcp_manager.status()


@router.post("/mcp/refresh", summary="MCP 설정 재로드")
async def refresh_mcp() -> dict[str, int]:
    """configs/mcp_servers.json 을 다시 읽어 도구를 재주입한다.

    도구 서버 추가/제거를 서비스 재시작 없이 반영하기 위한 운영 API.
    """
    from pathlib import Path

    return await mcp_manager.refresh(Path(get_settings().mcp_config_path))


# ──────────────────────────────────────────────────────────────────
# 지식베이스 (RAG)
# ──────────────────────────────────────────────────────────────────

@router.post("/knowledge", summary="지식베이스 생성", status_code=201)
async def create_knowledge_base(config: KnowledgeBaseConfig) -> dict[str, str]:
    """지식베이스를 생성하면 'kb__{name}' 검색 도구가 자동 등록되어
    에이전트 설정(tools)에서 즉시 참조할 수 있다."""
    try:
        kb_manager.create(config)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"name": config.name, "tool": f"kb__{config.name}"}


@router.get("/knowledge", summary="지식베이스 현황")
async def list_knowledge_bases() -> dict[str, dict]:
    """이름 → 문서/청크 수 및 노출 도구명."""
    return kb_manager.status()


@router.post("/knowledge/{name}/documents", summary="문서 등록")
async def add_document(name: str, doc: DocumentIn) -> dict[str, int]:
    """문서를 청킹→임베딩→색인한다. 추가된 청크 수를 반환."""
    try:
        kb = kb_manager.get(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"chunks_added": kb.add_document(doc.text, source=doc.source)}


@router.post("/knowledge/{name}/search", summary="하이브리드 검색 (디버그)")
async def search_knowledge_base(name: str, req: SearchRequest) -> list[SearchResult]:
    """운영 콘솔/디버깅용 직접 검색 — 에이전트를 거치지 않고 검색 품질을 확인."""
    try:
        kb = kb_manager.get(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return kb.search(req.query, top_k=req.top_k)


@router.delete("/knowledge/{name}", summary="지식베이스 삭제")
async def delete_knowledge_base(name: str) -> dict[str, str]:
    """지식베이스와 그 검색 도구를 함께 제거한다."""
    kb_manager.delete(name)
    return {"deleted": name}

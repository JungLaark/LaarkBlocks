"""LaarkBlocks 애플리케이션 엔트리포인트.

실행:
    uvicorn src.main:app --reload

API 문서:
    http://localhost:8000/docs (Swagger UI)
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src import __version__
from src.api.v1.endpoints import router as v1_router
from src.config import get_settings
from src.core import tracing
from src.core.mcp_manager import mcp_manager
from src.core.pricing import load_pricing_overrides
from src.db.database import database
from src.db.repository import trace_repo

# 운영 콘솔(4단계)에서 구조화 로깅(JSON)으로 교체 예정 — 지금은 표준 포맷
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 수명주기 훅.

    기동: DB 초기화 → 트레이스 sink 연결 → 단가표 로드 → MCP 연결
    종료: 대기 중인 트레이스 적재 완료(drain) → 자원 정리

    관측/도구 초기화 실패는 경고만 남기고 기동을 계속한다.
    (부가 기능 장애가 플랫폼 기동을 막으면 안 되므로)
    """
    log = logging.getLogger(__name__)
    settings = get_settings()

    # 1) 운영 콘솔: 실행 이력 DB + 트레이스 적재 파이프라인
    try:
        await database.init(settings.database_url)
        tracing.set_sink(trace_repo.save_trace)
        load_pricing_overrides(Path(settings.pricing_config_path))
    except Exception:
        log.exception("트레이싱 초기화 실패 — 실행 이력 적재 비활성")

    # 2) MCP 도구 서버 연결
    try:
        summary = await mcp_manager.connect(Path(settings.mcp_config_path))
        if summary:
            log.info("MCP 도구 주입 완료: %s", summary)
    except Exception:
        log.exception("MCP 초기화 실패 — 내장 도구만 사용")

    yield

    # 우아한 종료: 진행 중인 트레이스 적재를 마저 끝낸다
    await tracing.drain()
    tracing.set_sink(None)
    await database.dispose()
    mcp_manager.disconnect_all()


def create_app() -> FastAPI:
    """FastAPI 앱 팩토리. 테스트에서 독립된 앱 인스턴스 생성에도 사용한다."""
    settings = get_settings()

    app = FastAPI(
        lifespan=lifespan,
        title=settings.app_name,
        version=__version__,
        description=(
            "설정 기반 AI 에이전트 플랫폼 — "
            "에이전트를 JSON 설정으로 정의하고, 실행 엔진이 런타임에 "
            "LangGraph 그래프로 동적 빌드하여 SSE 로 스트리밍 실행한다."
        ),
    )

    # 빌더 스튜디오(React 개발 서버) 등에서의 브라우저 호출 허용
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(v1_router, prefix=settings.api_v1_prefix)

    @app.get("/health", tags=["ops"], summary="헬스체크")
    async def health() -> dict[str, str]:
        """로드밸런서/컨테이너 오케스트레이터용 liveness 체크."""
        return {"status": "ok", "service": settings.app_name, "version": __version__}

    return app


app = create_app()

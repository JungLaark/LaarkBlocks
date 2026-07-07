"""애플리케이션 전역 설정.

환경변수(.env) 기반으로 로드되며, 접두어 `LAARK_` 를 사용한다.
예) LAARK_OLLAMA_BASE_URL=http://localhost:11434
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """LaarkBlocks 런타임 설정."""

    model_config = SettingsConfigDict(
        env_prefix="LAARK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 서비스 메타 ──────────────────────────────────────────────
    app_name: str = "LaarkBlocks"
    api_v1_prefix: str = "/api/v1"

    # ── 모델 provider ───────────────────────────────────────────
    # 로컬 sLLM(Ollama) 서버 주소 — llm_factory 에서 사용
    ollama_base_url: str = "http://localhost:11434"

    # ── CORS ────────────────────────────────────────────────────
    # 콤마 구분 문자열 → 리스트 (빌더 스튜디오 프론트엔드 개발 서버 등)
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # ── 에이전트 프리셋 ──────────────────────────────────────────
    # 미리 정의된 에이전트 설정(JSON) 파일이 위치한 디렉터리
    agent_preset_dir: str = "configs/agents"

    # ── MCP ─────────────────────────────────────────────────────
    # MCP 서버 목록 설정 파일 — 존재하면 앱 기동 시 자동 연결된다
    mcp_config_path: str = "configs/mcp_servers.json"

    # ── 운영 콘솔 (LLMOps) ──────────────────────────────────────
    # 실행 이력 저장소. 운영 전환 시 postgresql+asyncpg://... 로 교체
    database_url: str = "sqlite+aiosqlite:///./laarkblocks.db"
    # 모델 토큰 단가표 오버라이드 파일 (선택)
    pricing_config_path: str = "configs/model_pricing.json"

    @property
    def cors_origin_list(self) -> list[str]:
        """콤마 구분 CORS 설정을 리스트로 변환."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """설정 싱글턴. FastAPI Depends 로도 주입 가능하다."""
    return Settings()

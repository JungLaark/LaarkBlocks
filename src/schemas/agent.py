"""에이전트 설정 스키마.

LaarkBlocks의 핵심 철학: "에이전트는 코드가 아니라 설정이다."
이 스키마(JSON)가 곧 에이전트의 정의이며, 실행 엔진(core/engine.py)이
이를 런타임에 해석하여 LangGraph 그래프로 동적 빌드한다.

model_name 은 "provider/model" 형식을 사용한다.
  - "ollama/qwen3:8b"           → 로컬 sLLM (Ollama)
  - "openai/gpt-4o"             → OpenAI API
  - "anthropic/claude-sonnet-5" → Anthropic API
provider 부분만 보고 llm_factory 가 적절한 구현체를 선택하므로,
설정 파일 수정만으로 로컬 ↔ 외부 API 전환이 가능하다.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ModelParams(BaseModel):
    """모델 호출 파라미터 (provider 공통 서브셋).

    provider 별 고유 옵션이 필요해지면 extra dict 를 추가하는 방식으로
    확장한다. (스키마를 provider 에 종속시키지 않기 위함)
    """

    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, gt=0)


class AgentConfig(BaseModel):
    """에이전트 정의 — 플랫폼에 등록/실행되는 최소 단위.

    빌더 스튜디오(2단계)에서 이 스키마를 편집하고,
    버전 관리(3단계)에서 이 스키마의 스냅샷을 저장하게 된다.
    """

    agent_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
        description="에이전트 고유 식별자 (소문자/숫자/하이픈/언더스코어)",
        examples=["doc-qa", "researcher"],
    )
    name: str = Field(..., min_length=1, max_length=100, description="표시용 이름")
    description: str = Field(default="", max_length=500, description="에이전트 설명")

    model_name: str = Field(
        ...,
        description='사용할 모델. "provider/model" 형식 (예: "ollama/qwen3:8b")',
        examples=["ollama/qwen3:8b", "openai/gpt-4o"],
    )
    system_prompt: str = Field(
        ...,
        min_length=1,
        description="에이전트의 역할/규칙을 정의하는 시스템 프롬프트",
    )
    tools: list[str] = Field(
        default_factory=list,
        description="사용할 도구 이름 목록 — core/tool_registry.py 에 등록된 이름",
        examples=[["get_current_time", "calculator"]],
    )
    model_params: ModelParams = Field(default_factory=ModelParams)

    # ── 멀티 에이전트 (슈퍼바이저 패턴) ──────────────────────────
    # workers 가 있으면 이 에이전트는 '슈퍼바이저'가 된다.
    # 각 워커는 'delegate_to__{agent_id}' 도구로 노출되어, 슈퍼바이저(모델)가
    # 워커의 description 을 보고 작업을 위임(라우팅)한다. (agent-as-tool 패턴)
    workers: list["AgentConfig"] = Field(
        default_factory=list,
        description="위임 대상 워커 에이전트 목록. description 이 라우팅 기준이 되므로 워커의 역할을 구체적으로 쓸 것",
    )

    @model_validator(mode="after")
    def validate_worker_depth(self) -> "AgentConfig":
        """계층은 슈퍼바이저 → 워커 1단계까지만 허용한다.

        더 깊은 중첩은 토큰 비용과 지연이 기하급수적으로 늘고 디버깅이
        어려워지므로, 플랫폼 차원에서 명시적으로 차단한다.
        """
        for w in self.workers:
            if w.workers:
                raise ValueError(
                    f"워커 '{w.agent_id}' 는 자신의 워커를 가질 수 없습니다 "
                    "(중첩은 1단계까지만 지원)"
                )
        return self

    @field_validator("model_name")
    @classmethod
    def validate_model_name(cls, v: str) -> str:
        """'provider/model' 형식 검증. 잘못된 설정을 실행 전에 차단한다."""
        if "/" not in v:
            raise ValueError(
                f'model_name 은 "provider/model" 형식이어야 합니다. (입력값: "{v}")'
            )
        provider, _, model = v.partition("/")
        if not provider or not model:
            raise ValueError(f'provider 또는 model 이 비어 있습니다. (입력값: "{v}")')
        return v

    @property
    def provider(self) -> str:
        """model_name 의 provider 부분 (예: 'ollama')."""
        return self.model_name.partition("/")[0]

    @property
    def model(self) -> str:
        """model_name 의 model 부분 (예: 'qwen3:8b')."""
        return self.model_name.partition("/")[2]


class AgentRunRequest(BaseModel):
    """POST /api/v1/agents/run 요청 본문."""

    agent_config: AgentConfig = Field(..., description="실행할 에이전트 설정")
    user_message: str = Field(..., min_length=1, description="사용자 입력 메시지")
    # 2단계(세션 메모리/체크포인터) 대비 필드 — 현재는 이력에만 기록
    session_id: Optional[str] = Field(
        default=None, description="대화 세션 ID (멀티턴 메모리용, 추후 지원)"
    )


class StreamEventType(str, Enum):
    """SSE 스트림 이벤트 타입.

    운영 콘솔(4단계)의 트레이스 뷰어가 이 타입을 기준으로
    토큰/도구 호출을 시각화하게 되므로 안정적인 계약(contract)으로 유지한다.
    """

    TOKEN = "token"            # 모델이 생성한 텍스트 토큰 조각
    TOOL_START = "tool_start"  # 도구 호출 시작 (도구명 + 입력)
    TOOL_END = "tool_end"      # 도구 호출 종료 (출력)
    DONE = "done"              # 실행 정상 종료 (최종 전체 응답 포함)
    ERROR = "error"            # 실행 중 오류

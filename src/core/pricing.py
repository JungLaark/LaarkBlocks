"""모델별 토큰 단가표 및 비용 계산.

단가는 USD / 1M tokens 기준. configs/model_pricing.json 이 존재하면
기본 단가표를 덮어쓴다(부분 갱신). 가격은 수시로 바뀌므로 코드가 아닌
설정 파일로 관리하는 것이 원칙이고, 아래 기본값은 폴백이다.

로컬 sLLM(ollama/*)은 API 과금이 없으므로 0 으로 계산한다.
(GPU 전력/감가상각 기반의 '자체 서빙 단가'를 넣고 싶다면 설정 파일에서
 ollama/모델명 단가를 지정하면 된다 — 외부 API 대비 절감액 리포트용)
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 모델명 → {"input": 입력단가, "output": 출력단가} (USD per 1M tokens)
_DEFAULT_PRICING: dict[str, dict[str, float]] = {
    # OpenAI
    "openai/gpt-4o": {"input": 2.50, "output": 10.00},
    "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60},
    # Anthropic
    "anthropic/claude-sonnet-5": {"input": 3.00, "output": 15.00},
    "anthropic/claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}

_pricing: dict[str, dict[str, float]] = dict(_DEFAULT_PRICING)


def load_pricing_overrides(path: Path) -> None:
    """설정 파일의 단가로 기본표를 부분 갱신한다. (앱 기동 시 호출)"""
    if not path.is_file():
        return
    try:
        overrides = json.loads(path.read_text("utf-8"))
        _pricing.update(overrides)
        logger.info("모델 단가표 갱신: %d개 항목", len(overrides))
    except (json.JSONDecodeError, OSError):
        logger.exception("단가표 로드 실패 — 기본값 사용: %s", path)


def calculate_cost(
    model_name: str, input_tokens: int | None, output_tokens: int | None
) -> float:
    """모델·토큰 수 기반 비용(USD) 계산.

    조회 우선순위: "provider/model" 정확 일치 → 모델명 단독 일치 → 0
    (Ollama 등 로컬 모델은 표에 없으므로 자연스럽게 0 이 된다)
    """
    price = _pricing.get(model_name)
    if price is None and "/" in model_name:
        price = _pricing.get(model_name.partition("/")[2])
    if price is None:
        price = _pricing.get(f"ollama/{model_name}")  # ls_model_name 폴백
    if price is None:
        return 0.0

    cost = (
        (input_tokens or 0) * price.get("input", 0.0)
        + (output_tokens or 0) * price.get("output", 0.0)
    ) / 1_000_000
    return round(cost, 8)

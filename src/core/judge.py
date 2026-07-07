"""LLM-as-judge — 저장된 실행 이력의 답변 품질을 모델이 평가한다.

평가 흐름:
    트레이스 조회 → 저지 프롬프트 구성 → 평가 모델 호출
    → JSON 파싱(방어적) → evaluations 테이블 적재

저지 모델도 llm_factory 를 통해 생성하므로, 실행 모델과 무관하게
로컬 sLLM / 외부 API 어느 쪽으로도 평가할 수 있다.
(예: 실행은 저렴한 로컬 모델, 평가는 상위 모델로 샘플링 평가)
"""

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from src.core.llm_factory import create_chat_model
from src.schemas.agent import AgentConfig
from src.schemas.tracing import EvaluationIn, TraceData

logger = logging.getLogger(__name__)

_JUDGE_SYSTEM_PROMPT = """당신은 AI 에이전트의 답변 품질을 평가하는 엄격한 평가자입니다.
주어진 [사용자 질문]과 [에이전트 답변]을 평가 기준에 따라 평가하세요.

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트를 추가하지 마세요.
{"score": 0.0에서 1.0 사이의 숫자, "feedback": "한두 문장의 구체적인 평가 근거"}"""

_JUDGE_USER_TEMPLATE = """[평가 기준]: {criteria}

[사용자 질문]
{user_message}

[에이전트 답변]
{final_response}

위 답변을 평가 기준에 따라 평가하고 JSON 으로만 응답하세요."""


def _parse_judge_output(text: str) -> tuple[float, str]:
    """저지 모델 출력에서 score/feedback 을 방어적으로 추출한다.

    모델이 JSON 앞뒤에 사족을 붙이는 경우가 흔하므로,
    본문에서 첫 번째 JSON 객체를 찾아 파싱한다.
    """
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"저지 응답에서 JSON 을 찾을 수 없습니다: {text[:200]}")

    data = json.loads(match.group())
    score = float(data["score"])
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"score 가 0~1 범위를 벗어났습니다: {score}")
    return score, str(data.get("feedback", ""))


async def judge_trace(
    trace: TraceData, judge_model: str, criteria: str
) -> EvaluationIn:
    """트레이스의 최종 답변을 평가하고 EvaluationIn 으로 반환한다.

    저장은 호출자(API 계층)가 리포지토리를 통해 수행한다.
    (평가 로직과 저장을 분리 — 배치 평가 파이프라인에서 재사용 가능)
    """
    # 저지 모델 생성을 위해 llm_factory 계약(AgentConfig)에 맞춘 최소 설정
    judge_config = AgentConfig(
        agent_id="llm-judge",
        name="LLM Judge",
        model_name=judge_model,
        system_prompt="_",  # 사용되지 않음 — 아래에서 메시지로 직접 구성
    )
    model = create_chat_model(judge_config)

    response = await model.ainvoke([
        SystemMessage(content=_JUDGE_SYSTEM_PROMPT),
        HumanMessage(content=_JUDGE_USER_TEMPLATE.format(
            criteria=criteria,
            user_message=trace.user_message,
            final_response=trace.final_response or "(응답 없음)",
        )),
    ])

    content = response.content if isinstance(response.content, str) else str(response.content)
    score, feedback = _parse_judge_output(content)

    return EvaluationIn(
        evaluator=f"judge:{judge_model}",
        criteria=criteria,
        score=score,
        feedback=feedback,
    )

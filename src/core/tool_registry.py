"""도구 레지스트리.

에이전트 설정(AgentConfig.tools)은 도구를 "이름"으로만 참조한다.
실제 구현체는 이 레지스트리에 등록되어 있으며, 엔진이 실행 시점에
이름 → 구현체로 해석(resolve)한다.

2단계에서 MCP(Model Context Protocol) 클라이언트가 추가되면,
MCP 서버가 노출하는 원격 도구들도 동일한 레지스트리 인터페이스로
편입된다. (이름 충돌 방지를 위해 "mcp:서버명:도구명" 네임스페이스 예정)
"""

import ast
import operator
from datetime import datetime, timezone

from langchain_core.tools import BaseTool, tool

# 도구 이름 → LangChain BaseTool 인스턴스
_TOOL_REGISTRY: dict[str, BaseTool] = {}


def register_tool(t: BaseTool) -> BaseTool:
    """도구를 레지스트리에 등록. @tool 데코레이터 결과물을 그대로 받는다."""
    _TOOL_REGISTRY[t.name] = t
    return t


def unregister_tool(name: str) -> None:
    """도구를 레지스트리에서 제거. (MCP 서버 재연결/해제 시 사용)"""
    _TOOL_REGISTRY.pop(name, None)


def available_tools() -> list[dict[str, str]]:
    """등록된 도구 목록 (빌더 스튜디오의 도구 선택 UI 노출용)."""
    return [
        {"name": name, "description": t.description}
        for name, t in sorted(_TOOL_REGISTRY.items())
    ]


def resolve_tools(names: list[str]) -> list[BaseTool]:
    """도구 이름 목록을 구현체 목록으로 해석.

    Raises:
        ValueError: 등록되지 않은 도구 이름이 포함된 경우.
                    (에이전트 실행 전에 설정 오류를 조기에 잡기 위함)
    """
    unknown = [n for n in names if n not in _TOOL_REGISTRY]
    if unknown:
        raise ValueError(
            f"등록되지 않은 도구입니다: {unknown}. "
            f"사용 가능: {[t['name'] for t in available_tools()]}"
        )
    return [_TOOL_REGISTRY[n] for n in names]


# ──────────────────────────────────────────────────────────────────
# 내장 데모 도구
# ──────────────────────────────────────────────────────────────────

@tool
def get_current_time() -> str:
    """현재 날짜와 시각을 UTC 기준 ISO-8601 형식으로 반환한다."""
    return datetime.now(timezone.utc).isoformat()


# eval() 은 임의 코드 실행 위험이 있으므로, AST 를 직접 순회하며
# 사칙연산/거듭제곱만 허용하는 안전한 계산기로 구현한다.
_ALLOWED_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node: ast.AST) -> float:
    """허용된 산술 연산만 재귀적으로 평가. 그 외 노드는 즉시 거부."""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError(f"허용되지 않는 표현식입니다: {ast.dump(node)}")


@tool
def calculator(expression: str) -> str:
    """수식 문자열을 계산한다. 사칙연산, 거듭제곱(**), 나머지(%)만 지원한다.

    Args:
        expression: 계산할 수식 (예: "(3 + 4) * 2 ** 3")
    """
    try:
        result = _safe_eval(ast.parse(expression, mode="eval"))
        return str(result)
    except (ValueError, SyntaxError, ZeroDivisionError) as e:
        # 도구 실패를 예외로 던지지 않고 문자열로 반환 →
        # 모델이 오류를 보고 스스로 수정 시도할 수 있게 한다.
        return f"계산 오류: {e}"


# 모듈 로드 시 내장 도구 자동 등록
register_tool(get_current_time)
register_tool(calculator)

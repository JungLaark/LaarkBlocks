"""테스트/데모용 초소형 MCP 서버 (FastMCP, stdio 전송).

E2E 테스트에서 서브프로세스로 기동되어, LaarkBlocks 의 MCP 연동이
'진짜 MCP 프로토콜'로 동작함을 검증하는 데 사용된다.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo")


@mcp.tool()
def add(a: int, b: int) -> int:
    """두 정수를 더한다."""
    return a + b


@mcp.tool()
def greet(name: str) -> str:
    """이름을 받아 인사말을 반환한다."""
    return f"안녕하세요, {name}님!"


if __name__ == "__main__":
    # stdio 전송: 표준입출력으로 MCP 프로토콜 통신
    mcp.run(transport="stdio")

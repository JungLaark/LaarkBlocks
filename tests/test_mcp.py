"""MCP 연동 E2E 테스트.

Mock 이 아니라 '진짜 MCP 서버'(tests/fixtures/demo_mcp_server.py, FastMCP)를
stdio 서브프로세스로 기동해 프로토콜 전체 경로를 검증한다:

    MCPManager → MultiServerMCPClient → (stdio) → FastMCP 서버
    → 도구 스키마 수신 → 레지스트리 주입 → 에이전트 ReAct 루프에서 실제 호출
"""

import json
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from src.core.engine import engine
from src.core.mcp_manager import MCPManager
from src.core.tool_registry import available_tools
from src.schemas.agent import StreamEventType
from tests.conftest import collect, make_config

FIXTURE_SERVER = Path(__file__).parent / "fixtures" / "demo_mcp_server.py"


@pytest.fixture
async def mcp(tmp_path):
    """demo MCP 서버를 가리키는 설정 파일을 만들고 매니저를 연결/정리한다."""
    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text(
        json.dumps({
            "servers": {
                "demo": {
                    "transport": "stdio",
                    # 현재 테스트를 실행 중인 파이썬 인터프리터로 서버 기동
                    "command": sys.executable,
                    "args": [str(FIXTURE_SERVER)],
                }
            }
        }),
        encoding="utf-8",
    )

    manager = MCPManager()
    summary = await manager.connect(config_path)
    yield manager, summary
    manager.disconnect_all()  # 레지스트리 원상 복구


async def test_mcp_tools_are_injected(mcp):
    """MCP 서버의 도구가 네임스페이스된 이름으로 레지스트리에 주입되어야 한다."""
    manager, summary = mcp
    assert summary == {"demo": 2}  # add, greet

    names = [t["name"] for t in available_tools()]
    assert "mcp__demo__add" in names
    assert "mcp__demo__greet" in names

    # 내장 도구는 그대로 공존해야 한다
    assert "calculator" in names


async def test_agent_calls_mcp_tool_e2e(mcp, use_scripted_model):
    """에이전트가 MCP 도구를 실제로 호출하고 결과를 받는 전체 흐름."""
    use_scripted_model([
        # 모델이 MCP 도구 호출을 요청하는 각본
        AIMessage(
            content="",
            tool_calls=[{
                "name": "mcp__demo__add",
                "args": {"a": 2, "b": 3},
                "id": "call_mcp_1",
                "type": "tool_call",
            }],
        ),
        AIMessage(content="합은 5 입니다."),
    ])

    config = make_config(tools=["mcp__demo__add"])
    events = await collect(engine.astream(config, "2 더하기 3은?"))

    tool_end = next(e for e in events if e["type"] == StreamEventType.TOOL_END)
    assert tool_end["tool"] == "mcp__demo__add"
    # 실제 MCP 서버(서브프로세스)가 계산한 결과
    assert "5" in tool_end["output"]

    done = next(e for e in events if e["type"] == StreamEventType.DONE)
    assert "5" in done["content"]


async def test_disconnect_removes_mcp_tools(mcp):
    """연결 해제 시 MCP 도구만 제거되고 내장 도구는 남아야 한다."""
    manager, _ = mcp
    manager.disconnect_all()

    names = [t["name"] for t in available_tools()]
    assert not any(n.startswith("mcp__") for n in names)
    assert "calculator" in names
    assert manager.status() == {}


async def test_connect_without_config_file(tmp_path):
    """설정 파일이 없으면 조용히 건너뛰어야 한다 (기동 실패 금지)."""
    manager = MCPManager()
    summary = await manager.connect(tmp_path / "no_such_file.json")
    assert summary == {}

"""MCP(Model Context Protocol) 매니저.

2026년 현재 에이전트-도구 연동의 사실상 표준인 MCP 서버들을
LaarkBlocks 도구 레이어에 동적으로 주입한다.

설계 원칙
---------
1. **레지스트리로의 편입**: MCP 도구도 결국 LangChain BaseTool 로 변환되어
   기존 tool_registry 에 등록된다. 엔진(engine.py)은 도구의 출처가
   내장인지 MCP 인지 알 필요가 없다. → 엔진 무수정 확장.

2. **네임스페이스**: 등록 이름은 "mcp__{서버명}__{도구명}" 형식이다.
   - 서버 간 도구 이름 충돌 방지
   - 구분자로 '__' 를 쓰는 이유: OpenAI 등 일부 provider 가 함수 이름에
     ':' 같은 특수문자를 허용하지 않기 때문 (^[a-zA-Z0-9_-]+$ 제약)

3. **설정 파일 기반**: configs/mcp_servers.json 에 서버 목록을 선언하면
   앱 기동 시(lifespan) 자동 연결된다. 에이전트 추가와 마찬가지로
   도구 서버 추가에도 코드 배포가 필요 없다.

설정 파일 형식 (configs/mcp_servers.json)
------------------------------------------
{
  "servers": {
    "fs": {
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/data"]
    },
    "internal-api": {
      "transport": "streamable_http",
      "url": "http://localhost:9000/mcp"
    }
  }
}
"""

import json
import logging
from pathlib import Path

from src.core.tool_registry import register_tool, unregister_tool

logger = logging.getLogger(__name__)

_NAMESPACE_FMT = "mcp__{server}__{tool}"


class MCPManager:
    """MCP 서버 연결과 도구 수명주기(등록/해제)를 관리한다."""

    def __init__(self) -> None:
        # 서버명 → 해당 서버에서 등록한 도구 이름 목록
        self._registered: dict[str, list[str]] = {}

    # ── 조회 ─────────────────────────────────────────────────────

    def status(self) -> dict[str, list[str]]:
        """연결된 서버와 각 서버가 제공한 도구 목록. (운영 콘솔 노출용)"""
        return dict(self._registered)

    # ── 연결/해제 ────────────────────────────────────────────────

    async def connect(self, config_path: Path) -> dict[str, int]:
        """설정 파일의 MCP 서버들에 연결하고 도구를 레지스트리에 주입한다.

        서버 하나의 연결 실패가 전체 기동을 막지 않도록 서버 단위로
        격리하여 처리한다. (운영 안정성)

        Returns:
            {서버명: 등록된 도구 수}
        """
        if not config_path.is_file():
            logger.info("MCP 설정 파일 없음 — MCP 연동 생략 (%s)", config_path)
            return {}

        # lazy import — MCP 미사용 배포에서 의존성을 강제하지 않는다
        from langchain_mcp_adapters.client import MultiServerMCPClient

        raw = json.loads(config_path.read_text("utf-8"))
        servers: dict = raw.get("servers", {})
        if not servers:
            return {}

        client = MultiServerMCPClient(servers)
        summary: dict[str, int] = {}

        for server_name in servers:
            try:
                # get_tools 는 MCP 서버의 도구 스키마를 LangChain BaseTool 로
                # 변환해 반환한다. (도구 호출 시마다 세션을 열고 닫는 무상태 방식
                # 이므로 상시 커넥션 관리가 필요 없다)
                tools = await client.get_tools(server_name=server_name)
            except Exception:
                logger.exception("MCP 서버 연결 실패 — 건너뜀: %s", server_name)
                continue

            names: list[str] = []
            for t in tools:
                namespaced = _NAMESPACE_FMT.format(server=server_name, tool=t.name)
                # model_copy 로 이름만 바꾼 사본을 등록 (원본 불변 유지)
                register_tool(t.model_copy(update={"name": namespaced}))
                names.append(namespaced)

            self._registered[server_name] = names
            summary[server_name] = len(names)
            logger.info("MCP 서버 연결됨: %s (도구 %d개)", server_name, len(names))

        return summary

    def disconnect_all(self) -> None:
        """모든 MCP 도구를 레지스트리에서 제거한다. (재연결 전 정리용)"""
        for names in self._registered.values():
            for name in names:
                unregister_tool(name)
        self._registered.clear()

    async def refresh(self, config_path: Path) -> dict[str, int]:
        """설정 파일 재로드 — 도구 서버 추가/제거를 무중단으로 반영한다."""
        self.disconnect_all()
        return await self.connect(config_path)


# 앱 전역에서 공유하는 매니저 싱글턴
mcp_manager = MCPManager()

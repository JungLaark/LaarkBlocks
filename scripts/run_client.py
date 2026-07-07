"""SSE 수동 테스트 클라이언트.

서버(uvicorn src.main:app)와 Ollama 가 떠 있는 상태에서 실행하면
에이전트 응답이 토큰 단위로 실시간 출력되는 것을 눈으로 확인할 수 있다.

사용법:
    python scripts/run_client.py                          # 기본 프리셋 실행
    python scripts/run_client.py "3 더하기 4 곱하기 2는?"  # 메시지 지정
"""

import json
import sys
from pathlib import Path

import httpx

BASE_URL = "http://localhost:8000"
PRESET = Path(__file__).parent.parent / "configs" / "agents" / "tool-demo.json"


def main() -> None:
    user_message = sys.argv[1] if len(sys.argv) > 1 else "(6 + 4) * 7 을 계산해줘"
    agent_config = json.loads(PRESET.read_text("utf-8"))

    body = {"agent_config": agent_config, "user_message": user_message}
    print(f"▶ agent: {agent_config['name']}  |  message: {user_message}\n")

    # SSE 스트림 소비 — event/data 라인을 짝지어 처리
    with httpx.stream(
        "POST", f"{BASE_URL}/api/v1/agents/run", json=body, timeout=120
    ) as res:
        res.raise_for_status()
        current_event = "message"
        for line in res.iter_lines():
            if line.startswith("event:"):
                current_event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                raw = line.split(":", 1)[1].strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue  # keep-alive ping

                if current_event == "token":
                    # 토큰을 개행 없이 이어서 출력 → 타자기 효과
                    print(data["content"], end="", flush=True)
                elif current_event == "tool_start":
                    print(f"\n🔧 도구 호출: {data['tool']}({data.get('input')})")
                elif current_event == "tool_end":
                    print(f"✅ 도구 결과: {data.get('output')}\n")
                elif current_event == "done":
                    print("\n\n── 완료 ──")
                elif current_event == "error":
                    print(f"\n❌ 오류: {data.get('message')}")


if __name__ == "__main__":
    main()

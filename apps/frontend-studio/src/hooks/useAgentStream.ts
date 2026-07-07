/** useAgentStream — POST SSE 스트리밍 수신의 핵심 훅.
 *
 * 브라우저 내장 EventSource 는 GET 전용이라 에이전트 설정(JSON body)을
 * 보낼 수 없다. 따라서 fetch + ReadableStream 으로 응답 바디를 직접 읽고
 * SSE 와이어 포맷(event:/data:/빈 줄)을 파싱한다.
 *
 * 안정성 포인트:
 * 1. 청크 경계 안전 — 네트워크 청크가 SSE 이벤트 중간에서 잘려도
 *    버퍼에 누적 후 '빈 줄' 단위로만 파싱하므로 이벤트가 깨지지 않는다.
 * 2. 중단 가능 — AbortController 로 사용자가 생성을 중단할 수 있고,
 *    백엔드는 연결 종료를 감지해 모델 호출을 멈춘다(서버 자원 보호).
 * 3. keep-alive 무해화 — sse-starlette 의 ping(주석 라인 ':')을 무시한다.
 */

import { useCallback, useRef, useState } from "react";
import { API_BASE } from "../api/client";
import type { AgentConfig, StreamEvent } from "../api/types";

export interface ToolActivity {
  tool: string;
  input?: unknown;
  output?: string;
  running: boolean;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  tools: ToolActivity[]; // 이 답변 중 발생한 도구 호출들
  error?: string;
}

export type StreamStatus = "idle" | "streaming" | "error";

/** SSE 와이어 포맷 파서 — 완성된 이벤트 블록(빈 줄 구분)만 콜백으로 전달 */
function createSseParser(onEvent: (name: string, data: string) => void) {
  let buffer = "";
  return (chunk: string) => {
    buffer += chunk;
    // 이벤트 구분자(빈 줄, \r\n 변형 포함)로 분리 — 마지막 조각은 미완성이므로 버퍼에 유지
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? "";

    for (const block of blocks) {
      let name = "message";
      const dataLines: string[] = [];
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith(":")) continue; // keep-alive ping 주석
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length) onEvent(name, dataLines.join("\n"));
    }
  };
}

export function useAgentStream() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState<StreamStatus>("idle");
  const abortRef = useRef<AbortController | null>(null);

  /** 마지막 assistant 메시지를 불변성 유지하며 갱신하는 헬퍼 */
  const patchLast = useCallback(
    (patch: (msg: ChatMessage) => ChatMessage) => {
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last?.role === "assistant") next[next.length - 1] = patch(last);
        return next;
      });
    },
    [],
  );

  const send = useCallback(
    async (config: AgentConfig, userMessage: string, sessionId?: string) => {
      // 사용자 메시지 + 빈 assistant 자리(스트리밍 대상)를 먼저 그린다
      setMessages((prev) => [
        ...prev,
        { role: "user", content: userMessage, tools: [] },
        { role: "assistant", content: "", tools: [] },
      ]);
      setStatus("streaming");

      const controller = new AbortController();
      abortRef.current = controller;

      const dispatch = (name: string, data: string) => {
        let event: StreamEvent;
        try {
          event = { type: name, ...JSON.parse(data) } as StreamEvent;
        } catch {
          return; // 파싱 불가 페이로드(ping 등)는 무시
        }

        switch (event.type) {
          case "token":
            // 타이핑 렌더의 본체 — 토큰을 마지막 답변에 이어 붙인다
            patchLast((m) => ({ ...m, content: m.content + event.content }));
            break;
          case "tool_start":
            patchLast((m) => ({
              ...m,
              tools: [...m.tools, { tool: event.tool, input: event.input, running: true }],
            }));
            break;
          case "tool_end":
            patchLast((m) => ({
              ...m,
              tools: m.tools.map((t, i) =>
                // 같은 도구의 마지막 실행 중 항목을 완료 처리
                i === m.tools.findLastIndex((x) => x.tool === event.tool && x.running)
                  ? { ...t, output: event.output, running: false }
                  : t,
              ),
            }));
            break;
          case "done":
            // 토큰 누락 대비: 서버가 확정한 전체 응답으로 최종 동기화
            patchLast((m) => ({ ...m, content: event.content || m.content }));
            setStatus("idle");
            break;
          case "error":
            patchLast((m) => ({ ...m, error: event.message }));
            setStatus("error");
            break;
        }
      };

      const parse = createSseParser(dispatch);

      try {
        const res = await fetch(`${API_BASE}/agents/run`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
          body: JSON.stringify({
            agent_config: config,
            user_message: userMessage,
            session_id: sessionId ?? null,
          }),
          signal: controller.signal,
        });
        if (!res.ok || !res.body) {
          throw new Error(`서버 오류: ${res.status} ${res.statusText}`);
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          // stream: true — 멀티바이트(한글) 문자가 청크 경계에서 잘려도 안전
          parse(decoder.decode(value, { stream: true }));
        }
        // 정상 종료면 done 이벤트가 이미 상태를 idle 로 돌려놓았다
        setStatus((s) => (s === "streaming" ? "idle" : s));
      } catch (e) {
        if ((e as Error).name === "AbortError") {
          // 사용자 중단 — 지금까지 받은 내용은 유지
          setStatus("idle");
        } else {
          patchLast((m) => ({ ...m, error: (e as Error).message }));
          setStatus("error");
        }
      } finally {
        abortRef.current = null;
      }
    },
    [patchLast],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);
  const clear = useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
    setStatus("idle");
  }, []);

  return { messages, status, send, stop, clear };
}

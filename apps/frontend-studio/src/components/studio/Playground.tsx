/** 플레이그라운드 — useAgentStream 으로 실시간 타이핑 렌더링.
 *  세션 유지 토글을 켜면 session_id 를 실어 보내 멀티턴 메모리를 테스트한다. */

import { useEffect, useRef, useState } from "react";
import type { AgentConfig } from "../../api/types";
import { useAgentStream, type ChatMessage } from "../../hooks/useAgentStream";

function ToolChip({ tool, running, output }: { tool: string; running: boolean; output?: string }) {
  return (
    <div
      className="my-1 rounded-lg border px-2.5 py-1.5 text-xs"
      style={{ borderColor: "var(--series-tool)", color: "var(--text-secondary)" }}
      title={output}
    >
      <span style={{ color: "var(--series-tool)" }}>
        {running ? "⚙ 실행 중" : "✓ 완료"}
      </span>{" "}
      <code>{tool}</code>
      {output && (
        <span className="ml-1 text-[var(--text-muted)]">
          → {output.length > 80 ? `${output.slice(0, 80)}…` : output}
        </span>
      )}
    </div>
  );
}

function Bubble({ msg, streaming }: { msg: ChatMessage; streaming: boolean }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[78%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap ${
          isUser
            ? "bg-[var(--accent)] text-white"
            : "bg-[var(--surface-2)] text-[var(--text-primary)]"
        }`}
      >
        {msg.tools.map((t, i) => (
          <ToolChip key={i} {...t} />
        ))}
        <span className={streaming ? "streaming-cursor" : ""}>{msg.content}</span>
        {msg.error && (
          <p className="mt-2 text-xs" style={{ color: "var(--status-critical)" }}>
            ⚠ {msg.error}
          </p>
        )}
      </div>
    </div>
  );
}

export default function Playground({ config }: { config: AgentConfig }) {
  const { messages, status, send, stop, clear } = useAgentStream();
  const [draft, setDraft] = useState("");
  const [keepSession, setKeepSession] = useState(false);
  const [sessionId] = useState(() => `pg-${Math.random().toString(36).slice(2, 10)}`);
  const bottomRef = useRef<HTMLDivElement>(null);

  // 새 토큰이 올 때마다 스크롤을 바닥에 고정
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const submit = () => {
    const text = draft.trim();
    if (!text || status === "streaming") return;
    setDraft("");
    void send(config, text, keepSession ? sessionId : undefined);
  };

  return (
    <div className="flex h-full flex-col">
      {/* 헤더 */}
      <header className="flex items-center justify-between border-b border-[var(--border-1)] px-5 py-3">
        <div>
          <h2 className="text-sm font-semibold">플레이그라운드</h2>
          <p className="text-xs text-[var(--text-muted)]">
            {config.name} · <code>{config.model_name}</code>
            {config.tools.length > 0 && ` · 도구 ${config.tools.length}개`}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-[var(--text-secondary)]">
            <input
              type="checkbox"
              className="accent-[var(--accent)]"
              checked={keepSession}
              onChange={(e) => setKeepSession(e.target.checked)}
            />
            세션 유지 (멀티턴)
          </label>
          <button
            onClick={clear}
            className="rounded-lg border border-[var(--border-1)] px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:bg-[var(--surface-2)]"
          >
            초기화
          </button>
        </div>
      </header>

      {/* 대화 영역 */}
      <div className="flex-1 space-y-4 overflow-y-auto p-5">
        {messages.length === 0 && (
          <div className="flex h-full items-center justify-center text-center">
            <div>
              <p className="text-2xl">🧱</p>
              <p className="mt-2 text-sm text-[var(--text-muted)]">
                왼쪽에서 에이전트를 조립하고 바로 테스트해보세요.
                <br />
                저장하지 않은 설정도 즉시 실행됩니다.
              </p>
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <Bubble
            key={i}
            msg={m}
            streaming={status === "streaming" && i === messages.length - 1}
          />
        ))}
        <div ref={bottomRef} />
      </div>

      {/* 입력 영역 */}
      <footer className="border-t border-[var(--border-1)] p-4">
        <div className="flex gap-2">
          <input
            className="flex-1 rounded-xl border border-[var(--border-1)] bg-[var(--surface-2)] px-4 py-2.5 text-sm outline-none focus:border-[var(--accent)]"
            placeholder="메시지를 입력하세요… (Enter 전송)"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && submit()}
          />
          {status === "streaming" ? (
            <button
              onClick={stop}
              className="rounded-xl px-4 py-2.5 text-sm font-medium text-white"
              style={{ background: "var(--status-critical)" }}
            >
              중단
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={!draft.trim()}
              className="rounded-xl bg-[var(--accent)] px-4 py-2.5 text-sm font-medium text-white disabled:opacity-40"
            >
              전송
            </button>
          )}
        </div>
      </footer>
    </div>
  );
}

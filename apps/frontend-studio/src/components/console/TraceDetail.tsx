/** 트레이스 상세 — 스팬 타임라인(간트 스타일) 뷰어.
 *
 *  각 스팬을 트레이스 시작 시각 기준 상대 오프셋으로 배치해
 *  "어느 단계(모델/도구)에서 시간이 얼마나 걸렸는지"를 보여준다.
 *  카테고리 2개(llm/tool)는 blue/aqua 로 구분하고 범례 + 행 라벨(직접 라벨)을
 *  병기한다. 워커 스팬은 같은 색의 외곽선 변형으로 표시(색상 낭비 방지).   */

import type { SpanData, TraceData } from "../../api/types";

const SPAN_COLOR: Record<SpanData["span_type"], string> = {
  llm: "var(--series-llm)",
  tool: "var(--series-tool)",
};

function SpanRow({ span, traceStart, traceMs }: { span: SpanData; traceStart: number; traceMs: number }) {
  const start = new Date(span.started_at).getTime();
  const offsetPct = Math.min(((start - traceStart) / traceMs) * 100, 98);
  const widthPct = Math.max(((span.latency_ms ?? 0) / traceMs) * 100, 1.5);
  const color = SPAN_COLOR[span.span_type];

  return (
    <div className="group flex items-center gap-2 py-1">
      {/* 직접 라벨 — 색이 아니라 텍스트가 정체를 보증한다 */}
      <div className="w-52 shrink-0 truncate text-xs">
        <span style={{ color }}>{span.span_type === "llm" ? "◆" : "●"}</span>{" "}
        <code className="text-[var(--text-secondary)]">{span.name}</code>
        {span.is_worker && (
          <span className="ml-1 rounded border border-[var(--border-1)] px-1 text-[10px] text-[var(--text-muted)]">
            worker
          </span>
        )}
      </div>

      {/* 타임라인 트랙 */}
      <div className="relative h-5 flex-1 rounded bg-[var(--surface-2)]">
        <div
          className="absolute top-0.5 h-4 rounded-[4px]"
          style={{
            left: `${offsetPct}%`,
            width: `${widthPct}%`,
            background: span.is_worker ? "transparent" : color,
            border: span.is_worker ? `2px solid ${color}` : "none",
          }}
          title={`${span.name}\n지연: ${span.latency_ms}ms${
            span.input_tokens != null
              ? `\n토큰: ${span.input_tokens}→${span.output_tokens}`
              : ""
          }\n입력: ${span.input.slice(0, 200)}\n출력: ${span.output.slice(0, 200)}`}
        />
      </div>

      <span className="w-16 shrink-0 text-right text-xs tabular-nums text-[var(--text-muted)]">
        {span.latency_ms}ms
      </span>
    </div>
  );
}

export default function TraceDetail({
  trace,
  onClose,
}: {
  trace: TraceData;
  onClose: () => void;
}) {
  const traceStart = new Date(trace.started_at).getTime();
  const traceMs = Math.max(trace.latency_ms, 1);

  return (
    <div className="rounded-xl border border-[var(--border-1)] bg-[var(--surface-1)] p-4">
      <div className="mb-3 flex items-start justify-between">
        <div>
          <h3 className="text-sm font-semibold">
            트레이스 <code className="text-[var(--text-muted)]">{trace.trace_id.slice(0, 12)}…</code>
          </h3>
          <p className="mt-0.5 text-xs text-[var(--text-muted)]">
            {trace.agent_id} · {trace.model_name} · 총 {trace.latency_ms}ms · $
            {trace.cost_usd.toFixed(6)}
            {trace.session_id && ` · 세션 ${trace.session_id}`}
          </p>
        </div>
        <button
          onClick={onClose}
          className="rounded-lg border border-[var(--border-1)] px-2.5 py-1 text-xs text-[var(--text-muted)] hover:bg-[var(--surface-2)]"
        >
          닫기 ✕
        </button>
      </div>

      {/* 범례 — 2개 카테고리 */}
      <div className="mb-2 flex gap-4 text-[11px] text-[var(--text-secondary)]">
        <span>
          <span style={{ color: "var(--series-llm)" }}>◆</span> 모델 호출
        </span>
        <span>
          <span style={{ color: "var(--series-tool)" }}>●</span> 도구 실행
        </span>
        <span className="text-[var(--text-muted)]">외곽선 = 워커 내부 실행</span>
      </div>

      {/* 스팬 타임라인 */}
      <div className="rounded-lg border border-[var(--border-1)] p-3">
        {trace.spans.map((s) => (
          <SpanRow key={s.span_id} span={s} traceStart={traceStart} traceMs={traceMs} />
        ))}
        {trace.spans.length === 0 && (
          <p className="py-4 text-center text-xs text-[var(--text-muted)]">스팬이 없습니다</p>
        )}
      </div>

      {/* 질문/응답 원문 */}
      <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div className="rounded-lg bg-[var(--surface-2)] p-3">
          <p className="mb-1 text-[11px] font-medium text-[var(--text-muted)]">사용자 질문</p>
          <p className="text-xs whitespace-pre-wrap text-[var(--text-secondary)]">
            {trace.user_message}
          </p>
        </div>
        <div className="rounded-lg bg-[var(--surface-2)] p-3">
          <p className="mb-1 text-[11px] font-medium text-[var(--text-muted)]">
            최종 응답 {trace.error && <span style={{ color: "var(--status-critical)" }}>· 에러</span>}
          </p>
          <p className="text-xs whitespace-pre-wrap text-[var(--text-secondary)]">
            {trace.error ?? trace.final_response ?? "(없음)"}
          </p>
        </div>
      </div>
    </div>
  );
}

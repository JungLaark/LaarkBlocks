/** 실행 이력 테이블 — 행 클릭 시 트레이스 상세(스팬 타임라인)로. */

import type { TraceStatus, TraceSummary } from "../../api/types";

const statusStyle: Record<TraceStatus, { label: string; color: string }> = {
  success: { label: "✓ 성공", color: "var(--status-good)" },
  error: { label: "✕ 에러", color: "var(--status-critical)" },
  aborted: { label: "◼ 중단", color: "var(--status-warning)" },
};

export default function TraceTable({
  traces,
  onSelect,
  selectedId,
}: {
  traces: TraceSummary[];
  onSelect: (traceId: string) => void;
  selectedId?: string;
}) {
  return (
    <div className="rounded-xl border border-[var(--border-1)] bg-[var(--surface-1)] p-4">
      <h3 className="text-sm font-semibold">실행 이력</h3>
      <p className="mb-3 text-[11px] text-[var(--text-muted)]">
        행을 클릭하면 스팬 타임라인이 열립니다
      </p>

      <div className="max-h-80 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-[var(--surface-1)] text-left text-[var(--text-muted)]">
            <tr>
              <th className="py-1.5 pr-2 font-medium">시각</th>
              <th className="py-1.5 pr-2 font-medium">에이전트</th>
              <th className="py-1.5 pr-2 font-medium">질문</th>
              <th className="py-1.5 pr-2 font-medium">상태</th>
              <th className="py-1.5 pr-2 text-right font-medium">지연</th>
              <th className="py-1.5 text-right font-medium">토큰</th>
            </tr>
          </thead>
          <tbody>
            {traces.length === 0 && (
              <tr>
                <td colSpan={6} className="py-8 text-center text-[var(--text-muted)]">
                  아직 실행 이력이 없습니다
                </td>
              </tr>
            )}
            {traces.map((t) => {
              const st = statusStyle[t.status];
              return (
                <tr
                  key={t.trace_id}
                  onClick={() => onSelect(t.trace_id)}
                  className={`cursor-pointer border-t border-[var(--border-1)] hover:bg-[var(--surface-2)] ${
                    selectedId === t.trace_id ? "bg-[var(--surface-2)]" : ""
                  }`}
                >
                  <td className="py-2 pr-2 whitespace-nowrap text-[var(--text-muted)]">
                    {new Date(t.started_at).toLocaleTimeString("ko-KR")}
                  </td>
                  <td className="py-2 pr-2">
                    <code>{t.agent_id}</code>
                  </td>
                  <td className="max-w-40 truncate py-2 pr-2 text-[var(--text-secondary)]">
                    {t.user_message}
                  </td>
                  <td className="py-2 pr-2 whitespace-nowrap" style={{ color: st.color }}>
                    {st.label}
                  </td>
                  <td className="py-2 pr-2 text-right tabular-nums text-[var(--text-secondary)]">
                    {t.latency_ms}ms
                  </td>
                  <td className="py-2 text-right tabular-nums text-[var(--text-secondary)]">
                    {t.input_tokens + t.output_tokens}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

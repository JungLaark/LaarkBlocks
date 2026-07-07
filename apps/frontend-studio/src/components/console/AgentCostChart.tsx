/** 에이전트별 사용량 가로 막대 — 단일 측정값(토큰)이므로 단일 청색 계열.
 *  값은 막대 옆 직접 라벨(모든 막대에 값 표기 대신 막대가 곧 크기,
 *  라벨은 행 단위라 과밀하지 않다). 시리즈가 1개이므로 범례는 없다. */

import type { AgentUsageStats } from "../../api/types";

const fmt = new Intl.NumberFormat("ko-KR");

export default function AgentCostChart({ rows }: { rows: AgentUsageStats[] }) {
  const top = rows.slice(0, 8); // 상위 8개 — 그 이하는 목록 성격
  const max = Math.max(...top.map((r) => r.total_input_tokens + r.total_output_tokens), 1);

  return (
    <div className="rounded-xl border border-[var(--border-1)] bg-[var(--surface-1)] p-4">
      <h3 className="text-sm font-semibold">에이전트별 토큰 사용량</h3>
      <p className="mb-4 text-[11px] text-[var(--text-muted)]">
        비용 내림차순 상위 {top.length}개
      </p>

      {top.length === 0 && (
        <p className="py-8 text-center text-xs text-[var(--text-muted)]">
          아직 실행 이력이 없습니다 — 플레이그라운드에서 에이전트를 실행해보세요
        </p>
      )}

      <div className="space-y-2.5">
        {top.map((r) => {
          const tokens = r.total_input_tokens + r.total_output_tokens;
          const pct = Math.max((tokens / max) * 100, 2);
          return (
            <div key={r.agent_id} className="flex items-center gap-3">
              <span className="w-28 shrink-0 truncate text-xs text-[var(--text-secondary)]">
                {r.agent_id}
              </span>
              <div className="h-4 flex-1 overflow-hidden rounded-r-[4px]">
                <div
                  className="h-full rounded-r-[4px]"
                  style={{ width: `${pct}%`, background: "var(--seq-450)" }}
                  title={`${r.agent_id}: ${fmt.format(tokens)} tokens · $${r.total_cost_usd.toFixed(4)} · 에러율 ${(r.error_rate * 100).toFixed(1)}%`}
                />
              </div>
              <span className="w-24 shrink-0 text-right text-xs tabular-nums text-[var(--text-secondary)]">
                {fmt.format(tokens)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

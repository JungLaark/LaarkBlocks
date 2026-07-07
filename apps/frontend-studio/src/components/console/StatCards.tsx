/** 헤드라인 지표 스탯 타일 4개 — 실행 수, 토큰, 비용, 에러율.
 *  숫자는 텍스트 토큰 색으로(시리즈 색 금지), 상태만 status 색을 쓴다. */

import type { UsageStats } from "../../api/types";

const fmt = new Intl.NumberFormat("ko-KR");

function Tile({
  label,
  value,
  sub,
  valueColor,
}: {
  label: string;
  value: string;
  sub?: string;
  valueColor?: string;
}) {
  return (
    <div className="rounded-xl border border-[var(--border-1)] bg-[var(--surface-1)] p-4">
      <p className="text-xs text-[var(--text-muted)]">{label}</p>
      <p
        className="mt-1.5 text-2xl font-bold tabular-nums"
        style={valueColor ? { color: valueColor } : undefined}
      >
        {value}
      </p>
      {sub && <p className="mt-0.5 text-[11px] text-[var(--text-muted)]">{sub}</p>}
    </div>
  );
}

export default function StatCards({ stats }: { stats: UsageStats | null }) {
  if (!stats) {
    return (
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {[...Array(4)].map((_, i) => (
          <div
            key={i}
            className="h-24 animate-pulse rounded-xl border border-[var(--border-1)] bg-[var(--surface-1)]"
          />
        ))}
      </div>
    );
  }

  const errorRate = (stats.error_rate * 100).toFixed(1);
  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <Tile
        label="총 실행"
        value={fmt.format(stats.total_traces)}
        sub={`평균 지연 ${fmt.format(Math.round(stats.avg_latency_ms))}ms`}
      />
      <Tile
        label="토큰 사용량"
        value={fmt.format(stats.total_input_tokens + stats.total_output_tokens)}
        sub={`입력 ${fmt.format(stats.total_input_tokens)} · 출력 ${fmt.format(stats.total_output_tokens)}`}
      />
      <Tile
        label="API 비용 (USD)"
        value={`$${stats.total_cost_usd.toFixed(4)}`}
        sub="로컬 sLLM 실행분은 $0 — 절감액의 근거"
      />
      <Tile
        label="에러율"
        value={`${errorRate}%`}
        sub={`에러 ${fmt.format(stats.error_traces)}건`}
        valueColor={
          stats.error_rate > 0.05 ? "var(--status-critical)" : "var(--status-good)"
        }
      />
    </div>
  );
}

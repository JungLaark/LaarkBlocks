/** LLMOps 운영 콘솔 — 사용량 집계 + 실행 이력 + 트레이스 뷰어.
 *  데이터 소스: /stats/usage, /stats/usage/by-agent, /traces, /traces/{id} */

import { useCallback, useEffect, useState } from "react";
import { getTrace, getUsageByAgent, getUsageStats, listTraces } from "../api/laark";
import type { AgentUsageStats, TraceData, TraceSummary, UsageStats } from "../api/types";
import AgentCostChart from "../components/console/AgentCostChart";
import StatCards from "../components/console/StatCards";
import TraceDetail from "../components/console/TraceDetail";
import TraceTable from "../components/console/TraceTable";

export default function ConsolePage() {
  const [stats, setStats] = useState<UsageStats | null>(null);
  const [byAgent, setByAgent] = useState<AgentUsageStats[]>([]);
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [selected, setSelected] = useState<TraceData | null>(null);
  const [backendDown, setBackendDown] = useState(false);

  const refresh = useCallback(() => {
    Promise.all([getUsageStats(), getUsageByAgent(), listTraces()])
      .then(([s, a, t]) => {
        setStats(s);
        setByAgent(a);
        setTraces(t);
        setBackendDown(false);
      })
      .catch(() => setBackendDown(true));
  }, []);

  useEffect(() => {
    refresh();
    // 운영 화면은 15초 주기 폴링 (SSE 구독은 고도화 항목)
    const timer = setInterval(refresh, 15_000);
    return () => clearInterval(timer);
  }, [refresh]);

  const openTrace = (traceId: string) =>
    getTrace(traceId).then(setSelected).catch(() => setSelected(null));

  return (
    <div className="p-6">
      <header className="mb-5 flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold">운영 콘솔</h2>
          <p className="text-xs text-[var(--text-muted)]">
            토큰 사용량 · 비용 · 실행 트레이스 (15초 자동 갱신)
          </p>
        </div>
        <button
          onClick={refresh}
          className="rounded-lg border border-[var(--border-1)] px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:bg-[var(--surface-2)]"
        >
          ↻ 새로고침
        </button>
      </header>

      {backendDown && (
        <div className="mb-4 rounded-lg border border-[var(--status-warning)]/40 bg-[var(--status-warning)]/10 p-3 text-xs text-[var(--text-secondary)]">
          ⚠ 백엔드(:8000)에 연결할 수 없습니다. 서버 기동 후 자동으로 다시 연결됩니다.
        </div>
      )}

      <StatCards stats={stats} />

      <div className="mt-5 grid grid-cols-1 gap-5 xl:grid-cols-2">
        <AgentCostChart rows={byAgent} />
        <TraceTable traces={traces} onSelect={openTrace} selectedId={selected?.trace_id} />
      </div>

      {selected && (
        <div className="mt-5">
          <TraceDetail trace={selected} onClose={() => setSelected(null)} />
        </div>
      )}
    </div>
  );
}

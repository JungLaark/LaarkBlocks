/** 도메인별 API 함수 — 화면은 이 함수들만 호출한다. */

import { api } from "./client";
import type {
  AgentConfig,
  AgentUsageStats,
  KnowledgeBaseStatus,
  ToolInfo,
  TraceData,
  TraceSummary,
  UsageStats,
} from "./types";

// ── 플랫폼 메타 (빌더 스튜디오 폼의 데이터 소스) ──────────────
export const getTools = () => api<ToolInfo[]>("/tools");
export const getProviders = () => api<string[]>("/providers");
export const getPresets = () => api<AgentConfig[]>("/agents/presets");

// ── 지식베이스 ────────────────────────────────────────────────
export const getKnowledgeBases = () =>
  api<Record<string, KnowledgeBaseStatus>>("/knowledge");

// ── 운영 콘솔 (트레이싱/집계) ─────────────────────────────────
export const listTraces = (params?: { agent_id?: string; status?: string }) => {
  const qs = new URLSearchParams(
    Object.entries(params ?? {}).filter(([, v]) => v) as [string, string][],
  ).toString();
  return api<TraceSummary[]>(`/traces${qs ? `?${qs}` : ""}`);
};
export const getTrace = (traceId: string) => api<TraceData>(`/traces/${traceId}`);
export const getUsageStats = () => api<UsageStats>("/stats/usage");
export const getUsageByAgent = () => api<AgentUsageStats[]>("/stats/usage/by-agent");

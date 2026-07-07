/** 백엔드 Pydantic 스키마와 1:1 대응하는 타입 정의.
 *  (계약 변경 시 이 파일만 갱신하면 컴파일러가 영향 범위를 알려준다) */

// ── 에이전트 ──────────────────────────────────────────────────
export interface ModelParams {
  temperature: number;
  max_tokens?: number | null;
}

export interface AgentConfig {
  agent_id: string;
  name: string;
  description?: string;
  model_name: string; // "provider/model" (예: "ollama/qwen3:8b")
  system_prompt: string;
  tools: string[];
  model_params?: ModelParams;
  workers?: AgentConfig[];
}

export interface ToolInfo {
  name: string;
  description: string;
}

// ── SSE 스트림 이벤트 (백엔드 StreamEventType 계약) ───────────
export type StreamEvent =
  | { type: "token"; content: string }
  | { type: "tool_start"; tool: string; input: unknown }
  | { type: "tool_end"; tool: string; output: string }
  | { type: "done"; agent_id: string; session_id: string | null; content: string }
  | { type: "error"; message: string };

// ── 운영 콘솔 (트레이싱) ──────────────────────────────────────
export type TraceStatus = "success" | "error" | "aborted";

export interface SpanData {
  span_id: string;
  span_type: "llm" | "tool";
  name: string;
  input: string;
  output: string;
  is_worker: boolean;
  started_at: string;
  ended_at: string | null;
  latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number;
}

export interface TraceSummary {
  trace_id: string;
  agent_id: string;
  session_id: string | null;
  model_name: string;
  user_message: string;
  status: TraceStatus;
  started_at: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

export interface TraceData extends TraceSummary {
  final_response: string;
  error: string | null;
  ended_at: string;
  spans: SpanData[];
}

export interface UsageStats {
  total_traces: number;
  error_traces: number;
  error_rate: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number;
  avg_latency_ms: number;
}

export interface AgentUsageStats extends UsageStats {
  agent_id: string;
}

// ── 지식베이스 ────────────────────────────────────────────────
export interface KnowledgeBaseStatus {
  documents: number;
  chunks: number;
  embedding_model: string;
  tool: string;
}

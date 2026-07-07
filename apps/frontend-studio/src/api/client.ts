/** 공용 API 클라이언트 — fetch 래퍼.
 *  모든 REST 호출이 이 관문을 지나므로 오류 규격화/인증 헤더 추가 등을
 *  한 곳에서 처리할 수 있다. (SSE 스트리밍은 useAgentStream 이 직접 fetch) */

export const API_BASE = "/api/v1";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    // FastAPI 오류 응답({"detail": ...})을 규격화된 예외로 변환
    const body = await res.json().catch(() => null);
    throw new ApiError(res.status, body?.detail ?? res.statusText);
  }
  return res.json() as Promise<T>;
}

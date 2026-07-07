/** 앱 셸 — 좌측 네비게이션 + 라우팅.
 *  /studio  : 에이전트 빌더 스튜디오 & 플레이그라운드
 *  /console : LLMOps 관제 대시보드                     */

import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import ConsolePage from "./pages/ConsolePage";
import StudioPage from "./pages/StudioPage";

const navItem = ({ isActive }: { isActive: boolean }) =>
  `block rounded-lg px-4 py-2.5 text-sm font-medium transition-colors ${
    isActive
      ? "bg-[var(--surface-2)] text-[var(--text-primary)]"
      : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
  }`;

export default function App() {
  return (
    <div className="flex h-screen">
      {/* ── 사이드바 ── */}
      <aside className="flex w-56 shrink-0 flex-col border-r border-[var(--border-1)] bg-[var(--surface-1)] p-4">
        <div className="mb-8 px-2">
          <h1 className="text-lg font-bold tracking-tight">
            <span style={{ color: "var(--accent)" }}>Laark</span>Blocks
          </h1>
          <p className="mt-0.5 text-xs text-[var(--text-muted)]">
            AI Agent Platform
          </p>
        </div>
        <nav className="space-y-1">
          <NavLink to="/studio" className={navItem}>
            빌더 스튜디오
          </NavLink>
          <NavLink to="/console" className={navItem}>
            운영 콘솔
          </NavLink>
        </nav>
        <div className="mt-auto px-2 text-[11px] text-[var(--text-muted)]">
          v0.1.0 · 설정이 곧 에이전트다
        </div>
      </aside>

      {/* ── 메인 ── */}
      <main className="min-w-0 flex-1 overflow-auto">
        <Routes>
          <Route path="/" element={<Navigate to="/studio" replace />} />
          <Route path="/studio" element={<StudioPage />} />
          <Route path="/console" element={<ConsolePage />} />
        </Routes>
      </main>
    </div>
  );
}

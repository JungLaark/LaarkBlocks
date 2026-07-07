/** 빌더 스튜디오 — 좌: 에이전트 설정 조립 / 우: 플레이그라운드.
 *
 *  "에이전트는 설정이다" 철학의 UI 구현: 왼쪽 폼의 상태가 곧
 *  AgentConfig(JSON)이고, 저장하지 않아도 오른쪽에서 즉시 테스트된다.
 */

import { useEffect, useState } from "react";
import { getPresets, getProviders, getTools } from "../api/laark";
import type { AgentConfig, ToolInfo } from "../api/types";
import AgentConfigPanel from "../components/studio/AgentConfigPanel";
import Playground from "../components/studio/Playground";

const DEFAULT_CONFIG: AgentConfig = {
  agent_id: "my-agent",
  name: "새 에이전트",
  description: "",
  model_name: "ollama/qwen3:8b",
  system_prompt: "당신은 친절하고 정확한 AI 어시스턴트입니다.",
  tools: [],
  model_params: { temperature: 0.7 },
};

export default function StudioPage() {
  const [config, setConfig] = useState<AgentConfig>(DEFAULT_CONFIG);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [providers, setProviders] = useState<string[]>([]);
  const [presets, setPresets] = useState<AgentConfig[]>([]);
  const [backendDown, setBackendDown] = useState(false);

  useEffect(() => {
    // 폼 데이터 소스(도구/프로바이더/프리셋)를 병렬 로드.
    // 백엔드 미기동 시에도 화면은 뜨되 배너로 안내한다.
    Promise.all([getTools(), getProviders(), getPresets()])
      .then(([t, p, pr]) => {
        setTools(t);
        setProviders(p);
        setPresets(pr);
      })
      .catch(() => setBackendDown(true));
  }, []);

  return (
    <div className="flex h-full">
      <section className="w-[380px] shrink-0 overflow-y-auto border-r border-[var(--border-1)] bg-[var(--surface-1)] p-5">
        <h2 className="mb-1 text-base font-semibold">에이전트 빌더</h2>
        <p className="mb-5 text-xs text-[var(--text-muted)]">
          설정(JSON)을 조립하면 코드 배포 없이 에이전트가 만들어집니다
        </p>
        {backendDown && (
          <div className="mb-4 rounded-lg border border-[var(--status-warning)]/40 bg-[var(--status-warning)]/10 p-3 text-xs text-[var(--text-secondary)]">
            ⚠ 백엔드(:8000)에 연결할 수 없습니다.{" "}
            <code className="text-[var(--text-primary)]">uvicorn src.main:app</code>{" "}
            실행 후 새로고침하세요.
          </div>
        )}
        <AgentConfigPanel
          config={config}
          onChange={setConfig}
          tools={tools}
          providers={providers}
          presets={presets}
        />
      </section>

      <section className="min-w-0 flex-1">
        <Playground config={config} />
      </section>
    </div>
  );
}

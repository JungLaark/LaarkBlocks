/** 에이전트 설정 조립 폼 — AgentConfig 의 각 필드를 편집한다.
 *  도구 목록은 백엔드 레지스트리에서 오므로 내장/MCP(mcp__*)/지식베이스(kb__*)
 *  도구가 자동으로 같은 체크박스 UI 에 나타난다. */

import type { AgentConfig, ToolInfo } from "../../api/types";

interface Props {
  config: AgentConfig;
  onChange: (config: AgentConfig) => void;
  tools: ToolInfo[];
  providers: string[];
  presets: AgentConfig[];
}

const label = "mb-1.5 block text-xs font-medium text-[var(--text-secondary)]";
const input =
  "w-full rounded-lg border border-[var(--border-1)] bg-[var(--surface-2)] px-3 py-2 text-sm outline-none focus:border-[var(--accent)]";

/** 도구 이름의 출처 배지 — 어디서 온 도구인지 한눈에 */
function toolOrigin(name: string): { tag: string; color: string } {
  if (name.startsWith("mcp__")) return { tag: "MCP", color: "var(--series-tool)" };
  if (name.startsWith("kb__")) return { tag: "RAG", color: "var(--seq-300)" };
  return { tag: "내장", color: "var(--text-muted)" };
}

export default function AgentConfigPanel({
  config,
  onChange,
  tools,
  providers,
  presets,
}: Props) {
  const set = <K extends keyof AgentConfig>(key: K, value: AgentConfig[K]) =>
    onChange({ ...config, [key]: value });

  const toggleTool = (name: string) =>
    set(
      "tools",
      config.tools.includes(name)
        ? config.tools.filter((t) => t !== name)
        : [...config.tools, name],
    );

  return (
    <div className="space-y-4">
      {/* 프리셋 로드 */}
      {presets.length > 0 && (
        <div>
          <label className={label}>프리셋 불러오기</label>
          <select
            className={input}
            value=""
            onChange={(e) => {
              const preset = presets.find((p) => p.agent_id === e.target.value);
              if (preset) onChange(preset);
            }}
          >
            <option value="">— 프리셋 선택 —</option>
            {presets.map((p) => (
              <option key={p.agent_id} value={p.agent_id}>
                {p.name} ({p.agent_id})
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={label}>Agent ID</label>
          <input
            className={input}
            value={config.agent_id}
            onChange={(e) => set("agent_id", e.target.value)}
          />
        </div>
        <div>
          <label className={label}>이름</label>
          <input
            className={input}
            value={config.name}
            onChange={(e) => set("name", e.target.value)}
          />
        </div>
      </div>

      {/* 모델 — provider/model 형식. provider 목록은 추상화 레이어에서 온다 */}
      <div>
        <label className={label}>
          모델{" "}
          <span className="text-[var(--text-muted)]">
            (provider/model · 사용 가능: {providers.join(", ") || "로딩 중"})
          </span>
        </label>
        <input
          className={input}
          value={config.model_name}
          onChange={(e) => set("model_name", e.target.value)}
          placeholder="ollama/qwen3:8b"
        />
      </div>

      <div>
        <label className={label}>시스템 프롬프트</label>
        <textarea
          className={`${input} min-h-28 resize-y font-mono text-xs leading-relaxed`}
          value={config.system_prompt}
          onChange={(e) => set("system_prompt", e.target.value)}
        />
      </div>

      {/* Temperature */}
      <div>
        <label className={label}>
          Temperature ·{" "}
          <span className="text-[var(--text-primary)]">
            {config.model_params?.temperature ?? 0.7}
          </span>
        </label>
        <input
          type="range"
          min={0}
          max={2}
          step={0.1}
          className="w-full accent-[var(--accent)]"
          value={config.model_params?.temperature ?? 0.7}
          onChange={(e) =>
            set("model_params", {
              ...config.model_params,
              temperature: Number(e.target.value),
            })
          }
        />
      </div>

      {/* 도구 체크박스 — 내장 + MCP + 지식베이스가 한 레지스트리에서 */}
      <div>
        <label className={label}>도구 ({config.tools.length}개 선택)</label>
        <div className="max-h-56 space-y-1 overflow-y-auto rounded-lg border border-[var(--border-1)] p-2">
          {tools.length === 0 && (
            <p className="p-2 text-xs text-[var(--text-muted)]">
              사용 가능한 도구가 없습니다
            </p>
          )}
          {tools.map((t) => {
            const origin = toolOrigin(t.name);
            return (
              <label
                key={t.name}
                className="flex cursor-pointer items-start gap-2 rounded-md p-2 hover:bg-[var(--surface-2)]"
                title={t.description}
              >
                <input
                  type="checkbox"
                  className="mt-0.5 accent-[var(--accent)]"
                  checked={config.tools.includes(t.name)}
                  onChange={() => toggleTool(t.name)}
                />
                <span className="min-w-0">
                  <span className="flex items-center gap-1.5">
                    <code className="text-xs">{t.name}</code>
                    <span
                      className="rounded px-1 text-[10px] font-semibold"
                      style={{ color: origin.color, border: `1px solid ${origin.color}` }}
                    >
                      {origin.tag}
                    </span>
                  </span>
                  <span className="mt-0.5 block truncate text-[11px] text-[var(--text-muted)]">
                    {t.description.split("\n")[0]}
                  </span>
                </span>
              </label>
            );
          })}
        </div>
      </div>

      {/* 현재 설정 JSON 미리보기 — "이 JSON 이 곧 에이전트" 를 보여주는 장치 */}
      <details className="rounded-lg border border-[var(--border-1)]">
        <summary className="cursor-pointer px-3 py-2 text-xs text-[var(--text-muted)]">
          AgentConfig JSON 보기
        </summary>
        <pre className="overflow-x-auto p-3 text-[11px] leading-relaxed text-[var(--text-secondary)]">
          {JSON.stringify(config, null, 2)}
        </pre>
      </details>
    </div>
  );
}

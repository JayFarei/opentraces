import { useState } from "react";
import type { ToolCall, Observation } from "../../types/trace";

const TOOL_COLORS: Record<string, string> = {
  Read: "var(--blue)",
  Edit: "var(--yellow)",
  Bash: "var(--green)",
  Grep: "var(--purple, #A855F7)",
  Write: "var(--accent)",
  Glob: "var(--cyan)",
};

function toolColor(name: string): string {
  return TOOL_COLORS[name] ?? "var(--text-muted)";
}

interface ToolCallDetailProps {
  toolCall: ToolCall;
  observation: Observation | null;
  compact?: boolean;
}

export function ToolCallDetail({ toolCall, observation, compact }: ToolCallDetailProps) {
  const [inputExpanded, setInputExpanded] = useState(false);
  const color = toolColor(toolCall.tool_name);

  const inputJson = JSON.stringify(toolCall.input, null, 2);
  const truncatedInput = inputJson.length > 200 && !inputExpanded
    ? inputJson.slice(0, 200) + "..."
    : inputJson;

  return (
    <div className={`border border-[var(--border)] ${compact ? "" : ""}`}>
      {/* Header */}
      <div className="flex items-center gap-2 px-2 py-1 bg-[var(--surface)]">
        <span
          className="text-[11px] font-[family-name:var(--font-mono)] font-bold"
          style={{ color }}
        >
          {toolCall.tool_name}
        </span>
        {"file_path" in toolCall.input && toolCall.input.file_path != null && (
          <span className="text-[10px] font-[family-name:var(--font-mono)] text-[var(--text-muted)] truncate">
            {String(toolCall.input.file_path)}
          </span>
        )}
        {"pattern" in toolCall.input && toolCall.input.pattern != null && (
          <span className="text-[10px] font-[family-name:var(--font-mono)] text-[var(--text-muted)] truncate">
            {String(toolCall.input.pattern)}
          </span>
        )}
        {toolCall.duration_ms !== null && (
          <span className="ml-auto text-[9px] font-[family-name:var(--font-mono)] text-[var(--text-dim)] flex-none">
            {toolCall.duration_ms}ms
          </span>
        )}
      </div>

      {/* Input */}
      <div className="px-2 py-1 border-t border-[var(--border)]">
        <button
          onClick={() => setInputExpanded((v) => !v)}
          className="text-[9px] font-[family-name:var(--font-mono)] text-[var(--text-muted)] hover:text-[var(--text)] cursor-pointer mb-1"
        >
          [{inputExpanded ? "collapse" : "expand"} input]
        </button>
        <pre className="text-[10px] font-[family-name:var(--font-mono)] text-[var(--text-secondary)] whitespace-pre-wrap overflow-x-auto max-h-[200px] overflow-y-auto">
          {truncatedInput}
        </pre>
      </div>

      {/* Observation */}
      {observation && (
        <div className="px-2 py-1 border-t border-[var(--border)] bg-[var(--bg-alt)]">
          <span className="text-[9px] font-[family-name:var(--font-mono)] text-[var(--text-muted)] block mb-1">
            output
          </span>
          {observation.error ? (
            <pre className="text-[10px] font-[family-name:var(--font-mono)] text-[var(--red)] whitespace-pre-wrap max-h-[150px] overflow-y-auto">
              {observation.error}
            </pre>
          ) : (
            <pre className="text-[10px] font-[family-name:var(--font-mono)] text-[var(--text)] whitespace-pre-wrap max-h-[150px] overflow-y-auto">
              {observation.output_summary ?? observation.content ?? "[no output]"}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

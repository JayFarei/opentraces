import { useSelection } from "../../contexts/SelectionContext";
import { useTraceData } from "../../hooks/useTraceData";
import { useViewPreferences } from "../../contexts/ViewPreferencesContext";
import { cleanSessionName, formatTokens, formatDuration } from "../../lib/format";


export function SessionHeader() {
  const { selectedSessionId } = useSelection();
  const { data: trace } = useTraceData(selectedSessionId);
  const { traceViewMode, setTraceViewMode } = useViewPreferences();

  if (!selectedSessionId || !trace) {
    return null;
  }

  const sessionName = cleanSessionName(
    trace.task.description ?? "",
    trace.timestamp_start,
  );

  // Date formatting
  let dateStr = "";
  if (trace.timestamp_start) {
    try {
      const d = new Date(trace.timestamp_start);
      const month = d.toLocaleString("en-US", { month: "short" });
      const day = d.getDate();
      const time = d.toLocaleString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
      dateStr = `${month} ${String(day)} ${time}`;
    } catch {
      /* ignore */
    }
  }

  // Duration
  const durationStr = trace.metrics.total_duration_s
    ? formatDuration(trace.metrics.total_duration_s * 1000)
    : null;

  // Model: extract from agent.model or from step models
  let rawModel = trace.agent.model;
  if (!rawModel) {
    const stepModels = [...new Set(
      trace.steps.map((s) => s.model).filter((m): m is string => m !== null && m !== undefined)
    )];
    rawModel = stepModels.join(", ");
  }
  const model = (rawModel || trace.agent.name || "")
    .split(", ")
    .map((m) => m.replace(/^anthropic\//, "").replace(/^claude-/, "").replace(/-\d{8}$/, ""))
    .join(", ") || trace.agent.name;

  // Tokens
  const inputTokens = formatTokens(trace.metrics.total_input_tokens);
  const outputTokens = formatTokens(trace.metrics.total_output_tokens);

  // Cache rate
  const cacheRate = trace.metrics.cache_hit_rate;
  const cacheStr = cacheRate !== null ? `${Math.round(cacheRate * 100)}%` : null;

  // Security
  const securityLabel = trace.security.scanned ? "SCANNED" : "UNSCANNED";
  const securityColor = trace.security.scanned ? "var(--green)" : "var(--text-dim)";
  const redactionCount = trace.security.redactions_applied;

  return (
    <div className="flex-none border-b border-[var(--border)] bg-[var(--surface)] px-3 py-1.5">
      {/* Line 1: session name + date + duration + step count */}
      <div className="flex items-center gap-3 text-[11px] font-[family-name:var(--font-mono)]">
        <span className="text-[var(--text)] font-semibold truncate max-w-[50%]">
          {sessionName}
        </span>
        {dateStr && (
          <span className="text-[var(--text-muted)] flex-none">{dateStr}</span>
        )}
        {durationStr && (
          <span className="text-[var(--text-muted)] flex-none">{durationStr}</span>
        )}
        <span className="text-[var(--text-dim)] flex-none">
          {trace.steps.length} steps
        </span>
      </div>

      {/* Line 2: model + tokens + cache + tier + view toggle */}
      <div className="flex items-center gap-3 text-[10px] font-[family-name:var(--font-mono)] mt-0.5">
        <span className="text-[var(--text-muted)]">{model}</span>
        <span className="text-[var(--text-dim)]">
          {inputTokens} in / {outputTokens} out
        </span>
        {cacheStr && (
          <span className="text-[var(--text-dim)]">cache: {cacheStr}</span>
        )}
        <span
          className="flex-none px-1 py-0 border text-[9px] uppercase tracking-wider"
          style={{ color: securityColor, borderColor: securityColor }}
        >
          {securityLabel}
        </span>
        {redactionCount > 0 && (
          <span className="text-[var(--yellow)] flex-none">
            {redactionCount} redactions
          </span>
        )}

        {/* View mode toggle */}
        <div className="ml-auto flex items-center gap-0.5">
          <button
            onClick={() => setTraceViewMode("timeline")}
            className={`px-2 py-0.5 text-[9px] uppercase tracking-wider font-[family-name:var(--font-mono)] cursor-pointer transition-colors duration-100 ${
              traceViewMode === "timeline"
                ? "text-[var(--accent)] border border-[var(--accent)]"
                : "text-[var(--text-dim)] border border-[var(--border)] hover:text-[var(--text-muted)]"
            }`}
          >
            timeline
          </button>
          <button
            onClick={() => setTraceViewMode("review")}
            className={`px-2 py-0.5 text-[9px] uppercase tracking-wider font-[family-name:var(--font-mono)] cursor-pointer transition-colors duration-100 ${
              traceViewMode === "review"
                ? "text-[var(--accent)] border border-[var(--accent)]"
                : "text-[var(--text-dim)] border border-[var(--border)] hover:text-[var(--text-muted)]"
            }`}
          >
            review
          </button>
        </div>
      </div>
    </div>
  );
}

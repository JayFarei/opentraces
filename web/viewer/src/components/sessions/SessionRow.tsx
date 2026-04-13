import { useSelection } from "../../contexts/SelectionContext";
import { cleanTraceName } from "../../lib/format";
import type { TraceListItem } from "../../types/trace";
import { VerdictBadge } from "./VerdictBadge";

interface TraceRowProps {
  trace: TraceListItem;
}

/** Shorten model name: "claude-sonnet-4-6" -> "sonnet-4-6" */
function shortModel(model: string): string {
  return model
    .split(", ")
    .map((m) => m.replace(/^claude-/, "").replace(/^anthropic\/claude-/, ""))
    .join(", ");
}

export function TraceRow({ trace }: TraceRowProps) {
  const { selectedTraceId, setSelectedTraceId } = useSelection();
  const isSelected = selectedTraceId === trace.trace_id;

  const shortId = trace.trace_id.slice(0, 8);
  const taskName = cleanTraceName(trace.task_description, trace.timestamp);
  const model = shortModel(trace.model);

  return (
    <button
      onClick={() => setSelectedTraceId(trace.trace_id)}
      className={`w-full text-left px-3 py-1.5 transition-colors duration-100 cursor-pointer border-l-2 ${
        isSelected
          ? "border-l-[var(--accent)] bg-[var(--surface-hover)]"
          : "border-l-transparent hover:bg-[var(--surface-hover)]"
      }`}
    >
      {/* Line 1: short ID + model + step count */}
      <div className="flex items-center gap-2 text-[9px] font-[family-name:var(--font-mono)]">
        <span className="text-[var(--text-muted)] font-mono">{shortId}</span>
        <span className="text-[var(--cyan)]">{model}</span>
        <span className="text-[var(--text-dim)]">{trace.step_count}s</span>
        {trace.flag_count > 0 && (
          <span className="text-[var(--red)]">{trace.flag_count}f</span>
        )}
        {trace.llm_review && trace.llm_review.status === "complete" && (
          <VerdictBadge review={trace.llm_review} compact />
        )}
      </div>
      {/* Line 2: task keywords */}
      <div className="text-[10px] font-[family-name:var(--font-mono)] text-[var(--text)] truncate leading-tight mt-0.5">
        {taskName}
      </div>
    </button>
  );
}

import { useSelection } from "../../contexts/SelectionContext";
import { cleanSessionName } from "../../lib/format";
import type { SessionListItem } from "../../types/trace";

interface SessionRowProps {
  session: SessionListItem;
}

/** Shorten model name: "claude-sonnet-4-6" -> "sonnet-4-6" */
function shortModel(model: string): string {
  return model
    .split(", ")
    .map((m) => m.replace(/^claude-/, "").replace(/^anthropic\/claude-/, ""))
    .join(", ");
}

export function SessionRow({ session }: SessionRowProps) {
  const { selectedSessionId, setSelectedSessionId } = useSelection();
  const isSelected = selectedSessionId === session.trace_id;

  const shortId = session.trace_id.slice(0, 8);
  const taskName = cleanSessionName(session.task_description, session.timestamp);
  const model = shortModel(session.model);

  return (
    <button
      onClick={() => setSelectedSessionId(session.trace_id)}
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
        <span className="text-[var(--text-dim)]">{session.step_count}s</span>
        {session.flag_count > 0 && (
          <span className="text-[var(--red)]">{session.flag_count}f</span>
        )}
      </div>
      {/* Line 2: task keywords */}
      <div className="text-[10px] font-[family-name:var(--font-mono)] text-[var(--text)] truncate leading-tight mt-0.5">
        {taskName}
      </div>
    </button>
  );
}

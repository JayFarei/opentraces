import { useSelection } from "../../contexts/SelectionContext";
import { cleanSessionName } from "../../lib/format";
import type { SessionListItem } from "../../types/trace";

interface SessionRowProps {
  session: SessionListItem;
}

export function SessionRow({ session }: SessionRowProps) {
  const { selectedSessionId, setSelectedSessionId } = useSelection();
  const isSelected = selectedSessionId === session.trace_id;

  const hasFlagsAndStaged = session.stage === "staged" && session.flag_count > 0;
  const isAutoClean = session.stage === "staged" && session.flag_count === 0;

  return (
    <button
      onClick={() => setSelectedSessionId(session.trace_id)}
      className={`w-full text-left px-3 py-1.5 transition-colors duration-100 cursor-pointer border-l-2 ${
        isSelected
          ? "border-l-[var(--accent)] bg-[var(--surface-hover)]"
          : "border-l-transparent hover:bg-[var(--surface-hover)]"
      }`}
    >
      <div className="text-[11px] font-[family-name:var(--font-mono)] text-[var(--text)] truncate leading-tight">
        {cleanSessionName(session.task_description, session.timestamp)}
      </div>
      <div className="flex items-center gap-2 mt-0.5">
        <span className="text-[9px] font-[family-name:var(--font-mono)] text-[var(--text-muted)]">
          {session.agent_name}
        </span>
        <span className="text-[9px] font-[family-name:var(--font-mono)] text-[var(--text-dim)]">
          {session.model}
        </span>
        <span className="text-[9px] font-[family-name:var(--font-mono)] text-[var(--text-dim)]">
          {session.step_count} steps
        </span>
        {session.flag_count > 0 && (
          <span className="text-[9px] font-[family-name:var(--font-mono)] text-[var(--red)]">
            {session.flag_count}f
          </span>
        )}
      </div>
      {hasFlagsAndStaged && (
        <span className="inline-block mt-0.5 text-[8px] font-[family-name:var(--font-mono)] text-[var(--yellow)] border border-[var(--yellow)] px-1">
          review
        </span>
      )}
      {isAutoClean && (
        <span className="inline-block mt-0.5 text-[8px] font-[family-name:var(--font-mono)] text-[var(--green)] border border-[var(--green)] px-1">
          auto-clear
        </span>
      )}
    </button>
  );
}

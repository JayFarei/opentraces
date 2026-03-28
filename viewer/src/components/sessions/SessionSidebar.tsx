import { useSessionList } from "../../hooks/useSessionList";
import { StageGroup } from "./StageGroup";
import type { SessionStage, SessionListItem } from "../../types/trace";

const STAGE_ORDER: SessionStage[] = ["unstaged", "staged", "committed", "pushed", "rejected"];

export function SessionSidebar() {
  const { data: sessions, isLoading, error } = useSessionList();

  if (isLoading) {
    return (
      <div className="h-full flex items-center justify-center text-[var(--text-muted)] text-[11px] font-[family-name:var(--font-mono)]">
        loading...
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-full flex items-center justify-center text-[var(--red)] text-[11px] font-[family-name:var(--font-mono)] px-3">
        error: {error.message}
      </div>
    );
  }

  if (!sessions || sessions.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-[var(--text-muted)] text-[10px] font-[family-name:var(--font-mono)] px-3 text-center leading-relaxed">
        no sessions found.
        <br />
        run opentraces to parse agent traces.
      </div>
    );
  }

  const grouped: Record<SessionStage, SessionListItem[]> = {
    unstaged: [],
    staged: [],
    committed: [],
    pushed: [],
    rejected: [],
  };

  for (const s of sessions) {
    const bucket = grouped[s.stage];
    if (bucket) {
      bucket.push(s);
    } else {
      grouped.unstaged.push(s);
    }
  }

  return (
    <div className="h-full flex flex-col overflow-hidden bg-[var(--bg)]">
      {/* Remote info */}
      <div className="flex-none px-3 py-2 border-b border-[var(--border)]">
        <div className="text-[9px] uppercase tracking-wider text-[var(--text-dim)] font-[family-name:var(--font-mono)] mb-0.5">
          remote
        </div>
        <div className="text-[10px] text-[var(--text-muted)] font-[family-name:var(--font-mono)] truncate">
          hf/opentraces-data
        </div>
      </div>

      {/* Session groups */}
      <div className="flex-1 overflow-y-auto">
        {STAGE_ORDER.map((stage) =>
          grouped[stage].length > 0 ? (
            <StageGroup key={stage} stage={stage} sessions={grouped[stage]} />
          ) : null,
        )}
      </div>
    </div>
  );
}

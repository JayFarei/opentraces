import { useSessionList } from "../../hooks/useSessionList";
import { StageGroup } from "./StageGroup";
import type { SessionStage, SessionListItem } from "../../types/trace";

const STAGE_ORDER: SessionStage[] = ["inbox", "committed", "pushed", "rejected"];

export function SessionPanel() {
  const { data: sessions, isLoading, error } = useSessionList();

  if (isLoading) {
    return (
      <div className="h-full flex items-center justify-center text-[var(--text-muted)] text-[11px] font-[family-name:var(--font-mono)]">
        loading sessions...
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-full flex items-center justify-center text-[var(--red)] text-[11px] font-[family-name:var(--font-mono)] px-4">
        error: {error.message}
      </div>
    );
  }

  if (!sessions || sessions.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-[var(--text-muted)] text-[11px] font-[family-name:var(--font-mono)] px-4 text-center">
        no sessions found.
        <br />
        run opentraces init to create this repo inbox.
      </div>
    );
  }

  const grouped: Record<SessionStage, SessionListItem[]> = {
    inbox: [],
    committed: [],
    pushed: [],
    rejected: [],
  };

  for (const s of sessions) {
    const bucket = grouped[s.stage];
    if (bucket) {
      bucket.push(s);
    } else {
      grouped.inbox.push(s);
    }
  }

  return (
    <div className="h-full overflow-y-auto bg-[var(--bg)]">
      <div className="px-3 py-2 border-b border-[var(--border)]">
        <span className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] font-[family-name:var(--font-mono)]">
          sessions ({sessions.length})
        </span>
      </div>
      {STAGE_ORDER.map((stage) =>
        grouped[stage].length > 0 ? (
          <StageGroup key={stage} stage={stage} sessions={grouped[stage]} />
        ) : null,
      )}
    </div>
  );
}

import { useTraceList } from "../../hooks/useTraceList";
import { StageGroup } from "./StageGroup";
import type { TraceStage, TraceListItem } from "../../types/trace";

const STAGE_ORDER: TraceStage[] = ["inbox", "staged", "pushed", "rejected"];

export function TraceListPanel() {
  const { data: traces, isLoading, error } = useTraceList();

  if (isLoading) {
    return (
      <div className="h-full flex items-center justify-center text-[var(--text-muted)] text-[11px] font-[family-name:var(--font-mono)]">
        loading traces...
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

  if (!traces || traces.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-[var(--text-muted)] text-[11px] font-[family-name:var(--font-mono)] px-4 text-center">
        no traces found.
        <br />
        run opentraces init to create this repo inbox.
      </div>
    );
  }

  const grouped: Record<TraceStage, TraceListItem[]> = {
    inbox: [],
    staged: [],
    pushed: [],
    rejected: [],
  };

  for (const s of traces) {
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
          traces ({traces.length})
        </span>
      </div>
      {STAGE_ORDER.map((stage) =>
        grouped[stage].length > 0 ? (
          <StageGroup key={stage} stage={stage} traces={grouped[stage]} />
        ) : null,
      )}
    </div>
  );
}

import { useState } from "react";
import { useSessionList } from "../../hooks/useSessionList";
import { useAppContext } from "../../hooks/useAppContext";
import { StageGroup } from "./StageGroup";
import { RemoteSetForm } from "../RemoteSetForm";
import type { SessionStage, SessionListItem } from "../../types/trace";

const STAGE_ORDER: SessionStage[] = ["inbox", "committed", "pushed", "rejected"];

export function SessionSidebar() {
  const { data: sessions, isLoading, error } = useSessionList();
  const { data: appContext } = useAppContext();
  const [showRemoteForm, setShowRemoteForm] = useState(false);

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
    <div className="h-full flex flex-col overflow-hidden bg-[var(--bg)]">
      {/* Remote info */}
      <div className="flex-none px-3 py-2 border-b border-[var(--border)]">
        <div className="text-[9px] uppercase tracking-wider text-[var(--text-dim)] font-[family-name:var(--font-mono)] mb-0.5">
          remote
        </div>
        {showRemoteForm ? (
          <RemoteSetForm onDone={() => setShowRemoteForm(false)} />
        ) : appContext?.remote ? (
          <div
            className="text-[10px] text-[var(--text-muted)] font-[family-name:var(--font-mono)] truncate cursor-pointer hover:text-[var(--text)] transition-colors duration-100"
            onClick={() => setShowRemoteForm(true)}
            title="Click to change remote"
          >
            {appContext.remote}
          </div>
        ) : (
          <button
            onClick={() => setShowRemoteForm(true)}
            className="text-[10px] text-[var(--accent)] font-[family-name:var(--font-mono)] hover:underline cursor-pointer"
          >
            not set - configure
          </button>
        )}
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

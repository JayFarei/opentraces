import { useState } from "react";
import { useSessionList } from "../../hooks/useSessionList";
import { useAppContext } from "../../hooks/useAppContext";
import { useViewPreferences } from "../../contexts/ViewPreferencesContext";
import { RemoteSetForm } from "../RemoteSetForm";
import type { SessionStage } from "../../types/trace";

export function Header() {
  const { data: sessions } = useSessionList();
  const { data: appContext } = useAppContext();
  const { theme, toggleTheme } = useViewPreferences();
  const [showRemoteForm, setShowRemoteForm] = useState(false);

  const counts: Record<SessionStage, number> = {
    inbox: 0,
    committed: 0,
    pushed: 0,
    rejected: 0,
  };

  if (sessions) {
    for (const s of sessions) {
      counts[s.stage]++;
    }
  }

  return (
    <header
      className="flex items-center justify-between px-4 py-2 border-b border-[var(--border)] bg-[var(--surface)]"
      style={{ fontFamily: "var(--font-mono)", fontSize: "14px" }}
    >
      <div className="flex items-center gap-4">
        <div className="flex flex-col">
          <span className="font-[family-name:var(--font-display)] text-[15px] tracking-tight text-[var(--text)]">
            <span style={{ fontWeight: 300 }}>open</span><span className="font-bold">traces</span>
          </span>
          <span className="text-[9px] uppercase tracking-wider text-[var(--text-dim)]">
            {appContext?.project_name ?? "repo inbox"}
          </span>
        </div>

        <div className="flex items-center gap-3 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
          <span className="text-[var(--yellow)]">inbox: {counts.inbox}</span>
          <span className="text-[var(--green)]">committed: {counts.committed}</span>
          <span className="text-[var(--cyan)]">pushed: {counts.pushed}</span>
        </div>
      </div>

      <div className="flex items-center gap-3">
        {showRemoteForm ? (
          <div className="w-[220px]">
            <RemoteSetForm onDone={() => setShowRemoteForm(false)} />
          </div>
        ) : appContext?.remote ? (
          <span
            className="text-[10px] text-[var(--text-dim)] cursor-pointer hover:text-[var(--text)] transition-colors duration-100"
            onClick={() => setShowRemoteForm(true)}
            title="Click to change remote"
          >
            {appContext.remote}
          </span>
        ) : (
          <button
            onClick={() => setShowRemoteForm(true)}
            className="text-[10px] text-[var(--accent)] font-[family-name:var(--font-mono)] hover:underline cursor-pointer"
          >
            remote not set
          </button>
        )}
        <button
          onClick={toggleTheme}
          className="text-[11px] text-[var(--text-muted)] hover:text-[var(--text)] transition-colors duration-100 cursor-pointer"
        >
          [{theme === "dark" ? "light" : "dark"}]
        </button>
      </div>
    </header>
  );
}

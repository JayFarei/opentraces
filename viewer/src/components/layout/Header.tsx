import { useSessionList } from "../../hooks/useSessionList";
import { useAppContext } from "../../hooks/useAppContext";
import { useReviewActions } from "../../hooks/useReviewActions";
import { useViewPreferences } from "../../contexts/ViewPreferencesContext";
import type { SessionStage } from "../../types/trace";

export function Header() {
  const { data: sessions } = useSessionList();
  const { data: appContext } = useAppContext();
  const { push } = useReviewActions();
  const { theme, toggleTheme } = useViewPreferences();

  const counts: Record<SessionStage, number> = {
    inbox: 0,
    ready: 0,
    committed: 0,
    pushed: 0,
    rejected: 0,
  };

  if (sessions) {
    for (const s of sessions) {
      counts[s.stage]++;
    }
  }

  const hasCommitted = counts.committed > 0;

  return (
    <header
      className="flex items-center justify-between px-4 py-2 border-b border-[var(--border)] bg-[var(--surface)]"
      style={{ fontFamily: "var(--font-mono)", fontSize: "14px" }}
    >
      <div className="flex items-center gap-4">
        <div className="flex flex-col">
          <span className="font-[family-name:var(--font-body)] text-[15px] tracking-tight">
            open<span className="font-bold">traces</span>
          </span>
          <span className="text-[9px] uppercase tracking-wider text-[var(--text-dim)]">
            {appContext?.project_name ?? "repo inbox"}
          </span>
        </div>

        <div className="flex items-center gap-3 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
          <span className="text-[var(--yellow)]">inbox: {counts.inbox}</span>
          <span className="text-[var(--green)]">ready: {counts.ready}</span>
          <span className="text-[var(--green)]">committed: {counts.committed}</span>
          <span className="text-[var(--cyan)]">pushed: {counts.pushed}</span>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <span className="text-[10px] text-[var(--text-dim)]">
          {appContext?.remote ?? "remote not set"}
        </span>
        <button
          onClick={toggleTheme}
          className="text-[11px] text-[var(--text-muted)] hover:text-[var(--text)] transition-colors duration-100 cursor-pointer"
        >
          [{theme === "dark" ? "light" : "dark"}]
        </button>
        <button
          onClick={() => push.mutate(undefined)}
          disabled={!hasCommitted}
          className={`text-[11px] transition-colors duration-100 cursor-pointer ${
            hasCommitted
              ? "text-[var(--accent)] hover:text-[var(--text)] border border-[var(--accent)]"
              : "text-[var(--text-dim)] border border-[var(--border)] cursor-not-allowed"
          } px-2 py-0.5`}
        >
          [push committed]
        </button>
      </div>
    </header>
  );
}

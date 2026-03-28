import { useSessionList } from "../../hooks/useSessionList";
import { useViewPreferences } from "../../contexts/ViewPreferencesContext";
import { Logo } from "../icons/Logo";
import type { SessionStage } from "../../types/trace";

export function Header() {
  const { data: sessions } = useSessionList();
  const { theme, toggleTheme } = useViewPreferences();

  const counts: Record<SessionStage, number> = {
    unstaged: 0,
    staged: 0,
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
        <div className="flex items-center gap-2">
          <Logo size={22} />
          <span className="font-[family-name:var(--font-display)] text-[16px] font-bold tracking-tight">
            opentraces<span className="text-[var(--accent)]">.</span>ai
          </span>
        </div>

        <div className="flex items-center gap-3 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
          <span>unstaged: {counts.unstaged}</span>
          <span className="text-[var(--yellow)]">staged: {counts.staged}</span>
          <span className="text-[var(--green)]">committed: {counts.committed}</span>
          <span className="text-[var(--cyan)]">pushed: {counts.pushed}</span>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={toggleTheme}
          className="text-[11px] text-[var(--text-muted)] hover:text-[var(--text)] transition-colors duration-100 cursor-pointer"
        >
          [{theme === "dark" ? "light" : "dark"}]
        </button>
        <button
          disabled={!hasCommitted}
          className={`text-[11px] transition-colors duration-100 cursor-pointer ${
            hasCommitted
              ? "text-[var(--accent)] hover:text-[var(--text)] border border-[var(--accent)]"
              : "text-[var(--text-dim)] border border-[var(--border)] cursor-not-allowed"
          } px-2 py-0.5`}
        >
          [push to hub]
        </button>
      </div>
    </header>
  );
}

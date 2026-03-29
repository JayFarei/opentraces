interface KeyboardHelpProps {
  onDismiss: () => void;
}

const SHORTCUTS = [
  { key: "j / k", desc: "navigate down / up" },
  { key: "Enter", desc: "expand / select" },
  { key: "Tab", desc: "switch panel focus" },
  { key: "c", desc: "open commit dialog" },
  { key: "p", desc: "push committed" },
  { key: "r", desc: "toggle redaction preview" },
  { key: "?", desc: "show / hide this help" },
  { key: "Esc", desc: "dismiss overlay" },
] as const;

export function KeyboardHelp({ onDismiss }: KeyboardHelpProps) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      onClick={onDismiss}
      onKeyDown={(e) => {
        if (e.key === "Escape" || e.key === "?") onDismiss();
      }}
    >
      {/* backdrop */}
      <div className="absolute inset-0 bg-black/60" />

      {/* card */}
      <div
        className="relative z-10 bg-[var(--surface)] border border-[var(--border)] px-8 py-6 max-w-md w-full"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-[13px] font-[family-name:var(--font-display)] text-[var(--text)] mb-4 tracking-tight">
          keyboard shortcuts
        </div>

        <table className="w-full text-[11px] font-[family-name:var(--font-mono)]">
          <tbody>
            {SHORTCUTS.map((s) => (
              <tr key={s.key} className="border-b border-[var(--border)]">
                <td className="py-1.5 pr-4 text-[var(--accent)] whitespace-nowrap">
                  {s.key}
                </td>
                <td className="py-1.5 text-[var(--text-muted)]">{s.desc}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="mt-4 text-[9px] text-[var(--text-dim)] font-[family-name:var(--font-mono)]">
          press any key to dismiss
        </div>
      </div>
    </div>
  );
}

import type { RedactionPreview as RedactionPreviewType } from "../../types/trace";

interface RedactionPreviewProps {
  preview: RedactionPreviewType;
}

export function RedactionPreview({ preview }: RedactionPreviewProps) {
  const signalPct = Math.round(preview.signal_kept * 100);

  return (
    <div className="border-2 border-[var(--yellow)]">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-[var(--surface)] border-b border-[var(--yellow)]">
        <span className="text-[10px] uppercase tracking-wider font-[family-name:var(--font-mono)] text-[var(--yellow)]">
          redaction preview
        </span>
        <div className="flex gap-2">
          <button className="text-[10px] font-[family-name:var(--font-mono)] text-[var(--green)] hover:text-[var(--text)] cursor-pointer">
            [accept]
          </button>
          <button className="text-[10px] font-[family-name:var(--font-mono)] text-[var(--text-muted)] hover:text-[var(--text)] cursor-pointer">
            [edit]
          </button>
        </div>
      </div>

      {/* Diff rows */}
      <div className="divide-y divide-[var(--border)]">
        {preview.steps.map((step) =>
          step.redactions.map((r, ri) => (
            <div key={`${step.step_index}-${ri}`} className="grid grid-cols-2 gap-0">
              <div className="px-3 py-1.5 bg-[var(--red-bg)]">
                <span className="text-[9px] text-[var(--text-muted)] font-[family-name:var(--font-mono)] block mb-0.5">
                  raw ({r.field})
                </span>
                <span className="text-[10px] font-[family-name:var(--font-mono)] text-[var(--red)] line-through">
                  {r.before}
                </span>
              </div>
              <div className="px-3 py-1.5 bg-[var(--green-bg)]">
                <span className="text-[9px] text-[var(--text-muted)] font-[family-name:var(--font-mono)] block mb-0.5">
                  published
                </span>
                <span className="text-[10px] font-[family-name:var(--font-mono)] text-[var(--green)]">
                  {r.after}
                </span>
              </div>
            </div>
          )),
        )}
      </div>

      {/* Signal kept bar */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-[var(--surface)] border-t border-[var(--border)]">
        <span className="text-[9px] font-[family-name:var(--font-mono)] text-[var(--text-muted)]">
          signal kept
        </span>
        <div className="flex-1 h-1.5 bg-[var(--border)]">
          <div
            className="h-full bg-[var(--green)]"
            style={{ width: `${signalPct}%` }}
          />
        </div>
        <span className="text-[9px] font-[family-name:var(--font-mono)] text-[var(--text)]">
          {signalPct}%
        </span>
      </div>
    </div>
  );
}

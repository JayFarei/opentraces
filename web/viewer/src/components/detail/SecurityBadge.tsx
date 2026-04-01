import type { SecurityMetadata } from "../../types/trace";

interface SecurityBadgeProps {
  security: SecurityMetadata;
}

export function SecurityBadge({ security }: SecurityBadgeProps) {
  const color = security.scanned ? "var(--green)" : "var(--text-dim)";
  const label = security.scanned ? "SCANNED" : "UNSCANNED";

  return (
    <div className="flex items-center gap-2">
      <span
        className="text-[10px] uppercase font-[family-name:var(--font-mono)] px-1.5 py-0 border"
        style={{ color, borderColor: color }}
      >
        {label}
      </span>
      {security.flags_reviewed > 0 && (
        <span className="text-[9px] font-[family-name:var(--font-mono)] text-[var(--text-muted)]">
          {security.flags_reviewed} flags
        </span>
      )}
      {security.redactions_applied > 0 && (
        <span className="text-[9px] font-[family-name:var(--font-mono)] text-[var(--text-muted)]">
          {security.redactions_applied} redactions
        </span>
      )}
    </div>
  );
}

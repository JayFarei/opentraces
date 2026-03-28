import type { SecurityMetadata } from "../../types/trace";

const TIER_CONFIG: Record<number, { label: string; color: string }> = {
  1: { label: "OPEN", color: "var(--green)" },
  2: { label: "GUARDED", color: "var(--yellow)" },
  3: { label: "STRICT", color: "var(--red)" },
};

interface SecurityBadgeProps {
  security: SecurityMetadata;
}

export function SecurityBadge({ security }: SecurityBadgeProps) {
  const config = TIER_CONFIG[security.tier] ?? TIER_CONFIG[1]!;

  return (
    <div className="flex items-center gap-2">
      <span
        className="text-[10px] uppercase font-[family-name:var(--font-mono)] px-1.5 py-0 border"
        style={{ color: config.color, borderColor: config.color }}
      >
        {config.label}
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

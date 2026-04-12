/**
 * Plan 032 — visible chip summarizing the Tier 2 LLM review verdict.
 *
 * Mirrors the taxonomy from `security/verdict_display.py` so the CLI,
 * TUI, and web viewer show identical wording. Intentionally tiny: one
 * element, four states. Styling uses existing CSS vars so it matches
 * the viewer theme without introducing new tokens.
 */

import type { LLMReviewSummary } from "../../types/trace";

type VerdictKind = "clean" | "blocked" | "manual" | "none";

function classify(review: LLMReviewSummary | undefined): VerdictKind {
  if (!review || review.status !== "complete") return "none";
  if (review.shareable === "no" || review.missed_sensitive_data === "yes") {
    return "blocked";
  }
  if (review.shareable === "yes" && review.missed_sensitive_data === "no") {
    return "clean";
  }
  return "manual";
}

const LABEL: Record<VerdictKind, string> = {
  clean: "✓ shareable",
  blocked: "✗ blocked",
  manual: "? manual",
  none: "no review",
};

const COLOR: Record<VerdictKind, string> = {
  clean: "var(--green)",
  blocked: "var(--red)",
  manual: "var(--yellow)",
  none: "var(--text-dim)",
};

interface Props {
  review?: LLMReviewSummary;
  /** Compact mode drops the "llm:" prefix for tight rows like the sidebar. */
  compact?: boolean;
}

export function VerdictBadge({ review, compact }: Props) {
  const kind = classify(review);
  const label = LABEL[kind];
  const color = COLOR[kind];
  const title = review?.summary
    ? `LLM review: ${label} — ${review.summary}`
    : `LLM review: ${label}`;

  return (
    <span
      title={title}
      className="text-[9px] font-[family-name:var(--font-mono)]"
      style={{ color }}
    >
      {compact ? label : `llm ${label}`}
    </span>
  );
}

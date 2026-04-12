"""Shared rendering helpers for LLM review verdicts and entity counts.

Consumed by the CLI, the TUI, and the Flask/React web review client so
all three surfaces speak with one voice. Keeping this in the security
module means no client has to know about dataclass shapes.
"""

from __future__ import annotations

from collections import Counter

from .anonymizer import EntityMap
from .llm_review import LLMReviewVerdict


# ---------------------------------------------------------------------------
# Verdict badge
# ---------------------------------------------------------------------------


def verdict_badge(verdict: LLMReviewVerdict | None) -> str:
    """One-line status badge suitable for CLI and inline TUI display."""
    if verdict is None:
        return "llm-review: not run"
    if verdict.shareable == "yes" and verdict.missed_sensitive_data == "no":
        return "llm-review: ✓ shareable"
    if verdict.shareable == "no" or verdict.missed_sensitive_data == "yes":
        return "llm-review: ✗ blocked"
    return "llm-review: ? needs manual review"


def verdict_summary_lines(verdict: LLMReviewVerdict | None) -> list[str]:
    """Multi-line summary for step/session inspectors."""
    if verdict is None:
        return ["LLM review: not run for this session."]

    lines = [
        verdict_badge(verdict),
        f"  shareable: {verdict.shareable}",
        f"  missed_sensitive_data: {verdict.missed_sensitive_data}",
    ]
    if verdict.summary:
        lines.append(f"  summary: {verdict.summary}")
    if verdict.flagged_parts:
        lines.append("  flagged:")
        for part in verdict.flagged_parts:
            reason = part.get("reason", "?")
            evidence = part.get("evidence", "")
            # Cap to 100 chars per plan; truncate mid-string with ellipsis.
            if len(evidence) > 100:
                evidence = evidence[:97] + "..."
            lines.append(f"    - {reason}: {evidence}")
    return lines


# ---------------------------------------------------------------------------
# EntityMap tallies
# ---------------------------------------------------------------------------


def entity_counts(entity_map: EntityMap | None) -> dict[str, int]:
    """Count entities by type for reviewer surfaces.

    Returns ``{}`` when ``entity_map`` is ``None`` so callers don't need
    to branch on the absence of a map.
    """
    if entity_map is None:
        return {}
    counts: Counter[str] = Counter()
    for etype, bucket in entity_map._entries.items():  # noqa: SLF001
        counts[etype] = len(bucket)
    return dict(counts)


def entity_summary_line(entity_map: EntityMap | None) -> str:
    """Human-readable 'N PERSON, M EMAIL' tally for inline display."""
    counts = entity_counts(entity_map)
    if not counts:
        return "no PII entities detected"
    parts = [f"{n} {etype}" for etype, n in sorted(counts.items())]
    return ", ".join(parts)


def verdict_to_payload(verdict: LLMReviewVerdict | None) -> dict:
    """JSON-safe payload for the web review client."""
    if verdict is None:
        return {"status": "not_run"}
    return {
        "status": "complete",
        "shareable": verdict.shareable,
        "missed_sensitive_data": verdict.missed_sensitive_data,
        "summary": verdict.summary,
        "flagged_parts": list(verdict.flagged_parts),
        "badge": verdict_badge(verdict),
    }

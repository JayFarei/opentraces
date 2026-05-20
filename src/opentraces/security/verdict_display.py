"""Shared rendering helpers for LLM review verdicts and entity counts.

Consumed by the CLI and retained legacy review clients. Keeping this in the
security module means no client has to know about dataclass shapes.
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


def verdict_to_payload(
    verdict: LLMReviewVerdict | None,
    *,
    api_format: str = "",
    model: str = "",
    base_url: str = "",
    reviewed_at: str = "",
    prompt_version: str = "",
) -> dict:
    """JSON-safe payload for review clients and dataset metadata.

    Provenance kwargs (``api_format``, ``model``, ``base_url``,
    ``reviewed_at``, ``prompt_version``) are included when provided so
    downstream surfaces (legacy clients, HF dataset metadata) can tell
    users *which* LLM issued the verdict, not just *a* verdict.
    """
    if verdict is None:
        out: dict = {"status": "not_run"}
    else:
        out = {
            "status": "complete",
            "shareable": verdict.shareable,
            "missed_sensitive_data": verdict.missed_sensitive_data,
            "summary": verdict.summary,
            "flagged_parts": list(verdict.flagged_parts),
            "badge": verdict_badge(verdict),
        }
    if api_format:
        out["api_format"] = api_format
    if model:
        out["model"] = model
    if base_url:
        out["base_url"] = base_url
    if reviewed_at:
        out["reviewed_at"] = reviewed_at
    if prompt_version:
        out["prompt_version"] = prompt_version
    return out

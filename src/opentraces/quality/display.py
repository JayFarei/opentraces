"""Human-friendly formatting for quality assessment output.

Internal persona names ("conformance", "training", "rl", "analytics",
"domain") are kept stable for compatibility with quality.json, README
frontmatter keys (``training_score``, ``rl_score``, …), and the gate
threshold table. This module maps them to display names + taglines for
the CLI, and derives "top fixes" / "top offenders" from the underlying
check data.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import BatchAssessment
    from .summary import QualitySummary


# Internal name -> (display name, one-line tagline)
LABELS: dict[str, tuple[str, str]] = {
    "conformance": ("Schema", "structural contract + secrets check"),
    "training": ("Conversation", "well-formed dialogue for SFT"),
    "rl": ("Outcome", "grounded reward signal"),
    "analytics": ("Metrics", "cost / timing / cache fields"),
    "domain": ("Metadata", "search + filter context"),
}


def display_name(internal: str) -> str:
    """Return the user-facing label for an internal persona name."""
    return LABELS.get(internal, (internal.capitalize(), ""))[0]


def display_tagline(internal: str) -> str:
    return LABELS.get(internal, ("", ""))[1]


# ---------------------------------------------------------------------------
# "Safe to publish?" — driven by the schema (conformance) checks alone.
# The rest of the personas are quality dimensions; safety is separate.
# ---------------------------------------------------------------------------


def safe_to_publish(batch: "BatchAssessment") -> tuple[bool, str]:
    """Return (safe, one-line reason).

    Safe = every trace's conformance score is at or above the gate's
    individual minimum (today: 70%). That is the secrets / schema /
    redaction bar; quality of dialogue or outcomes is orthogonal.
    """
    from .gates import DEFAULT_THRESHOLDS

    threshold = next(
        (t for t in DEFAULT_THRESHOLDS if t.persona == "conformance"), None
    )
    if threshold is None or threshold.min_individual is None:
        return True, "no schema threshold configured"

    bar = threshold.min_individual
    failing = []
    for a in batch.assessments:
        ps = a.persona_scores.get("conformance")
        if ps and not ps.skipped and ps.total_score < bar:
            failing.append((a.trace_id, ps.total_score))

    if not failing:
        return True, f"all traces clear the {bar:.0f}% schema bar"
    worst = min(failing, key=lambda x: x[1])
    return False, (
        f"{len(failing)} trace(s) below {bar:.0f}% schema bar "
        f"(worst: {worst[0][:8]} at {worst[1]:.0f}%)"
    )


# ---------------------------------------------------------------------------
# Top fixes — for each non-passing check, project the score lift if every
# trace where it currently fails would suddenly pass.
# ---------------------------------------------------------------------------


@dataclass
class TopFix:
    persona: str  # internal name
    check_name: str  # human-readable description, prefix stripped
    affected_traces: int
    score_lift: float  # estimated avg-points the persona would gain


_PREFIX_RE = re.compile(r"^[A-Z]+\d+:\s*")


def _clean_check_name(name: str) -> str:
    """Strip the 'T6: ' / 'RL1: ' / 'C24: ' prefix from check names."""
    return _PREFIX_RE.sub("", name).strip()


def derive_top_fixes(batch: "BatchAssessment", n: int = 3) -> list[TopFix]:
    """Rank actionable fixes by projected score impact across the batch.

    For each (persona, check) pair, sum up the per-trace lift the check
    would deliver if it went from failing to passing. Per-trace lift is
    ``weight / total_weight_for_that_trace * 100``. The persona-average
    lift is then ``sum_of_lifts / trace_count_in_persona``.
    """
    # accumulator: (persona, check_name) -> [lift_per_trace, …]
    bucket: dict[tuple[str, str], list[float]] = defaultdict(list)
    persona_traces: dict[str, int] = defaultdict(int)

    for a in batch.assessments:
        for persona_name, ps in a.persona_scores.items():
            if ps.skipped:
                continue
            persona_traces[persona_name] += 1
            active = [i for i in ps.items if not i.skipped]
            total_w = sum(i.weight for i in active) or 1.0
            for item in active:
                if item.passed:
                    continue
                # Lift if this single check went from current score to 1.0
                gap = max(0.0, 1.0 - item.score)
                per_trace_lift = gap * item.weight / total_w * 100
                bucket[(persona_name, item.name)].append(per_trace_lift)

    fixes: list[TopFix] = []
    for (persona, check), lifts in bucket.items():
        n_traces = persona_traces.get(persona, 1)
        avg_lift = sum(lifts) / n_traces
        if avg_lift < 0.5:  # discard sub-half-point noise
            continue
        fixes.append(
            TopFix(
                persona=persona,
                check_name=_clean_check_name(check),
                affected_traces=len(lifts),
                score_lift=avg_lift,
            )
        )
    fixes.sort(key=lambda f: f.score_lift, reverse=True)
    return fixes[:n]


# ---------------------------------------------------------------------------
# Top offenders — lowest-scoring traces by overall utility.
# ---------------------------------------------------------------------------


@dataclass
class TopOffender:
    trace_id: str
    overall: float
    worst_persona: str  # internal name
    worst_score: float


def derive_top_offenders(batch: "BatchAssessment", n: int = 3) -> list[TopOffender]:
    rows: list[TopOffender] = []
    for a in batch.assessments:
        active = [(name, ps) for name, ps in a.persona_scores.items() if not ps.skipped]
        if not active:
            continue
        worst = min(active, key=lambda x: x[1].total_score)
        rows.append(
            TopOffender(
                trace_id=a.trace_id,
                overall=a.overall_utility,
                worst_persona=worst[0],
                worst_score=worst[1].total_score,
            )
        )
    rows.sort(key=lambda o: o.overall)
    return rows[:n]


# ---------------------------------------------------------------------------
# Single entry point for the CLI human view.
# ---------------------------------------------------------------------------


def format_assessment(
    summary: "QualitySummary",
    batch: "BatchAssessment",
    *,
    header: str,
    elapsed_seconds: float | None = None,
    remote_delta: dict[str, float] | None = None,
) -> str:
    """Render the full human-facing block for `opentraces assess`.

    ``remote_delta`` maps internal persona name -> (local - remote) in
    points; values are appended to each row when present.
    """
    lines: list[str] = []

    elapsed = f" · scored in {elapsed_seconds:.1f}s" if elapsed_seconds else ""
    lines.append(f"{header} · {summary.trace_count} traces{elapsed}")
    lines.append("")

    # Persona rows. Display order matches LABELS, but only includes scored personas.
    name_width = max((len(display_name(n)) for n in summary.persona_scores), default=8)
    for internal in LABELS:
        ps = summary.persona_scores.get(internal)
        if ps is None:
            continue
        label = display_name(internal).ljust(name_width)
        mark = "✓" if ps.average >= 70 else ("·" if ps.average >= 50 else "✗")
        delta_str = ""
        if remote_delta and internal in remote_delta:
            d = remote_delta[internal]
            sign = "+" if d > 0 else ""
            delta_str = f"  ({sign}{d:.1f} vs remote)"
        lines.append(f"  {label}  {ps.average:5.1f}%  {mark}{delta_str}")

    lines.append("")
    lines.append(f"  Overall: {summary.overall_utility:.1f}%")

    safe, reason = safe_to_publish(batch)
    verdict = "yes" if safe else "no"
    lines.append(f"  Safe to publish: {verdict}  ({reason})")

    fixes = derive_top_fixes(batch)
    if fixes:
        lines.append("")
        lines.append("Top fixes (by score impact):")
        for i, f in enumerate(fixes, 1):
            label = display_name(f.persona)
            lines.append(
                f"  {i}. {f.check_name}"
                f"  →  {label} +{f.score_lift:.1f}pts ({f.affected_traces} traces)"
            )

    offenders = derive_top_offenders(batch)
    if offenders and len(batch.assessments) > 1:
        lines.append("")
        parts = [
            f"{o.trace_id[:8]} ({o.overall:.0f}%, {display_name(o.worst_persona)} {o.worst_score:.0f}%)"
            for o in offenders
        ]
        lines.append(f"Lowest scoring: {'  '.join(parts)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Glossary — printed by `opentraces assess --explain`. Kept tiny on purpose.
# ---------------------------------------------------------------------------


def format_glossary() -> str:
    lines = ["Quality dimensions:"]
    for internal, (label, tagline) in LABELS.items():
        lines.append(f"  {label:<13} {tagline}")
    lines.append("")
    lines.append('"Safe to publish" = every trace clears the schema/secrets bar.')
    lines.append('"Overall" = mean of dimension averages.')
    return "\n".join(lines)

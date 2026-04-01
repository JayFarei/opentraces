"""Quality gates for trace assessment.

Defines pass/fail thresholds per persona so the CLI can block
uploads that fail the quality bar. Tests import these thresholds
instead of hardcoding them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import BatchAssessment


@dataclass
class PersonaThreshold:
    """Threshold for one persona."""

    persona: str
    min_individual: float | None  # None = no minimum (session-dependent)
    min_average: float


DEFAULT_THRESHOLDS = [
    PersonaThreshold("conformance", 70.0, 80.0),
    PersonaThreshold("training", 40.0, 45.0),  # Lowered: redacted thinking (Opus 4.6) gives 0.5 credit
    PersonaThreshold("rl", None, 40.0),
    PersonaThreshold("analytics", 60.0, 70.0),
    PersonaThreshold("domain", 45.0, 55.0),
]

PRESERVATION_THRESHOLD = 0.85


@dataclass
class GateResult:
    """Result of checking a batch against quality gates."""

    passed: bool
    failures: list[str] = field(default_factory=list)


def check_gate(batch: BatchAssessment, thresholds: list[PersonaThreshold] | None = None, preservation_threshold: float | None = None) -> GateResult:
    """Check a BatchAssessment against quality gates.

    Args:
        batch: BatchAssessment from assess_batch()
        thresholds: list of PersonaThreshold, defaults to DEFAULT_THRESHOLDS
        preservation_threshold: float, defaults to PRESERVATION_THRESHOLD

    Returns:
        GateResult with passed=True if all gates pass, failures list otherwise.
    """
    if thresholds is None:
        thresholds = DEFAULT_THRESHOLDS
    if preservation_threshold is None:
        preservation_threshold = PRESERVATION_THRESHOLD

    failures = []

    for threshold in thresholds:
        # Skip gate check if no traces had active (non-skipped) checks for this persona
        if threshold.persona not in batch.persona_averages:
            continue

        # Check average
        avg = batch.persona_averages[threshold.persona]
        if avg < threshold.min_average:
            failures.append(
                f"{threshold.persona} average {avg:.1f}% below"
                f" {threshold.min_average}% minimum"
            )

        # Check individual minimums (skipped traces excluded)
        if threshold.min_individual is not None:
            for assessment in batch.assessments:
                ps = assessment.persona_scores.get(threshold.persona)
                if ps and not ps.skipped and ps.total_score < threshold.min_individual:
                    failures.append(
                        f"Trace {assessment.trace_id[:8]} {threshold.persona} "
                        f"{ps.total_score:.1f}% below"
                        f" {threshold.min_individual}% minimum"
                    )

    # Check preservation
    if batch.preservation_average is not None:
        if batch.preservation_average < preservation_threshold:
            failures.append(
                f"Preservation average {batch.preservation_average:.0%} "
                f"below {preservation_threshold:.0%} threshold"
            )

    return GateResult(passed=len(failures) == 0, failures=failures)

"""QualitySummary: shared contract for quality scores across CLI, upload, and README.

Serializes to quality.json (machine truth), YAML frontmatter (HF-searchable),
and CLI display (human-readable). Single source for all quality metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .gates import GateResult


@dataclass
class PersonaScoreSummary:
    """Aggregate scores for one persona across a batch."""

    average: float  # 0-100
    min: float  # 0-100
    max: float  # 0-100


@dataclass
class QualitySummary:
    """Aggregate quality assessment for a batch or dataset.

    This is the shared contract between CLI display, quality.json sidecar,
    and README YAML frontmatter. All serialization flows through here.
    """

    scorer_version: str  # schema version, e.g. "0.2.0"
    scoring_mode: str  # "deterministic" | "hybrid"
    judge_model: str | None  # "haiku" | "sonnet" | "opus" | None
    assessed_at: str  # ISO 8601
    trace_count: int
    persona_scores: dict[str, PersonaScoreSummary] = field(default_factory=dict)
    overall_utility: float = 0.0
    gate_passed: bool = True
    gate_failures: list[str] = field(default_factory=list)
    preservation_average: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for quality.json sidecar."""
        result: dict[str, Any] = {
            "scorer_version": self.scorer_version,
            "scoring_mode": self.scoring_mode,
            "assessed_at": self.assessed_at,
            "trace_count": self.trace_count,
            "persona_scores": {
                name: {
                    "average": round(ps.average, 1),
                    "min": round(ps.min, 1),
                    "max": round(ps.max, 1),
                }
                for name, ps in self.persona_scores.items()
            },
            "overall_utility": round(self.overall_utility, 1),
            "gate_status": "passing" if self.gate_passed else "failing",
            "gate_failures": self.gate_failures if not self.gate_passed else [],
        }
        if self.judge_model is not None:
            result["judge_model"] = self.judge_model
        if self.preservation_average is not None:
            result["preservation_average"] = round(self.preservation_average, 2)
        return result

    def to_yaml_frontmatter(self) -> dict[str, Any]:
        """Serialize to YAML frontmatter keys for HF dataset card.

        Returns both flat top-level keys (HF-searchable) and a nested
        opentraces_quality block for full detail.
        """
        flat: dict[str, Any] = {}
        # Flat top-level keys for HF search
        for name, ps in self.persona_scores.items():
            flat[f"{name}_score"] = round(ps.average, 1)
        flat["overall_quality"] = round(self.overall_utility, 1)

        # Nested block for full detail
        flat["opentraces_quality"] = self.to_dict()
        return flat

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QualitySummary:
        """Deserialize from quality.json dict."""
        persona_scores = {}
        for name, ps_data in data.get("persona_scores", {}).items():
            persona_scores[name] = PersonaScoreSummary(
                average=ps_data.get("average", 0.0),
                min=ps_data.get("min", 0.0),
                max=ps_data.get("max", 0.0),
            )

        gate_status = data.get("gate_status", "passing")
        return cls(
            scorer_version=data.get("scorer_version", "unknown"),
            scoring_mode=data.get("scoring_mode", "deterministic"),
            judge_model=data.get("judge_model"),
            assessed_at=data.get("assessed_at", ""),
            trace_count=data.get("trace_count", 0),
            persona_scores=persona_scores,
            overall_utility=data.get("overall_utility", 0.0),
            gate_passed=gate_status == "passing",
            gate_failures=data.get("gate_failures", []),
            preservation_average=data.get("preservation_average"),
        )


def build_summary(
    batch: Any,  # BatchAssessment, Any to avoid circular import
    gate_result: Any,  # GateResult
    mode: str = "deterministic",
    judge_model: str | None = None,
) -> QualitySummary:
    """Build a QualitySummary from a BatchAssessment and GateResult.

    Args:
        batch: BatchAssessment from assess_batch()
        gate_result: GateResult from check_gate()
        mode: "deterministic" or "hybrid"
        judge_model: model name if judge was used
    """
    from opentraces_schema.version import SCHEMA_VERSION

    # Build per-persona summaries with min/max
    persona_scores: dict[str, PersonaScoreSummary] = {}
    for persona_name, avg in batch.persona_averages.items():
        scores = []
        for assessment in batch.assessments:
            ps = assessment.persona_scores.get(persona_name)
            if ps:
                scores.append(ps.total_score)
        if scores:
            persona_scores[persona_name] = PersonaScoreSummary(
                average=avg,
                min=min(scores),
                max=max(scores),
            )

    # Overall utility: mean of persona averages
    overall = 0.0
    if batch.persona_averages:
        overall = sum(batch.persona_averages.values()) / len(batch.persona_averages)

    return QualitySummary(
        scorer_version=SCHEMA_VERSION,
        scoring_mode=mode,
        judge_model=judge_model,
        assessed_at=datetime.now(timezone.utc).isoformat(),
        trace_count=len(batch.assessments),
        persona_scores=persona_scores,
        overall_utility=overall,
        gate_passed=gate_result.passed,
        gate_failures=gate_result.failures,
        preservation_average=batch.preservation_average,
    )

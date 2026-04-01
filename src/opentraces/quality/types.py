"""Quality assessment types for trace conformance scoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class RubricItem:
    """One item in the conformance rubric."""
    name: str
    category: str  # schema, parser, security, enrichment, structure
    weight: float  # 0.0-1.0, how important this check is
    passed: bool = False
    score: float = 0.0  # 0.0-1.0
    evidence: str = ""
    note: str = ""
    skipped: bool = False  # True = N/A for this trace type; excluded from weighted average


@dataclass
class RubricReport:
    """Full rubric report for one trace."""
    trace_id: str
    session_id: str
    task_description: str
    items: list[RubricItem] = field(default_factory=list)

    @property
    def total_score(self) -> float:
        active = [i for i in self.items if not i.skipped]
        total_weight = sum(i.weight for i in active)
        if total_weight == 0:
            return 0.0
        weighted = sum(i.score * i.weight for i in active)
        return round(weighted / total_weight * 100, 1)

    @property
    def pass_rate(self) -> float:
        active = [i for i in self.items if not i.skipped]
        if not active:
            return 0.0
        return round(sum(1 for i in active if i.passed) / len(active) * 100, 1)

    @property
    def category_scores(self) -> dict[str, float]:
        categories: dict[str, list[RubricItem]] = {}
        for item in self.items:
            if not item.skipped:
                categories.setdefault(item.category, []).append(item)
        return {
            cat: round(
                sum(i.score * i.weight for i in items) /
                max(sum(i.weight for i in items), 0.001) * 100, 1
            )
            for cat, items in categories.items()
        }


@dataclass
class CheckResult:
    """Result of a single quality check."""
    passed: bool
    score: float
    evidence: str
    note: str = ""
    skipped: bool = False  # True = check does not apply to this trace type


@dataclass
class CheckDef:
    """Definition of a single quality check."""
    name: str
    category: str
    weight: float
    check: Callable  # (record: TraceRecord, raw_data: dict) -> CheckResult


@dataclass
class PersonaDef:
    """Definition of a quality assessment persona."""
    name: str
    description: str
    checks: list[CheckDef] = field(default_factory=list)

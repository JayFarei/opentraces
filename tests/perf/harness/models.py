from __future__ import annotations

import json
import math
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_DIR = REPO_ROOT / "tests" / "perf" / "scenarios"
BUDGETS_PATH = REPO_ROOT / "tests" / "perf" / "budgets.toml"
_LANE_ORDER = {"smoke": 0, "full": 1, "nightly": 2}


@dataclass(frozen=True)
class PerfBudget:
    max_cold_ms: float | None = None
    max_p50_ms: float | None = None
    max_p95_ms: float | None = None
    max_peak_rss_mb: float | None = None
    max_python_heap_peak_mb: float | None = None
    max_rss_delta_mb: float | None = None
    max_initial_p95_ms: float | None = None
    max_update_p95_ms: float | None = None
    max_peak_heap_used_mb: float | None = None
    max_retained_heap_used_mb: float | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "PerfBudget":
        data = data or {}
        return cls(
            max_cold_ms=_float_or_none(data.get("max_cold_ms")),
            max_p50_ms=_float_or_none(data.get("max_p50_ms")),
            max_p95_ms=_float_or_none(data.get("max_p95_ms")),
            max_peak_rss_mb=_float_or_none(data.get("max_peak_rss_mb")),
            max_python_heap_peak_mb=_float_or_none(data.get("max_python_heap_peak_mb")),
            max_rss_delta_mb=_float_or_none(data.get("max_rss_delta_mb")),
            max_initial_p95_ms=_float_or_none(data.get("max_initial_p95_ms")),
            max_update_p95_ms=_float_or_none(data.get("max_update_p95_ms")),
            max_peak_heap_used_mb=_float_or_none(data.get("max_peak_heap_used_mb")),
            max_retained_heap_used_mb=_float_or_none(
                data.get("max_retained_heap_used_mb")
            ),
        )

    def to_mapping(self) -> dict[str, float]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass(frozen=True)
class PerfScenario:
    name: str
    domain: str
    lane: str
    adapter: str
    target: str
    fixture: str
    tier: str
    repetitions: int
    warmups: int
    params: dict[str, Any] = field(default_factory=dict)
    budget: PerfBudget = field(default_factory=PerfBudget)
    source_file: Path | None = None


@dataclass
class PerfSample:
    duration_ms: float
    rss_before_mb: float | None = None
    rss_after_mb: float | None = None
    peak_rss_mb: float | None = None
    python_heap_peak_mb: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def rss_delta_mb(self) -> float | None:
        if self.rss_before_mb is None or self.rss_after_mb is None:
            return None
        return self.rss_after_mb - self.rss_before_mb


@dataclass
class PerfResult:
    samples: list[PerfSample]
    cold_ms: float
    p50_ms: float
    p95_ms: float
    mean_ms: float
    peak_rss_mb: float | None
    python_heap_peak_mb: float | None
    max_rss_delta_mb: float | None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cold_ms": self.cold_ms,
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "mean_ms": self.mean_ms,
            "peak_rss_mb": self.peak_rss_mb,
            "python_heap_peak_mb": self.python_heap_peak_mb,
            "max_rss_delta_mb": self.max_rss_delta_mb,
            "samples": [
                {
                    "duration_ms": s.duration_ms,
                    "rss_before_mb": s.rss_before_mb,
                    "rss_after_mb": s.rss_after_mb,
                    "peak_rss_mb": s.peak_rss_mb,
                    "python_heap_peak_mb": s.python_heap_peak_mb,
                    "rss_delta_mb": s.rss_delta_mb,
                    "metadata": s.metadata,
                }
                for s in self.samples
            ],
            "extra": self.extra,
        }


def load_budgets(path: Path = BUDGETS_PATH) -> dict[str, PerfBudget]:
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    return {name: PerfBudget.from_mapping(section) for name, section in raw.items()}


def write_budgets(budgets: dict[str, PerfBudget], path: Path = BUDGETS_PATH) -> None:
    lines: list[str] = []
    for name in sorted(budgets):
        lines.append(f"[{name}]")
        section = budgets[name].to_mapping()
        for key in sorted(section):
            lines.append(f"{key} = {json.dumps(_round_budget(section[key]))}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n")


def update_budget(budgets: dict[str, PerfBudget], scenario: PerfScenario, result: PerfResult) -> dict[str, PerfBudget]:
    current = budgets.get(scenario.name, PerfBudget())
    proposed = PerfBudget(
        max_cold_ms=_max_budget(current.max_cold_ms, result.cold_ms),
        max_p50_ms=_max_budget(current.max_p50_ms, result.p50_ms),
        max_p95_ms=_max_budget(current.max_p95_ms, result.p95_ms),
        max_peak_rss_mb=_max_budget(current.max_peak_rss_mb, result.peak_rss_mb),
        max_python_heap_peak_mb=_max_budget(
            current.max_python_heap_peak_mb,
            result.python_heap_peak_mb,
        ),
        max_rss_delta_mb=_max_budget(current.max_rss_delta_mb, result.max_rss_delta_mb),
        max_initial_p95_ms=_max_budget(
            current.max_initial_p95_ms,
            _extra_metric(result, "initial_p95_ms"),
        ),
        max_update_p95_ms=_max_budget(
            current.max_update_p95_ms,
            _extra_metric(result, "update_p95_ms"),
        ),
        max_peak_heap_used_mb=_max_budget(
            current.max_peak_heap_used_mb,
            _extra_metric(result, "peak_heap_used_mb"),
        ),
        max_retained_heap_used_mb=_max_budget(
            current.max_retained_heap_used_mb,
            _extra_metric(result, "retained_heap_used_mb"),
        ),
    )
    updated = dict(budgets)
    updated[scenario.name] = proposed
    return updated


def load_scenarios(domain: str, lane: str, *, scenarios_dir: Path = SCENARIOS_DIR) -> list[PerfScenario]:
    budgets = load_budgets()
    selected: list[PerfScenario] = []
    for path in sorted(scenarios_dir.glob("*.toml")):
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
        if raw.get("domain") != domain:
            continue
        scenario = PerfScenario(
            name=raw.get("name") or path.stem,
            domain=raw["domain"],
            lane=raw.get("lane", "smoke"),
            adapter=raw["adapter"],
            target=raw["target"],
            fixture=raw["fixture"],
            tier=raw["tier"],
            repetitions=int(raw.get("repetitions", 3)),
            warmups=int(raw.get("warmups", 0)),
            params=dict(raw.get("params") or {}),
            budget=budgets.get(raw.get("name") or path.stem, PerfBudget()),
            source_file=path,
        )
        if _LANE_ORDER[scenario.lane] <= _LANE_ORDER[lane]:
            selected.append(scenario)
    return selected


def assert_budget(scenario: PerfScenario, result: PerfResult) -> None:
    failures: list[str] = []
    budget = scenario.budget
    _check_metric(failures, "cold_ms", budget.max_cold_ms, result.cold_ms)
    _check_metric(failures, "p50_ms", budget.max_p50_ms, result.p50_ms)
    _check_metric(failures, "p95_ms", budget.max_p95_ms, result.p95_ms)
    if scenario.adapter != "python":
        _check_metric(failures, "peak_rss_mb", budget.max_peak_rss_mb, result.peak_rss_mb)
    _check_metric(
        failures,
        "python_heap_peak_mb",
        budget.max_python_heap_peak_mb,
        result.python_heap_peak_mb,
    )
    _check_metric(
        failures,
        "max_rss_delta_mb",
        budget.max_rss_delta_mb,
        result.max_rss_delta_mb,
    )
    _check_metric(
        failures,
        "initial_p95_ms",
        budget.max_initial_p95_ms,
        _extra_metric(result, "initial_p95_ms"),
    )
    _check_metric(
        failures,
        "update_p95_ms",
        budget.max_update_p95_ms,
        _extra_metric(result, "update_p95_ms"),
    )
    _check_metric(
        failures,
        "peak_heap_used_mb",
        budget.max_peak_heap_used_mb,
        _extra_metric(result, "peak_heap_used_mb"),
    )
    _check_metric(
        failures,
        "retained_heap_used_mb",
        budget.max_retained_heap_used_mb,
        _extra_metric(result, "retained_heap_used_mb"),
    )
    if failures:
        joined = "; ".join(failures)
        raise AssertionError(f"{scenario.name} exceeded its budget: {joined}")


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    idx = max(0, math.ceil(len(ordered) * q) - 1)
    return float(ordered[idx])


def _check_metric(failures: list[str], label: str, limit: float | None, actual: float | None) -> None:
    if limit is None or actual is None:
        return
    if actual > limit:
        failures.append(f"{label} <= {limit:.2f} expected, got {actual:.2f}")


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _round_budget(value: float) -> float:
    return round(value, 2)


def _max_budget(current: float | None, actual: float | None) -> float | None:
    if actual is None:
        return current
    suggested = _round_budget(actual * 1.25)
    if current is None:
        return suggested
    return _round_budget(max(current, suggested))


def _extra_metric(result: PerfResult, key: str) -> float | None:
    value = result.extra.get(key)
    return _float_or_none(value)

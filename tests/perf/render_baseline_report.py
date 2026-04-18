from __future__ import annotations

import json
import tomllib
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SUMMARY_PATH = REPO_ROOT / "tests" / "perf" / "artifacts" / "latest" / "summary.json"
JOURNEYS_PATH = REPO_ROOT / "tests" / "perf" / "journeys.toml"
BUDGETS_PATH = REPO_ROOT / "tests" / "perf" / "budgets.toml"
OUTPUT_PATH = REPO_ROOT / "tests" / "perf" / "BASELINE.md"


def _fmt(value: float | int | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, int):
        return str(value)
    return f"{value:.2f}"


def _load_summary() -> dict[str, dict]:
    return json.loads(SUMMARY_PATH.read_text())


def _load_journeys() -> list[dict]:
    with JOURNEYS_PATH.open("rb") as handle:
        data = tomllib.load(handle)
    return list(data["journeys"])


def _load_budgets() -> dict[str, dict]:
    with BUDGETS_PATH.open("rb") as handle:
        return tomllib.load(handle)


def _scenario_to_journeys(journeys: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for journey in journeys:
        for scenario in journey.get("perf_scenarios", []):
            out[scenario].append(journey["id"])
    return out


def build_report() -> str:
    summary = _load_summary()
    journeys = _load_journeys()
    budgets = _load_budgets()
    scenario_journeys = _scenario_to_journeys(journeys)

    profiled = [journey for journey in journeys if journey["profiled"]]
    non_profiled = [journey for journey in journeys if not journey["profiled"]]

    lines: list[str] = []
    lines.append("# Performance Baseline")
    lines.append("")
    lines.append("Generated from `tests/perf/artifacts/latest/summary.json`.")
    lines.append("")
    lines.append("## Profiled Journeys")
    lines.append("")
    lines.append("| Journey | Priority | Perf Scenarios | User Smoke | Live |")
    lines.append("|---|---:|---:|---:|---:|")
    for journey in profiled:
        lines.append(
            "| "
            + " | ".join(
                [
                    journey["id"],
                    journey["priority"],
                    str(len(journey.get("perf_scenarios", []))),
                    str(len(journey.get("user_smoke_tests", []))),
                    str(len(journey.get("live_tests", []))),
                ]
            )
            + " |"
        )

    lines.append("")
    lines.append("## Scenario Baselines")
    lines.append("")
    lines.append(
        "| Scenario | Journey(s) | Domain | Target | p50 ms | p95 ms | Cold ms | Peak RSS MB | Py Heap MB | RSS Delta MB | Budget p95 |"
    )
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|")

    for scenario_name in sorted(summary):
        row = summary[scenario_name]
        journey_ids = ", ".join(sorted(scenario_journeys.get(scenario_name, []))) or "-"
        budget = budgets.get(scenario_name, {})
        lines.append(
            "| "
            + " | ".join(
                [
                    scenario_name,
                    journey_ids,
                    row["domain"],
                    row["target"],
                    _fmt(row.get("p50_ms")),
                    _fmt(row.get("p95_ms")),
                    _fmt(row.get("cold_ms")),
                    _fmt(row.get("peak_rss_mb")),
                    _fmt(row.get("python_heap_peak_mb")),
                    _fmt(row.get("max_rss_delta_mb")),
                    _fmt(budget.get("max_p95_ms")),
                ]
            )
            + " |"
        )

    lines.append("")
    lines.append("## Viewer-Specific Baselines")
    lines.append("")
    lines.append("| Scenario | Initial p95 ms | Update p95 ms | Peak Heap Used MB | Retained Heap MB |")
    lines.append("|---|---:|---:|---:|---:|")
    for scenario_name in sorted(summary):
        row = summary[scenario_name]
        extra = row.get("extra", {})
        if "initial_p95_ms" not in extra and "update_p95_ms" not in extra:
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    scenario_name,
                    _fmt(extra.get("initial_p95_ms")),
                    _fmt(extra.get("update_p95_ms")),
                    _fmt(extra.get("peak_heap_used_mb")),
                    _fmt(extra.get("retained_heap_used_mb")),
                ]
            )
            + " |"
        )

    lines.append("")
    lines.append("## Mapped But Not Yet Profiled")
    lines.append("")
    lines.append("| Journey | Why Not In Perf Baseline Yet |")
    lines.append("|---|---|")
    for journey in non_profiled:
        lines.append(f"| {journey['id']} | {journey['notes']} |")

    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- `push-smoke` measures local push-path overhead with a fake uploader. Real private-HF success remains an opt-in live integration test.")
    lines.append("- `tmux` and `agent-browser` remain separate opt-in regression smokes; they are not included in the perf latency tables because they are environment-heavy.")
    lines.append("- Budgets in `tests/perf/budgets.toml` are current smoke-lane regression thresholds, not ideal targets.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    OUTPUT_PATH.write_text(build_report())
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()

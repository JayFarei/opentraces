from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.perf.conftest import PerfRuntime
from tests.perf.harness.measure import measure_python_factory, write_artifact
from tests.perf.harness.models import assert_budget, load_budgets, load_scenarios, update_budget, write_budgets
from tests.perf.harness.python import build_python_callable


@pytest.mark.perf
@pytest.mark.parametrize("scenario", load_scenarios("publish", lane="nightly"), ids=lambda s: s.name)
def test_publish_perf(
    scenario,
    perf_runtime: PerfRuntime,
    perf_home: Path,
    tmp_path: Path,
) -> None:
    if not perf_runtime.includes(scenario.lane):
        pytest.skip(f"{scenario.name} requires lane {scenario.lane}")

    def factory(run_idx: int):
        base_dir = tmp_path / scenario.name / f"run-{run_idx:02d}"
        base_dir.mkdir(parents=True, exist_ok=True)
        return build_python_callable(base_dir, perf_home, scenario)

    result = measure_python_factory(scenario, factory)
    budgets = load_budgets()
    budget_updated = False
    if perf_runtime.update_baselines:
        budgets = update_budget(budgets, scenario, result)
        write_budgets(budgets)
        budget_updated = True
        scenario = replace(scenario, budget=budgets[scenario.name])
    write_artifact(perf_runtime.artifacts_dir, scenario, result, budget_updated=budget_updated)
    assert_budget(scenario, result)

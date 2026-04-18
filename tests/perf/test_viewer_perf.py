from __future__ import annotations

from dataclasses import replace
import pytest

from tests.perf.conftest import PerfRuntime
from tests.perf.harness.measure import write_artifact
from tests.perf.harness.models import (
    assert_budget,
    load_budgets,
    load_scenarios,
    update_budget,
    write_budgets,
)
from tests.perf.harness.viewer import run_viewer_scenario


@pytest.mark.perf
@pytest.mark.parametrize("scenario", load_scenarios("viewer", lane="nightly"), ids=lambda s: s.name)
def test_viewer_perf(scenario, perf_runtime: PerfRuntime) -> None:
    if not perf_runtime.includes(scenario.lane):
        pytest.skip(f"{scenario.name} requires lane {scenario.lane}")

    result = run_viewer_scenario(scenario)
    budgets = load_budgets()
    budget_updated = False
    if perf_runtime.update_baselines:
        budgets = update_budget(budgets, scenario, result)
        write_budgets(budgets)
        budget_updated = True
        scenario = replace(scenario, budget=budgets[scenario.name])
    write_artifact(perf_runtime.artifacts_dir, scenario, result, budget_updated=budget_updated)
    assert_budget(scenario, result)

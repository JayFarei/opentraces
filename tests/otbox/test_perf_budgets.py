"""Perf budgets (otbox 2.0 phase 6, first tranche).

Two real regressions shipped in this class: a ~200s/commit post-commit hang
and a ~7.5GB hook OOM. Nothing stood guard. This tranche puts a STANDING
latency ceiling on the user-facing read commands against a real captured
world, computed from the driver's own wall-clock measurement.

HONEST SCOPE: these run on the small captured world, so the ceilings are
catastrophic-regression catches (a command going from ~1s to tens of
seconds — i.e. the 200s-hang shape), not tight SLAs. The p95 latency budgets
AND the RSS ceilings the design calls for need the 50K-event scale world +
a portable RSS measurement; both are the focused scale-world session. This
file is the cheap, CI-portable floor that exists NOW rather than after that.
"""

from __future__ import annotations

import json

import pytest

from .drivers import get_driver

# (argv, ceiling_seconds). Ceilings are ~15-30x normal on this small world —
# comfortably above noise, well below the 200s regression shape. Human-set
# round numbers; there is no --update-baselines ratchet (the design forbids
# it for gating budgets — a laptop ratchet is how the 200s hang slipped).
BUDGETS = [
    (["bucket", "status", "--json"], 20.0),
    (["trace", "query", "--since", "3650d", "--json"], 30.0),
    (["trace", "index", "rebuild"], 30.0),
    (["trail", "graph", "--json"], 20.0),
]


@pytest.fixture(scope="module")
def driver():
    return get_driver("local")


@pytest.fixture(scope="module")
def captured_box(driver):
    from .checkpoints import resolve_checkpoint

    cp = resolve_checkpoint(driver, "c-captured-real-session")
    yield driver, cp.box
    if cp.box.root.exists():
        driver.teardown(cp.box)


@pytest.mark.parametrize("argv,ceiling", BUDGETS, ids=lambda x: " ".join(x) if isinstance(x, list) else "")
def test_read_command_within_latency_budget(captured_box, argv, ceiling):
    driver, box = captured_box
    res = driver.exec(box, [*driver.cli_argv(box), *argv])
    # A crash is not a latency pass — the command must actually run.
    assert res.returncode == 0, f"{' '.join(argv)} failed rc={res.returncode}: {res.stderr[:200]}"
    assert res.duration_s <= ceiling, (
        f"{' '.join(argv)} took {res.duration_s:.1f}s, budget {ceiling:.0f}s "
        f"(catastrophic-regression ceiling on the small captured world — a "
        f"breach here is the 200s-hang shape, not an SLA miss)"
    )


def test_steady_state_search_latency_budget(captured_box):
    """U7 default-lane search-latency budget — regression-class detection.

    The Trace Spotlight regression class: ``trace query`` accidentally
    forcing a full reindex on every call was ~90s against a real bucket
    (issue #22 / PR #24). On this small captured world the steady-state
    read-only query measures ~0.2s; a CONSERVATIVE 5s ceiling stays
    flake-proof on CI while an accidental full-reindex-on-query (tens of
    seconds even at this scale) trips it.

    Scale-world latency budgets (tight p50/p95 SLAs against the 50K-event
    world) are tracked under #40/#41 — the search-perf lane owns the index
    internals. This test treats search as a black box and must NOT touch
    index code.
    """
    driver, box = captured_box
    cli = driver.cli_argv(box)
    # Warm-up: the FIRST query may legitimately self-heal the snapshot once
    # (issue #30); the budget governs the steady-state read path.
    warm = driver.exec(box, [*cli, "trace", "query", "farewell", "--json"])
    assert warm.returncode == 0, f"warm-up query failed: {warm.stderr[:200]}"

    res = driver.exec(box, [*cli, "trace", "query", "farewell", "--json"])

    assert res.returncode == 0, f"steady-state query failed: {res.stderr[:200]}"
    diagnostics = json.loads(res.stdout)["search_diagnostics"]
    # Bind the measurement to the read-only path: a steady-state query that
    # reports rebuilt_index=True IS the regression this budget exists for.
    assert diagnostics["rebuilt_index"] is False
    assert res.duration_s <= 5.0, (
        f"steady-state trace query took {res.duration_s:.2f}s, budget 5s on "
        f"the small captured world — the full-reindex-on-query shape, not "
        f"an SLA miss"
    )


def test_budgets_cover_the_regression_shaped_commands():
    """The two shipped regressions were in trace-query sync and post-commit
    reconcile; both read the event log + index. Assert those surfaces are
    budgeted so a future regression there trips a gate."""
    budgeted = {tuple(a[:2]) for a, _ in BUDGETS}
    assert ("trace", "query") in budgeted
    assert ("trace", "index") in budgeted
    assert ("bucket", "status") in budgeted

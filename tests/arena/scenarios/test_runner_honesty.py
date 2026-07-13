"""Executable red/green controls for the bench runner itself."""

from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("OT_BENCH_SCENARIOS") != "1",
    reason="bench scenarios run through `opentraces bench run`",
)


def python_is_available(run):
    observed = run.terminal.exec("python3", "--version")
    assert observed.returncode == 0, observed.stderr
    assert "Python 3" in observed.stdout
    return {"evidence_refs": [observed.result_ref]}


def test_runner_reports_pass(bench):
    """The runner records a passing verifier as a pass."""

    with bench.run(app_state="base-only") as run:
        run.verify(python_is_available)


def test_runner_reports_fail(bench):
    """The runner records a false product assertion as a fail."""

    with bench.run(app_state="base-only"):
        assert False, "forced functional red"


def test_runner_reports_named_skip(bench):
    """The runner records an absent named prerequisite as a skip."""

    with bench.run(app_state="base-only") as run:
        run.skip("absent_prerequisite", "the self-test names this absent prerequisite")


def test_runner_reports_machinery_error(bench):
    """The runner records machinery that prevents adjudication as an error."""

    with bench.run(app_state="base-only"):
        raise RuntimeError("forced machinery red")

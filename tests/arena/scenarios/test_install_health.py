"""Launch scenario 1 for bench.v0."""

from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("OT_BENCH_SCENARIOS") != "1",
    reason="bench scenarios run through `opentraces bench run`",
)


def installed_cli_reports_health(run):
    observed = run.terminal.exec("opentraces", "doctor", "--json", timeout=120)
    assert observed.returncode == 0, (
        f"doctor exited {observed.returncode}: {observed.stderr or 'no stderr'}"
    )
    payload = observed.json
    assert payload["status"] == "ok"
    assert isinstance(payload["schema_version"], str) and payload["schema_version"]
    doctor = payload["doctor"]
    assert isinstance(doctor["cli"]["installed_version"], str)
    assert doctor["cli"]["installed_version"]
    assert isinstance(doctor["security_version"], str) and doctor["security_version"]
    tools = doctor["security"]["tools"]
    assert isinstance(tools, list) and tools
    assert all(isinstance(tool.get("name"), str) and tool.get("name") for tool in tools)
    assert all(isinstance(tool.get("state"), str) and tool.get("state") for tool in tools)
    return {"evidence_refs": [observed.result_ref]}


def test_install_is_healthy_on_a_fresh_box(bench):
    """Install is healthy on a fresh box.

    The product is installed from wheels built from the checked-out HEAD. The
    verifier requires doctor rc=0. Doctor rc=3 includes hard unhealthy states,
    so accepting its JSON envelope would turn a broken install into a false green.
    """

    with bench.run(app_state="install-only") as run:
        run.verify(installed_cli_reports_health)

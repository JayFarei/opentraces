"""Shared fixtures for plan-043 integration tests.

Most of the work here is marker registration and ergonomic helpers for
driving scenario TOMLs. The real heavy lifting (tmux orchestration, audit
history construction, git blame) lives in `opentraces.enrichment.git.attribution`
and in the scenario runner under `test_attribution_scenarios.py`.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


SCENARIOS_DIR = Path(__file__).parent / "scenarios"


def pytest_collection_modifyitems(config, items):
    """Auto-skip tests marked `real_repl` unless OT_REAL_REPL=1 in the env.

    Real-REPL scenarios drive an actual claude REPL through tmux and cost
    both API budget and wall-clock. They stay opt-in so the default
    `pytest tests/integration/` run stays cheap.
    """
    if os.environ.get("OT_REAL_REPL") == "1":
        return
    skip_marker = pytest.mark.skip(
        reason="real_repl scenario; set OT_REAL_REPL=1 to run"
    )
    for item in items:
        if "real_repl" in item.keywords:
            item.add_marker(skip_marker)


@pytest.fixture
def scenarios_dir() -> Path:
    """Absolute path to the scenario TOMLs."""
    return SCENARIOS_DIR

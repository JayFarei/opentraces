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


FIXTURE_ENTITY_BIN = (
    Path(__file__).parent.parent / "fixtures" / "ot-entities" / "ot-entities"
).resolve()


@pytest.fixture(autouse=True)
def _entity_parser_fixture_bin(request, monkeypatch):
    """Point the entity parser at the fixture binary for `entity_*` scenarios.

    Scenarios named ``entity_*`` exercise the entity-diff cache path but
    we never want them to touch the real binary (which may not be
    installed locally). Setting ``OPENTRACES_ENTITY_BIN`` to the fixture
    means ``EntityRunner.available()`` returns True and the backfill
    writes entity cache files using the fixture's canned JSON.
    """
    node = getattr(request, "node", None)
    nodeid = getattr(node, "nodeid", "") if node else ""
    # The scenario runner parametrizes with id=<stem>, e.g. "[entity_adds_function]"
    if "entity_" in nodeid and FIXTURE_ENTITY_BIN.is_file():
        monkeypatch.setenv("OPENTRACES_ENTITY_BIN", str(FIXTURE_ENTITY_BIN))
    yield

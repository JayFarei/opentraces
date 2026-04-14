"""Pytest port of the 6 synthetic self-tests from attribution_v2_selftest.

These scenarios are API-free — they fabricate JSONL entries and file-history
blobs under ~/.claude/, run the spike subprocess, and assert per-line
ownership. They've been the cheap conviction check throughout the spike
phase (~5s for all six).

Porting to pytest means the same six assertions now gate every push via
`pytest tests/integration/`, not just a manual script invocation. Each
scenario cleans up after itself; the test module cleans up on setup too,
so ctrl-c'd previous runs don't leak state.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
SELFTEST_PATH = SCRIPTS_DIR / "attribution_v2_selftest.py"


def _load_selftest_module():
    """Load scripts/attribution_v2_selftest.py as a module without requiring
    the scripts/ directory on sys.path. The file still reads trace IDs,
    blob helpers, and scenario functions the way the CLI invocation does."""
    spec = importlib.util.spec_from_file_location(
        "_ot_selftest", SELFTEST_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_ot_selftest"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _isolate_opentraces_global_state():
    """Override the repo-wide autouse HOME-redirect fixture.

    The selftest scenarios write synthetic JSONLs and file-history blobs
    under `Path.home() / ".claude"` at module-import time, then invoke
    the spike as a subprocess which re-reads `Path.home()` from the
    inherited env. If HOME is a per-test tmpdir the module-time paths
    and the subprocess-time paths disagree and the scenarios see empty
    file-history. We explicitly let these tests use the real HOME; the
    selftest module cleans up after itself (cleanup() wipes
    ~/.claude/file-history/selftest-*).
    """
    yield


@pytest.fixture(scope="module")
def _selftest_module():
    """Load the selftest module once per test-module."""
    return _load_selftest_module()


@pytest.fixture
def selftest(_selftest_module):
    """Per-test fixture: clean up before AND after each scenario so state
    never leaks between tests. Each scenario sets up its own synthetic
    project, blobs, and JSONL, so a clean slate is required."""
    _selftest_module.cleanup()
    yield _selftest_module
    _selftest_module.cleanup()


def test_scenario_baseline(selftest):
    """One trace creates one file, one commit — all lines credit the trace."""
    selftest.scenario_baseline()


def test_scenario_two_sessions_different_files(selftest):
    """Trace A writes README.md, trace B writes config.yaml; each gets its own."""
    selftest.scenario_two_sessions_different_files()


def test_scenario_small_tweak(selftest):
    """The patch-id-killer: A writes 20 lines, B edits 2 → 18/2 split."""
    selftest.scenario_small_tweak()


def test_scenario_three_way_layered(selftest):
    """A/B/C build a file across three sessions; per-line credit is preserved."""
    selftest.scenario_three_way_layered()


def test_scenario_pre_session_content(selftest):
    """Init-commit content gets pre-audit credit; trace-appended lines credit the trace."""
    selftest.scenario_pre_session_content()


def test_scenario_working_tree_captured(selftest):
    """Watcher sidecar (working-tree snapshot) attributes Bash-created files correctly."""
    selftest.scenario_working_tree_captured()

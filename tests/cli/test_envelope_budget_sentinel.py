"""PR-lane envelope-budget sentinel (issue #51).

The authoritative 25-surface envelope-budget sweep lives in
``tests/otbox/test_envelope_budgets.py`` and only runs nightly — the PR
lane ignores ``tests/otbox`` entirely. That gap is how commit 348248c
landed a 4.3x envelope blow on ``trace index status --json`` with green
PR CI (the PR-lane shape check freezes top-level key SETS, never size).

This module is the cheap early tripwire: a tiny deterministic
in-process world (CliRunner, no subprocess, no otbox) measuring three
high-traffic ``--json`` surfaces against committed budgets in
``tests/cli/envelope_budgets_pr.json``. Measurement is path-normalized
via the shared ``tests/envelope_measure.py`` helper so identical
envelopes measure identically across checkouts and tmp roots.

Budgets are deliberately independent of the otbox numbers — different
world. Seeded at ``max(2 * measured, 300)`` (floor precedent documented
in ``tests/otbox/envelope_budgets.json``). Self-service re-seed:

    OT_REGEN_PR_ENVELOPE_BUDGETS=1 pytest tests/cli/test_envelope_budget_sentinel.py -q
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from opentraces.cli import main
from tests.envelope_measure import approx_tokens, normalize_paths

BUDGETS_PATH = Path(__file__).parent / "envelope_budgets_pr.json"

_REGEN = os.environ.get("OT_REGEN_PR_ENVELOPE_BUDGETS") == "1"

# Bootstrap argv vocabulary for first-time seeding (regen with no
# committed file yet). Once the seed exists, the JSON file's argv is
# authoritative (hand-edits survive regen).
_DEFAULT_ARGV: dict[str, list[str]] = {
    "status": ["--json", "status"],
    "trace-index-status": ["trace", "index", "status", "--json"],
    "ctx-list": ["ctx", "list", "--json"],
}

_BUDGET_FLOOR = 300


def _load_budgets() -> dict[str, dict]:
    if BUDGETS_PATH.exists():
        return json.loads(BUDGETS_PATH.read_text(encoding="utf-8"))
    if _REGEN:
        return {label: {"argv": list(argv)} for label, argv in _DEFAULT_ARGV.items()}
    pytest.fail(
        f"missing committed PR-lane budget seed {BUDGETS_PATH}; seed with "
        "`OT_REGEN_PR_ENVELOPE_BUDGETS=1 pytest "
        "tests/cli/test_envelope_budget_sentinel.py -q`"
    )


# ---------------------------------------------------------------------------
# Helper micro-tests (pure functions, no world)
# ---------------------------------------------------------------------------


def test_normalize_paths_replaces_longest_root_first():
    text = "/private/var/x/home/.opentraces/db.sqlite and /private/var/x/file"
    out = normalize_paths(text, ["/private/var/x", "/private/var/x/home"])
    # The nested (longer) root collapses first — no "<ROOT>/home" residue.
    assert out == "<ROOT>/.opentraces/db.sqlite and <ROOT>/file"


def test_approx_tokens_is_path_invariant():
    payload_a = json.dumps({"path": "/tmp/aa/snapshot.db", "count": 1})
    payload_b = json.dumps(
        {"path": "/tmp/a-much-longer-alternate-tmp-root-xxxxxxxx/snapshot.db", "count": 1}
    )
    tokens_a = approx_tokens(payload_a, ["/tmp/aa"])
    tokens_b = approx_tokens(
        payload_b, ["/tmp/a-much-longer-alternate-tmp-root-xxxxxxxx"]
    )
    assert tokens_a == tokens_b


# ---------------------------------------------------------------------------
# Sentinel world (function-scoped — composes with the autouse HOME
# isolation in tests/conftest.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def sentinel_world(tmp_path, monkeypatch, _isolate_opentraces_global_state):
    """Tiny deterministic in-process world for envelope measurement.

    One project, one staged trace (fixed ids, NO ``timestamp_end`` so
    the status ``age`` field is the constant ``"unknown"``), one bucket
    manifest row for ``ctx list``, and a built search snapshot so a
    348248c-class ``db_sizes`` re-expansion is measurable (FTS shadow
    table count is corpus-independent).
    """
    opentraces_dir = _isolate_opentraces_global_state
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: False)
    runner = CliRunner()
    result = runner.invoke(main, ["init", "--start-fresh"])
    assert result.exit_code == 0, f"init failed: {result.output}"

    from opentraces.core.bucket_store import (
        upsert_manifest_trace_row,
        write_trace_record,
    )
    from opentraces.core.config import (
        get_project_state_path,
        get_project_traces_dir,
    )
    from opentraces.core.state import StateManager, TraceStatus
    from opentraces_schema import TraceRecord

    trace_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    record = TraceRecord(
        trace_id=trace_id,
        session_id="sentinel-session-001",
        agent={"name": "claude-code", "version": "1.0.0"},
        task={"description": "Envelope budget sentinel trace"},
        steps=[
            {"step_index": 1, "role": "user", "content": "hello"},
            {
                "step_index": 2,
                "role": "agent",
                "content": "hi there",
                "tool_calls": [
                    {
                        "tool_call_id": "tc1",
                        "tool_name": "Read",
                        "input": {"file": "test.py"},
                    }
                ],
            },
        ],
    )

    staging_dir = get_project_traces_dir(tmp_path)
    staging_file = staging_dir / f"{trace_id}.jsonl"
    staging_file.write_text(record.model_dump_json() + "\n")
    state = StateManager(state_path=get_project_state_path(tmp_path))
    state.set_trace_status(
        trace_id,
        TraceStatus.STAGED,
        session_id="sentinel-session-001",
        file_path=str(staging_file),
    )

    # Bucket manifest row so `ctx list` (manifest-only reader) has one trace.
    write_trace_record(
        record, project_slug="proj", source_layer="canonical", legacy_mirror=False
    )
    upsert_manifest_trace_row(
        None, project_slug="proj", trace_id=trace_id, record=record
    )

    # Build the search-snapshot SQLite DB WITH its FTS shadow tables — that
    # makes a db_sizes re-expansion measurable even on a one-trace world.
    result = runner.invoke(main, ["trace", "index"])
    assert result.exit_code == 0, f"trace index failed: {result.output}"

    home = os.environ["HOME"]
    roots: list[str] = []
    for raw in (str(tmp_path), home, str(opentraces_dir)):
        roots.append(raw)
        try:
            roots.append(str(Path(raw).resolve()))
        except OSError:
            pass
    return runner, roots


@pytest.fixture
def measured(sentinel_world):
    runner, roots = sentinel_world
    budgets = _load_budgets()
    out: dict[str, dict] = {}
    for label, spec in budgets.items():
        result = runner.invoke(main, list(spec["argv"]))
        out[label] = {
            "rc": result.exit_code,
            "tokens": approx_tokens(result.output or "", roots),
            "budget": int(spec.get("max_tokens") or 0),
        }
    return out


# ---------------------------------------------------------------------------
# Budget gate tests (mirror tests/otbox/test_envelope_budgets.py)
# ---------------------------------------------------------------------------


def test_pr_budget_file_is_valid():
    if _REGEN and not BUDGETS_PATH.exists():
        pytest.skip("regen mode: seed not yet written")
    assert BUDGETS_PATH.exists()
    budgets = json.loads(BUDGETS_PATH.read_text(encoding="utf-8"))
    assert budgets, "envelope_budgets_pr.json must define at least one surface"
    for label, spec in budgets.items():
        assert spec.get("argv"), f"{label}: argv required"
        assert int(spec.get("max_tokens", 0)) > 0, f"{label}: max_tokens required"


def test_pr_budgeted_surfaces_run(measured):
    failed = {label: row for label, row in measured.items() if row["rc"] != 0}
    assert not failed, f"PR-budgeted surfaces failed to run (no silent skip): {failed}"


def test_pr_envelopes_within_budget(measured):
    if _REGEN:
        existing = (
            json.loads(BUDGETS_PATH.read_text(encoding="utf-8"))
            if BUDGETS_PATH.exists()
            else {}
        )
        seed: dict[str, dict] = {}
        for label, row in measured.items():
            prev = existing.get(label) or {}
            entry: dict = {
                "argv": list(prev.get("argv") or _DEFAULT_ARGV[label]),
                "max_tokens": max(2 * row["tokens"], _BUDGET_FLOOR),
                "measured_at_seed": row["tokens"],
            }
            if prev.get("note"):
                entry["note"] = prev["note"]
            seed[label] = entry
        BUDGETS_PATH.write_text(
            json.dumps(seed, indent=2) + "\n", encoding="utf-8"
        )
        pytest.skip(
            f"re-seeded {len(seed)} PR-lane envelope budgets at {BUDGETS_PATH}"
        )
    over = {
        label: row
        for label, row in measured.items()
        if row["rc"] == 0 and row["tokens"] > row["budget"]
    }
    assert not over, (
        "agent-facing envelopes exceeded their committed PR-lane token "
        "budgets (raising a budget is a reviewed contract change in "
        "tests/cli/envelope_budgets_pr.json, in the same PR as the "
        f"envelope change): {over}"
    )

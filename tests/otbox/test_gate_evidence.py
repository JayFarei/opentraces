"""Executed-evidence gate contract (otbox 2.0 phase 3 tail).

Mocks the `gh` boundary so the gate's DECISION logic is tested without
network: latest-run selection (schedule/dispatch on main only), fail-closed
on absent/failed/red, and tier derivation from the nightly ledger.
"""

from __future__ import annotations

import json

import pytest

from . import gate_evidence as ge


@pytest.fixture
def fake_gh(monkeypatch, tmp_path):
    state = {"runs": [], "ledger": None, "download_ok": True}

    def fake_gh_json(args):
        if args[:2] == ["run", "list"]:
            return state["runs"]
        return None

    def fake_run(cmd, capture_output, text):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        r = R()
        if "download" in cmd:
            if not state["download_ok"] or state["ledger"] is None:
                r.returncode = 1
                return r
            (tmp_path_dir := _last_dir(cmd)).mkdir(parents=True, exist_ok=True)
            (tmp_path_dir / ge.LEDGER_FILE).write_text(json.dumps(state["ledger"]))
        return r

    def _last_dir(cmd):
        from pathlib import Path
        return Path(cmd[cmd.index("--dir") + 1])

    monkeypatch.setattr(ge, "_gh_json", fake_gh_json)
    monkeypatch.setattr(ge.subprocess, "run", fake_run)
    return state


def _run(event="schedule", conclusion="success", rid=42, branch="main"):
    return {"databaseId": rid, "status": "completed", "conclusion": conclusion,
            "event": event, "createdAt": "2026-06-10T00:00:00Z", "headBranch": branch}


def test_no_run_is_fail_closed(fake_gh):
    fake_gh["runs"] = []
    res = ge.read_gate()
    assert not res.ok and "no completed" in res.reason


def test_pr_event_runs_are_ignored(fake_gh):
    fake_gh["runs"] = [_run(event="pull_request")]
    assert ge.latest_nightly_run() is None


def test_failed_nightly_is_fail_closed(fake_gh):
    fake_gh["runs"] = [_run(conclusion="failure")]
    res = ge.read_gate()
    assert not res.ok and "concluded" in res.reason


def test_red_rows_fail_the_gate(fake_gh):
    fake_gh["runs"] = [_run()]
    fake_gh["ledger"] = {"rows": [
        {"journey": "a", "red": False, "effective_tier": "silver"},
        {"journey": "b", "red": True, "effective_tier": "pending"},
    ]}
    res = ge.read_gate()
    assert not res.ok and "red row" in res.reason


def test_clean_nightly_passes_and_yields_tiers(fake_gh):
    fake_gh["runs"] = [_run(rid=99)]
    fake_gh["ledger"] = {"rows": [
        {"journey": "sec", "red": False, "effective_tier": "gold"},
        {"journey": "q", "red": False, "effective_tier": "silver"},
    ]}
    res = ge.read_gate()
    assert res.ok and res.run_id == "99"
    tiers = ge.effective_tiers_from_gate(res)
    assert tiers == {"sec": "gold", "q": "silver"}


def test_stale_gate_yields_no_tiers(fake_gh):
    """When the gate is not ok, labels contribute nothing — every journey is
    pending by absence."""
    fake_gh["runs"] = []
    res = ge.read_gate()
    assert ge.effective_tiers_from_gate(res) == {}


def test_dispatch_event_is_accepted(fake_gh):
    """workflow_dispatch on main is a legitimate nightly (the first run before
    the cron fires is dispatched)."""
    fake_gh["runs"] = [_run(event="workflow_dispatch")]
    assert ge.latest_nightly_run()["databaseId"] == 42

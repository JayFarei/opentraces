"""Issue #162 — `bucket list` bounded reads, the sync-push withhold partition,
and the `list` <-> `status` one-truth (L6).

Hermetic: writes into the conftest-isolated ``$HOME/.opentraces`` bucket and
reads it back through the same in-process store the CLI uses. No network, no
subprocess.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.bucket_list import build_bucket_list
from opentraces.core.bucket_sync import push_withhold_partition, sync_push_partition


# --------------------------------------------------------------------------
# push_withhold_partition — the pure egress-safety partition (V4 core).
# --------------------------------------------------------------------------
def test_partition_pure_covers_all_three_process_states():
    rows = [
        {"trace_id": "t-cleared", "status": {"known": True, "syncable": True}},
        {"trace_id": "t-unfiltered", "status": {"known": True, "syncable": False}},
        {"trace_id": "t-unknown", "status": {"known": False, "syncable": None}},
        # A row with NO status block at all is also 'unknown' (conservative).
        {"trace_id": "t-no-status"},
    ]
    part = push_withhold_partition(rows)

    assert part["pushed"] == ["t-cleared"]
    withheld = {w["trace_id"]: w for w in part["withheld"]}
    assert set(withheld) == {"t-unfiltered", "t-unknown", "t-no-status"}
    # Every withhold reason is the softened PROCESS state, never a content claim.
    assert all(w["reason"] == "not_cleared_for_sync" for w in part["withheld"])
    assert withheld["t-unfiltered"]["sub_reason"] == "syncable_false"
    assert withheld["t-unknown"]["sub_reason"] == "status_unknown"
    assert withheld["t-no-status"]["sub_reason"] == "status_unknown"

    # The load-bearing egress-safety invariant: the two sets are disjoint.
    assert set(part["pushed"]).isdisjoint({w["trace_id"] for w in part["withheld"]})


def test_partition_empty_when_no_manifest(_isolate_opentraces_global_state):
    # Absent manifest → empty partition (nothing cleared, nothing withheld);
    # never a scan.
    part = sync_push_partition()
    assert part == {"pushed": [], "withheld": []}


# --------------------------------------------------------------------------
# Seeded bucket with a manufactured status mix (the V4 / L6 fixture).
# --------------------------------------------------------------------------
def _seed_three_traces() -> None:
    from opentraces.core.bucket_store import write_trace_record
    from opentraces.security import SECURITY_VERSION
    from opentraces_schema import Agent, Step, TraceRecord

    for i in range(3):
        rec = TraceRecord(
            trace_id=f"list-trace-{i}",
            session_id=f"list-session-{i}",
            agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
            task={"description": f"seed row {i} " + ("x" * 200)},
            steps=[Step(step_index=1, role="user", content="seed")],
            outcome={"success": True, "committed": False},
        )
        rec.security.scanned = True
        rec.security.classifier_version = SECURITY_VERSION
        write_trace_record(
            rec,
            project_slug="list-demo",
            source_layer="canonical",
            legacy_mirror=False,
            privacy_tier="medium",
        )
    # Persist manifest.json with the per-row status accelerator so the O(1)
    # read model (which both `bucket list` and top-level `status` consume) has
    # something to serve. write_trace_record alone does not persist it.
    from opentraces.core.bucket_store import bucket_manifest

    bucket_manifest(write=True, heal=True)


def _stamp_status_mix() -> dict[str, str]:
    """Rewrite the persisted manifest rows' status accelerator into a known mix:
    one cleared, one syncable-false, one unscanned. Returns trace_id -> facet."""
    from opentraces.core.bucket_store import bucket_manifest_path

    path = bucket_manifest_path()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    rows = sorted(manifest["traces"], key=lambda r: r["trace_id"])
    assert len(rows) == 3
    facets = {}
    rows[0]["status"] = {"known": True, "syncable": True, "privacy_off": False,
                         "security_stale": False, "written_at": "2026-01-01T00:00:00Z"}
    facets[rows[0]["trace_id"]] = "cleared"
    rows[1]["status"] = {"known": True, "syncable": False, "privacy_off": False,
                         "security_stale": False, "written_at": "2026-01-02T00:00:00Z"}
    facets[rows[1]["trace_id"]] = "syncable_false"
    rows[2]["status"] = {"known": False, "syncable": None, "privacy_off": None,
                         "security_stale": None, "written_at": None}
    facets[rows[2]["trace_id"]] = "unscanned"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return facets


def test_list_unscanned_count_equals_status_unscanned_count(_isolate_opentraces_global_state):
    """L6 one-truth: `bucket list --unscanned --count` == `status`
    safety.unscanned_count over the SAME seeded bucket."""
    from opentraces.core.fleet_status import build_fleet_status

    _seed_three_traces()
    _stamp_status_mix()

    list_count = build_bucket_list(unscanned=True, count_only=True)["count"]
    status = build_fleet_status()
    assert list_count == status["safety"]["unscanned_count"] == 1


def test_list_filters_and_sparsity_contract(_isolate_opentraces_global_state):
    _seed_three_traces()
    facets = _stamp_status_mix()

    # --unscanned isolates exactly the unknown-status row and reports it as
    # an explicit 'unknown' sync_state (never coerced to clean).
    unscanned = build_bucket_list(unscanned=True)
    assert unscanned["total"] == 1
    assert unscanned["unknown_status_count"] == 1
    assert unscanned["rows"][0]["sync_state"] == "unknown"

    # --unsynced (not cleared for sync) is the syncable-false + unknown rows.
    unsynced = build_bucket_list(unsynced=True, count_only=True)
    assert unsynced["count"] == 2

    # --unfiltered is the scanned-but-not-syncable row only.
    unfiltered = build_bucket_list(unfiltered=True)
    assert unfiltered["total"] == 1
    assert unfiltered["rows"][0]["sync_state"] == "not_cleared"

    # The full list carries the whole corpus + the sparsity count, and titles
    # are truncated to <= 80 chars.
    full = build_bucket_list()
    assert full["total"] == 3
    assert full["unknown_status_count"] == 1
    assert all(r["title"] is None or len(r["title"]) <= 80 for r in full["rows"])


def test_list_pagination_cursor_is_stable_and_deterministic(_isolate_opentraces_global_state):
    _seed_three_traces()
    _stamp_status_mix()

    page1 = build_bucket_list(limit=2)
    assert len(page1["rows"]) == 2
    assert page1["total"] == 3
    assert page1["cursor"]  # more pages remain

    page2 = build_bucket_list(limit=2, cursor=page1["cursor"])
    assert len(page2["rows"]) == 1
    assert page2["cursor"] is None  # last page

    # No overlap and full coverage across pages.
    seen = [r["trace_id"] for r in page1["rows"]] + [r["trace_id"] for r in page2["rows"]]
    assert sorted(seen) == ["list-trace-0", "list-trace-1", "list-trace-2"]

    # Rows with a written_at sort before the unknown-written_at row (sentinel
    # last): the unscanned row (no written_at) is on the final page.
    assert page2["rows"][0]["written_at"] is None


def test_list_limit_hard_capped(_isolate_opentraces_global_state):
    _seed_three_traces()
    _stamp_status_mix()
    # A limit over the hard cap is clamped; a limit of 0 clamps up to 1.
    assert len(build_bucket_list(limit=10_000)["rows"]) == 3
    assert len(build_bucket_list(limit=0)["rows"]) == 1


def test_sync_push_partition_withholds_manufactured_not_cleared_trace(
    _isolate_opentraces_global_state,
):
    """V4 core over a real seeded bucket: the cleared trace is pushed, the
    syncable-false and unscanned traces are withheld as a PROCESS state, and
    the two sets are disjoint."""
    _seed_three_traces()
    facets = _stamp_status_mix()

    part = sync_push_partition()
    cleared = [t for t, f in facets.items() if f == "cleared"]
    not_cleared = [t for t, f in facets.items() if f != "cleared"]

    assert part["pushed"] == cleared
    assert {w["trace_id"] for w in part["withheld"]} == set(not_cleared)
    assert set(part["pushed"]).isdisjoint({w["trace_id"] for w in part["withheld"]})
    assert all(w["reason"] == "not_cleared_for_sync" for w in part["withheld"])


# --------------------------------------------------------------------------
# Egress safety (#162 hardening): the REAL push must REFUSE — zero bytes out —
# when the fresh push-time partition still withholds any trace. Reproduces the
# adversarial-review finding: a status_unknown (status.known=False) row does
# NOT increment unfiltered_count, so the bucket-level sync.eligible flag passes
# while the trace is reported withheld — yet it physically egressed.
# --------------------------------------------------------------------------
def _clear_bucket() -> None:
    """Remove any bucket left by other seed helpers in the same test process."""
    import shutil

    from opentraces.core import paths

    root = paths.bucket_dir()
    if root.exists():
        shutil.rmtree(root)


def _write_cleared_trace(trace_id: str) -> None:
    """A fully-cleared trace via the normal writer (object envelope syncable)."""
    from opentraces.core.bucket_store import write_trace_record
    from opentraces.security import SECURITY_VERSION
    from opentraces_schema import Agent, Step, TraceRecord

    rec = TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="claude-code"),
        task={"description": "cleared for sync"},
        steps=[Step(step_index=1, role="user", content="x")],
        outcome={"success": True, "committed": False},
    )
    rec.security.scanned = True
    rec.security.classifier_version = SECURITY_VERSION
    write_trace_record(
        rec,
        project_slug="egress-demo",
        source_layer="canonical",
        legacy_mirror=False,
        privacy_tier="medium",
    )


def _write_bare_trace_json(trace_id: str, *, scanned: bool) -> None:
    """Write a per-trace ``trace.json`` DIRECTLY with no object-store envelope.

    A trace with no object-store envelope is invisible to unfiltered_count
    (which scans the object store), so sync.eligible can stay True — while its
    manifest row is derived from trace.json and lands ``syncable=False``
    (unscanned) or ``status_unknown`` (unparseable).
    """
    from opentraces.core.bucket_envelope import trace_v1_json_path
    from opentraces.security import SECURITY_VERSION
    from opentraces_schema import Agent, Step, TraceRecord

    path = trace_v1_json_path("egress-demo", trace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if scanned is None:
        # Unparseable -> record=None -> row status_unknown (known=False).
        path.write_text(json.dumps({"not": "a valid trace record"}), encoding="utf-8")
        return
    rec = TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="claude-code"),
        task={"description": "unscanned"},
        steps=[Step(step_index=1, role="user", content="x")],
        outcome={"success": True, "committed": False},
    )
    if scanned:
        rec.security.scanned = True
        rec.security.classifier_version = SECURITY_VERSION
    path.write_text(rec.model_dump_json(), encoding="utf-8")


def _seed_eligible_but_withheld_bucket() -> None:
    """Seed the reviewer's repro: sync.eligible True, but withheld non-empty.

    One cleared trace (object-store syncable=True), one unscanned trace
    (``syncable_false``), and one unparseable trace (``status_unknown``); the
    latter two have no object envelope, so unfiltered_count stays 0 and the
    bucket-level gate passes.
    """
    from opentraces.core.bucket_store import bucket_manifest

    _clear_bucket()
    _write_cleared_trace("cleared-1")
    _write_bare_trace_json("unscanned-1", scanned=False)
    _write_bare_trace_json("status-unknown-1", scanned=None)
    bucket_manifest(write=True, heal=True)


def _remote_trace_files(remote_root) -> list[str]:
    from pathlib import Path

    root = Path(remote_root)
    if not root.exists():
        return []
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    )


def _assert_refusal_and_zero_egress(result, remote_root) -> None:
    assert result.exit_code != 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "opentraces.bucket.sync_push.v1"
    assert payload["status"] == "refused"
    assert payload["pushed"] == []
    withheld_ids = {w["trace_id"] for w in payload["withheld"]}
    assert withheld_ids == {"unscanned-1", "status-unknown-1"}
    assert all(w["reason"] == "not_cleared_for_sync" for w in payload["withheld"])
    # The load-bearing property: NOT ONE trace file egressed.
    assert _remote_trace_files(remote_root) == []


def test_real_sync_push_refuses_and_egresses_nothing_when_withheld(
    monkeypatch, tmp_path, _isolate_opentraces_global_state
):
    _seed_eligible_but_withheld_bucket()
    remote_root = tmp_path / "fake-remote"
    monkeypatch.setenv("OPENTRACES_FAKE_BUCKET_REMOTE_ROOT", str(remote_root))

    result = CliRunner().invoke(main, ["bucket", "sync", "push", "--json"])
    _assert_refusal_and_zero_egress(result, remote_root)


def test_hidden_bucket_remote_push_enforces_identically(
    monkeypatch, tmp_path, _isolate_opentraces_global_state
):
    """The hidden ``bucket remote push`` alias shares the command object, so it
    must refuse identically — hidden != a weaker egress contract."""
    _seed_eligible_but_withheld_bucket()
    remote_root = tmp_path / "fake-remote"
    monkeypatch.setenv("OPENTRACES_FAKE_BUCKET_REMOTE_ROOT", str(remote_root))

    result = CliRunner().invoke(main, ["bucket", "remote", "push", "--json"])
    _assert_refusal_and_zero_egress(result, remote_root)


def test_fully_cleared_bucket_pushes_everything(
    monkeypatch, tmp_path, _isolate_opentraces_global_state
):
    from opentraces.core.bucket_store import bucket_manifest

    _clear_bucket()
    _write_cleared_trace("cleared-1")
    _write_cleared_trace("cleared-2")
    bucket_manifest(write=True, heal=True)
    remote_root = tmp_path / "fake-remote"
    monkeypatch.setenv("OPENTRACES_FAKE_BUCKET_REMOTE_ROOT", str(remote_root))

    result = CliRunner().invoke(main, ["bucket", "sync", "push", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["withheld"] == []
    assert set(payload["pushed"]) == {"cleared-1", "cleared-2"}
    # Everything egressed: both trace.json spines landed on the remote.
    egressed = _remote_trace_files(remote_root)
    assert any("cleared-1/trace.json" in f for f in egressed)
    assert any("cleared-2/trace.json" in f for f in egressed)


def test_dry_run_and_real_push_agree_on_a_current_bucket(
    monkeypatch, tmp_path, _isolate_opentraces_global_state
):
    """Consistency: --dry-run's persisted-manifest partition matches the fresh
    push-time partition on a bucket whose manifest is current."""
    _seed_eligible_but_withheld_bucket()
    remote_root = tmp_path / "fake-remote"
    monkeypatch.setenv("OPENTRACES_FAKE_BUCKET_REMOTE_ROOT", str(remote_root))

    dry = CliRunner().invoke(main, ["bucket", "sync", "push", "--dry-run", "--json"])
    assert dry.exit_code == 0, dry.output
    dry_payload = json.loads(dry.output)
    assert dry_payload["dry_run"] is True
    dry_withheld = {w["trace_id"] for w in dry_payload["withheld"]}
    assert dry_withheld == {"unscanned-1", "status-unknown-1"}
    # Nothing egressed by the dry-run either.
    assert _remote_trace_files(remote_root) == []

    real = CliRunner().invoke(main, ["bucket", "sync", "push", "--json"])
    real_payload = json.loads(real.output)
    assert real_payload["status"] == "refused"
    assert {w["trace_id"] for w in real_payload["withheld"]} == dry_withheld

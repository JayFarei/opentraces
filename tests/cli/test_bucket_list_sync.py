"""Issue #162 — `bucket list` bounded reads, the sync-push withhold partition,
and the `list` <-> `status` one-truth (L6).

Hermetic: writes into the conftest-isolated ``$HOME/.opentraces`` bucket and
reads it back through the same in-process store the CLI uses. No network, no
subprocess.
"""
from __future__ import annotations

import json

import pytest

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

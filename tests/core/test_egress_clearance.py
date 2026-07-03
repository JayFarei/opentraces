"""Issue #183 — the ONE shared egress clearance predicate.

RED-first: names the symbols the seal-family train extracts out of
``bucket_sync.push_withhold_partition`` (PR #174) so dataset-publish (#194) and
capsule-publish (#198) can adopt the SAME per-trace clearance test in M2. The
predicate is a leaf (imports nothing from ``bucket_sync``/``datasets``/
``capsule``); this file pins its vocabulary against ``bucket_list``'s
``sync_state`` three-way (``cleared`` / ``not_cleared`` / ``unknown``).
"""
from __future__ import annotations

import pytest

# RED-first symbol existence: these MUST resolve out of the new leaf module.
from opentraces.core.egress_clearance import (
    clearance_for_trace,
    clearance_state,
    is_row_cleared,
)


# --------------------------------------------------------------------------
# clearance_state — the pure three-way over one row's status block.
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "status,expected",
    [
        # cleared: known and positively syncable.
        ({"known": True, "syncable": True}, "cleared"),
        # not_cleared: known but syncable is not True (explicit False).
        ({"known": True, "syncable": False}, "not_cleared"),
        # not_cleared: known but syncable is None (declared-but-not-clean).
        ({"known": True, "syncable": None}, "not_cleared"),
        # not_cleared: known but syncable key absent entirely.
        ({"known": True}, "not_cleared"),
        # unknown: known is False.
        ({"known": False, "syncable": None}, "unknown"),
        # unknown: known key absent.
        ({"syncable": True}, "unknown"),
        # unknown: status block entirely absent.
        (None, "unknown"),
        # unknown: non-dict junk is coerced to unknown, never crashes.
        ("not-a-dict", "unknown"),
    ],
)
def test_clearance_state_three_way(status, expected):
    assert clearance_state(status) == expected


def test_clearance_state_mirrors_bucket_list_vocabulary():
    """The three return strings ARE bucket_list.py's sync_state vocabulary, so a
    future consolidation of the duplicate readers is not fighting a fourth
    vocabulary (reader §4)."""
    assert {
        clearance_state({"known": True, "syncable": True}),
        clearance_state({"known": True, "syncable": False}),
        clearance_state(None),
    } == {"cleared", "not_cleared", "unknown"}


# --------------------------------------------------------------------------
# is_row_cleared — the boolean convenience wrapper (== state == "cleared").
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "status,cleared",
    [
        ({"known": True, "syncable": True}, True),
        ({"known": True, "syncable": False}, False),
        ({"known": False, "syncable": None}, False),
        (None, False),
    ],
)
def test_is_row_cleared(status, cleared):
    assert is_row_cleared(status) is cleared
    # Load-bearing identity: the boolean is exactly "state == cleared".
    assert is_row_cleared(status) is (clearance_state(status) == "cleared")


# --------------------------------------------------------------------------
# clearance_for_trace — the trace-id-keyed entry point Doors B/C consume.
# --------------------------------------------------------------------------
def _synthetic_manifest() -> dict:
    return {
        "traces": [
            {"trace_id": "t-cleared", "status": {"known": True, "syncable": True}},
            {"trace_id": "t-unfiltered", "status": {"known": True, "syncable": False}},
            {"trace_id": "t-unscanned", "status": {"known": False, "syncable": None}},
            {"trace_id": "t-no-status"},
        ]
    }


@pytest.mark.parametrize(
    "trace_id,expected",
    [
        ("t-cleared", "cleared"),
        ("t-unfiltered", "not_cleared"),
        ("t-unscanned", "unknown"),
        ("t-no-status", "unknown"),
        # A trace not present in the manifest is unknown, never crashes.
        ("t-absent", "unknown"),
    ],
)
def test_clearance_for_trace_with_in_hand_manifest(trace_id, expected):
    # No I/O: the caller already holds ONE push-time snapshot and indexes into
    # it for every row (the no-TOCTOU path — #183 AC3).
    assert clearance_for_trace(trace_id, manifest=_synthetic_manifest()) == expected


def test_clearance_for_trace_manifest_junk_is_unknown():
    # A malformed manifest (no traces / not a dict) yields unknown, not a crash.
    assert clearance_for_trace("t-cleared", manifest={}) == "unknown"
    assert clearance_for_trace("t-cleared", manifest={"traces": None}) == "unknown"
    assert clearance_for_trace("t-cleared", manifest=None) is not None  # falls back


def test_clearance_for_trace_falls_back_to_by_id_when_no_manifest(monkeypatch):
    """With no manifest, resolve one trace's status via the existing
    ``trace_v2_summary_by_id`` primitive (reader §4) — no second lookup path."""
    summaries = {
        "t-cleared": {"status": {"known": True, "syncable": True}},
        "t-unfiltered": {"status": {"known": True, "syncable": False}},
        "t-unscanned": {"status": {"known": False, "syncable": None}},
    }

    def fake_by_id(trace_id: str):
        return summaries.get(trace_id)

    # The lazy import target lives in bucket_envelope; patch it there.
    monkeypatch.setattr(
        "opentraces.core.bucket_envelope.trace_v2_summary_by_id", fake_by_id
    )

    assert clearance_for_trace("t-cleared") == "cleared"
    assert clearance_for_trace("t-unfiltered") == "not_cleared"
    assert clearance_for_trace("t-unscanned") == "unknown"
    # A trace with no envelope on disk resolves to unknown (summary is None).
    assert clearance_for_trace("t-absent") == "unknown"


# --------------------------------------------------------------------------
# Cross-check: the shared predicate agrees with bucket_sync's wrapper on the
# same rows (the byte-identity contract the re-wire must preserve, #183 AC1).
# --------------------------------------------------------------------------
def test_shared_predicate_agrees_with_push_withhold_partition():
    from opentraces.core.bucket_sync import push_withhold_partition

    rows = _synthetic_manifest()["traces"]
    part = push_withhold_partition(rows)

    pushed = set(part["pushed"])
    withheld = {w["trace_id"]: w for w in part["withheld"]}

    for row in rows:
        tid = row["trace_id"]
        state = clearance_state(row.get("status"))
        if state == "cleared":
            assert tid in pushed
        elif state == "not_cleared":
            assert withheld[tid]["sub_reason"] == "syncable_false"
        else:
            assert withheld[tid]["sub_reason"] == "status_unknown"

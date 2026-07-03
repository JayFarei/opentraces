"""``capsule import`` — schema-valid TraceRecord write + Trail companion + collisions.

The explicit opt-in WRITE side of the capsule consume ladder (issue #196):

* import materializes the carried spine into a schema-valid ``TraceRecord`` and
  the imported unit projects natively (map / slice / run-intel / compare);
* the carried anchor rows land in the per-trace Trail companion (empty companion
  + ``trail_anchors_unavailable`` when the capsule carries no anchors);
* an unparseable projection is an import ERROR, never a partial write;
* collisions: same capsule id is an idempotent no-op; a different capsule id over
  the same trace scope-merges (union + last-import-wins on overlap + provenance).

Hermetic: the bucket root is redirected to a tmp dir per test.
"""

from __future__ import annotations

import gzip
import json

import pytest

from opentraces.core import paths
from opentraces.core.bucket_layout import trace_v1_trail_path
from opentraces.core.bucket_trace_records import (
    read_trace_record_object,
    trace_record_path,
)
from opentraces.core.capsule.contract import freeze_capsule
from opentraces.core.capsule.import_ import (
    CAPSULE_IMPORT_REPORT_SCHEMA,
    CapsuleImportError,
    import_capsule,
)


@pytest.fixture
def bucket(tmp_path, monkeypatch):
    """Redirect the bucket root to a hermetic tmp dir."""

    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / "ot")
    return tmp_path


def _capsule(*, trace_id, cid, steps, anchors=(), slug="proj", agent="codex",
             capture_method="transcript_reconstruction", eco="python"):
    idxs = [s["step_index"] for s in steps] or [0]
    return freeze_capsule(
        capsule_id=cid,
        source={
            "project_slug": slug,
            "trace_id": trace_id,
            "agent": agent,
            "capture_method": capture_method,
        },
        summary={"title": "session"},
        test=None,
        environment={"dependencies": [], "language_ecosystem": eco, "setup": []},
        bundle=None,
        intent={"headline": "did stuff"},
        failing_step={"index": max(idxs)},
        slice_payload={
            "slice_id": "s",
            "trace_id": trace_id,
            "steps": steps,
            "start_step_index": min(idxs),
            "end_step_index": max(idxs),
            "limitations": [],
        },
        context_resume_packet={"schema_version": "opentraces.context_resume.v1", "node_id": None},
        trail_anchors=list(anchors),
        repo_pin={"remote_url": "https://github.com/o/r", "commit_sha": "abc", "changed_files": []},
        redaction={"manifest": {"floor": [], "floor_satisfied": True}},
        render_state={"redaction": "redacted_ok", "closure": "closure_full", "replay": "replay_unverified"},
        limitations=[],
        created_with="test",
    )


def _step(idx, content="did work"):
    return {"step_index": idx, "role": "agent", "content": content}


# --------------------------------------------------------------------------- #
# Schema-valid TraceRecord + native projection
# --------------------------------------------------------------------------- #


def test_import_writes_schema_valid_trace_record(bucket):
    tid = "11111111-1111-1111-1111-111111111111"
    cap = _capsule(trace_id=tid, cid="cid_aaaa00000000", steps=[_step(0), _step(1), _step(2)])
    report = import_capsule(cap)

    assert report["schema_version"] == CAPSULE_IMPORT_REPORT_SCHEMA
    assert report["status"] == "created"
    assert report["written"] is True
    assert report["trace_id"] == tid  # trace_id REUSED, not minted

    obj = read_trace_record_object(trace_record_path("proj", tid))
    assert obj is not None
    rec = obj.record
    assert rec.trace_id == tid
    assert [s.step_index for s in rec.steps] == [0, 1, 2]
    # Provenance recorded.
    prov = (rec.metadata or {}).get("capsule_import") or {}
    assert prov.get("capsule_ids") == ["cid_aaaa00000000"]


def test_imported_capsule_projects_natively(bucket):
    tid = "22222222-2222-2222-2222-222222222222"
    cap = _capsule(trace_id=tid, cid="cid_bbbb00000000",
                   steps=[_step(0, "start"), _step(1, "edit"), _step(2, "done")])
    import_capsule(cap)

    obj = read_trace_record_object(trace_record_path("proj", tid))
    rec = obj.record

    from opentraces.core.run_intel import detect_run_signals
    from opentraces.core.trace_map import build_trace_map
    from opentraces.core.trace_slices import slice_by_steps

    tm = build_trace_map(rec)
    assert tm.trace_id == tid
    sl = slice_by_steps(tm, rec, start_step_index=0, end_step_index=2)
    assert sl["steps"], "imported record must slice natively"
    ri = detect_run_signals(rec)
    assert ri.trace_id == tid  # run-intel projects natively


def test_two_imported_capsules_compare_natively(bucket):
    a, b = "aaaaaaaa-0000-0000-0000-000000000000", "bbbbbbbb-0000-0000-0000-000000000000"
    import_capsule(_capsule(trace_id=a, cid="cid_a000000000000", steps=[_step(0), _step(1)]))
    import_capsule(_capsule(trace_id=b, cid="cid_b000000000000", steps=[_step(0), _step(1), _step(2)]))

    from opentraces.core.trace_compare import compare_traces

    rec_a = read_trace_record_object(trace_record_path("proj", a)).record
    rec_b = read_trace_record_object(trace_record_path("proj", b)).record
    env = compare_traces(rec_a, rec_b, include_quality=False)
    assert env["schema_version"] == "opentraces.trace_compare.v1"


# --------------------------------------------------------------------------- #
# Trail companion
# --------------------------------------------------------------------------- #


def test_import_writes_trail_companion_with_anchors(bucket):
    tid = "33333333-3333-3333-3333-333333333333"
    anchors = [
        {"trace_patch_id": "tp:1", "git_anchor_id": "ga:1", "survival_state": "unknown"},
        {"trace_patch_id": "tp:2", "git_anchor_id": "ga:2", "survival_state": "unknown"},
    ]
    report = import_capsule(_capsule(trace_id=tid, cid="cid_cccc00000000", steps=[_step(0)], anchors=anchors))
    assert report["trail_anchor_count"] == 2
    assert "trail_anchors_unavailable" not in report["limitations"]

    path = trace_v1_trail_path("proj", tid)
    assert path.exists()
    body = gzip.decompress(path.read_bytes()).decode("utf-8")
    rows = [json.loads(line) for line in body.splitlines() if line.strip()]
    assert {r["git_anchor_id"] for r in rows} == {"ga:1", "ga:2"}


def test_import_empty_anchors_writes_empty_companion_and_surfaces_limitation(bucket):
    tid = "44444444-4444-4444-4444-444444444444"
    report = import_capsule(_capsule(trace_id=tid, cid="cid_dddd00000000", steps=[_step(0)], anchors=[]))
    assert report["trail_anchor_count"] == 0
    assert "trail_anchors_unavailable" in report["limitations"]

    path = trace_v1_trail_path("proj", tid)
    assert path.exists()
    assert gzip.decompress(path.read_bytes()) == b""  # empty companion, not absent


# --------------------------------------------------------------------------- #
# Collision rules
# --------------------------------------------------------------------------- #


def test_reimport_same_capsule_id_is_noop(bucket):
    tid = "55555555-5555-5555-5555-555555555555"
    cap = _capsule(trace_id=tid, cid="cid_eeee00000000", steps=[_step(0), _step(1)])
    first = import_capsule(cap)
    assert first["status"] == "created"
    second = import_capsule(cap)
    assert second["status"] == "noop"
    assert second["written"] is False

    # The record is unchanged: still exactly one capsule id in provenance.
    rec = read_trace_record_object(trace_record_path("proj", tid)).record
    assert (rec.metadata or {})["capsule_import"]["capsule_ids"] == ["cid_eeee00000000"]


def test_different_capsule_id_same_trace_scope_merges(bucket):
    tid = "66666666-6666-6666-6666-666666666666"
    import_capsule(_capsule(trace_id=tid, cid="cid_1111", steps=[_step(0), _step(1)]))
    report = import_capsule(_capsule(trace_id=tid, cid="cid_2222", steps=[_step(2), _step(3)]))

    assert report["status"] == "scope_merged"
    rec = read_trace_record_object(trace_record_path("proj", tid)).record
    # Disjoint steps unioned.
    assert [s.step_index for s in rec.steps] == [0, 1, 2, 3]
    # Both capsule ids recorded as provenance (never overwritten).
    assert (rec.metadata or {})["capsule_import"]["capsule_ids"] == ["cid_1111", "cid_2222"]


def test_scope_merge_records_conflict_on_overlapping_leaf(bucket):
    tid = "77777777-7777-7777-7777-777777777777"
    import_capsule(_capsule(trace_id=tid, cid="cid_v1", steps=[_step(0, "original")]))
    report = import_capsule(_capsule(trace_id=tid, cid="cid_v2", steps=[_step(0, "changed")]))

    assert report["status"] == "scope_merged"
    assert report["conflicts"], "an overlapping leaf that differs is a recorded conflict"
    assert report["conflicts"][0]["step_index"] == 0
    # last-import-wins: the newer content is kept.
    rec = read_trace_record_object(trace_record_path("proj", tid)).record
    assert rec.steps[0].content == "changed"


def test_scope_merge_anchors_union(bucket):
    tid = "88888888-8888-8888-8888-888888888888"
    import_capsule(_capsule(trace_id=tid, cid="cid_x1", steps=[_step(0)],
                            anchors=[{"git_anchor_id": "ga:A", "survival_state": "unknown"}]))
    import_capsule(_capsule(trace_id=tid, cid="cid_x2", steps=[_step(1)],
                            anchors=[{"git_anchor_id": "ga:B", "survival_state": "unknown"}]))

    path = trace_v1_trail_path("proj", tid)
    body = gzip.decompress(path.read_bytes()).decode("utf-8")
    rows = [json.loads(line) for line in body.splitlines() if line.strip()]
    assert {r["git_anchor_id"] for r in rows} == {"ga:A", "ga:B"}


# --------------------------------------------------------------------------- #
# Error, not partial write
# --------------------------------------------------------------------------- #


def test_unparseable_projection_is_import_error_not_partial_write(bucket):
    tid = "99999999-9999-9999-9999-999999999999"
    # A step with an invalid ``role`` cannot round-trip into a schema-valid Step.
    bad = _capsule(trace_id=tid, cid="cid_bad0", steps=[{"step_index": 0, "role": "not_a_role"}])
    with pytest.raises(CapsuleImportError):
        import_capsule(bad)

    # No partial write: neither the record nor its companion exist.
    assert read_trace_record_object(trace_record_path("proj", tid)) is None
    assert not trace_v1_trail_path("proj", tid).exists()


def test_import_missing_trace_id_is_error(bucket):
    cap = _capsule(trace_id="tid", cid="cid", steps=[_step(0)])
    cap["source"]["trace_id"] = ""
    with pytest.raises(CapsuleImportError):
        import_capsule(cap)


# --------------------------------------------------------------------------- #
# bucket_digest material shape is unchanged (M2 gate)
# --------------------------------------------------------------------------- #


def test_import_does_not_change_digest_material_shape(bucket):
    """Import writes a real trace row (which legitimately moves the digest VALUE),
    but must not change the digest MATERIAL SHAPE — no capsule-specific digest
    field, the same algorithm/schema, and a byte-stable digest on a fixed corpus."""

    from opentraces.core.bucket_trace_records import trace_record_snapshot

    tid = "cafe0000-0000-0000-0000-000000000000"
    import_capsule(_capsule(trace_id=tid, cid="cid_digest0000", steps=[_step(0), _step(1)]))

    snap = trace_record_snapshot(include_objects=True)
    assert snap["schema_version"] == "opentraces.bucket.trace_records_snapshot.v1"
    obj = next(o for o in snap["objects"] if o["trace_id"] == tid)
    # Exactly the standard digest-input fields — import injects no novel material.
    assert set(obj) == {
        "path", "trace_id", "project_slug", "source_layer", "record_hash",
        "privacy_tier", "security_version", "syncable", "security_stale", "written_at",
    }
    # Byte-stable on a fixed corpus: recomputing the snapshot yields the same digest.
    assert trace_record_snapshot()["digest"] == snap["digest"]

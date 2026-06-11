"""Local bucket TraceRecord store behavior."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from opentraces_schema import Agent, Step, TraceRecord


def _enroll_project(project_dir: Path, project_id: str) -> None:
    from opentraces.core.config import get_project_traces_dir

    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".opentraces.json").write_text(
        json.dumps({"marker_version": "2", "project_id": project_id})
    )
    get_project_traces_dir(project_dir).mkdir(parents=True, exist_ok=True)


def _write_project_trace(project_dir: Path, record: TraceRecord) -> Path:
    from opentraces.core.config import get_project_traces_dir

    trace_path = get_project_traces_dir(project_dir) / f"{record.trace_id}.jsonl"
    trace_path.write_text(record.model_dump_json() + "\n")
    return trace_path


def _trace(trace_id: str, content: str = "Patch database client setup") -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
        task={"description": content},
        dependencies=["pymongo"],
        steps=[Step(step_index=1, role="user", content=content)],
        outcome={"success": True, "committed": False},
    )


def _scanned_trace(trace_id: str, content: str = "Patch database client setup") -> TraceRecord:
    from opentraces.security import SECURITY_VERSION

    record = _trace(trace_id, content)
    record.security.scanned = True
    record.security.classifier_version = SECURITY_VERSION
    return record


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def test_local_trace_records_sync_to_bucket_and_prune(tmp_path):
    from opentraces.core import paths
    from opentraces.core.bucket_store import (
        iter_trace_record_objects,
        sync_trace_records_from_local_stores,
        trace_record_snapshot,
        trace_records_root,
    )

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    trace_path = _write_project_trace(project, _trace("trace-bucket-1"))

    first = sync_trace_records_from_local_stores()
    assert first.written == 1
    assert first.unchanged == 0
    assert trace_records_root() == paths.OPENTRACES_DIR / "bucket" / "objects" / "traces" / "v1"

    objects = iter_trace_record_objects()
    assert [obj.trace_id for obj in objects] == ["trace-bucket-1"]
    assert objects[0].source_layer == "canonical"
    assert objects[0].project_slug != "_staging"
    assert objects[0].envelope["record_hash"].startswith("sha256:")
    assert objects[0].envelope["security"]["privacy_tier"] == "off"
    assert objects[0].envelope["security"]["syncable"] is False
    assert "bucket_version" not in objects[0].envelope
    assert objects[0].envelope["written_at"].endswith("Z")
    assert "source" not in objects[0].envelope
    assert objects[0].envelope["trace_id"] == "trace-bucket-1"

    snapshot = trace_record_snapshot(include_objects=True)
    assert snapshot["object_count"] == 1
    assert snapshot["digest"].startswith("sha256:")
    assert "bucket_version" not in snapshot
    assert snapshot["objects"][0]["trace_id"] == "trace-bucket-1"

    second = sync_trace_records_from_local_stores()
    assert second.written == 0
    assert second.unchanged == 1

    trace_path.unlink()
    pruned = sync_trace_records_from_local_stores()
    assert pruned.removed == 1
    assert iter_trace_record_objects() == []


def test_bucket_trace_records_are_versioned_and_current_points_to_latest():
    from opentraces.core.bucket_store import (
        iter_trace_record_objects,
        trace_record_path,
        write_trace_record,
    )

    first = write_trace_record(
        _scanned_trace("trace-versioned", "Patch parser logic"),
        project_slug="demo",
        source_layer="canonical",
        legacy_mirror=False,
    )
    second = write_trace_record(
        _scanned_trace("trace-versioned", "Patch renderer logic"),
        project_slug="demo",
        source_layer="canonical",
        legacy_mirror=False,
    )

    assert first.path.exists()
    assert second.path.exists()
    assert first.path != second.path
    assert "objects/traces/v1/demo/trace-versioned" in first.path.as_posix()

    pointer = json.loads(trace_record_path("demo", "trace-versioned").read_text())
    assert pointer["schema_version"] == "opentraces.bucket.trace_record_pointer.v1"
    assert pointer["record_hash"] == second.record_hash
    assert pointer["object_path"].endswith(second.path.name)
    assert [obj.record.task.description for obj in iter_trace_record_objects()] == [
        "Patch renderer logic"
    ]


def test_trace_index_prefers_bucket_and_tracks_legacy_updates(tmp_path):
    from opentraces.core.bucket_store import iter_trace_record_objects
    from opentraces.core.trace_index import query_index, rebuild_index, refresh_index

    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _trace("trace-bucket-query", "Patch parser logic"))

    rebuild_index()
    assert [obj.trace_id for obj in iter_trace_record_objects()] == ["trace-bucket-query"]
    assert [packet.trace_id for packet in query_index(lex="parser")] == ["trace-bucket-query"]

    _write_project_trace(project, _trace("trace-bucket-query", "Patch renderer logic"))
    # Plan 087 U1: query_index no longer auto-refreshes; an explicit refresh
    # drives incremental tracking of the rewritten legacy trace.
    refresh_index()
    assert query_index(lex="parser") == []
    assert [packet.trace_id for packet in query_index(lex="renderer")] == ["trace-bucket-query"]


def test_bucket_security_state_marks_unfiltered_and_stale_records():
    from opentraces.core.bucket_store import write_trace_record
    from opentraces.security import SECURITY_VERSION

    unfiltered = write_trace_record(
        _trace("trace-unfiltered"),
        project_slug="demo",
        source_layer="canonical",
        legacy_mirror=False,
        privacy_tier="off",
    )
    assert unfiltered.envelope["security"]["privacy_tier"] == "off"
    assert unfiltered.envelope["security"]["filtered"] is False
    assert unfiltered.envelope["security"]["syncable"] is False

    stale_record = _trace("trace-stale")
    stale_record.security.scanned = True
    stale_record.security.classifier_version = "0.0.0"
    stale = write_trace_record(
        stale_record,
        project_slug="demo",
        source_layer="canonical",
        legacy_mirror=False,
    )
    assert stale.envelope["security"]["security_version"] != SECURITY_VERSION
    assert stale.envelope["security"]["stale"] is True
    assert stale.envelope["security"]["syncable"] is False


def test_bucket_manifest_status_and_fake_remote(tmp_path, monkeypatch):
    from opentraces.core.bucket_store import (
        bucket_manifest,
        bucket_manifest_path,
        bucket_status,
        fake_remote_diff,
        fake_remote_pull,
        fake_remote_push,
        fake_remote_status,
        iter_trace_record_objects,
        write_trace_record,
    )

    from opentraces.security import SECURITY_VERSION

    record = _trace("trace-bucket-manifest")
    record.security.scanned = True
    record.security.classifier_version = SECURITY_VERSION
    write_trace_record(
        record,
        project_slug="demo",
        source_layer="canonical",
        legacy_mirror=False,
        privacy_tier="medium",
    )

    status = bucket_status()
    bucket = status["bucket"]
    assert bucket["trace_records"]["object_count"] == 1
    assert bucket["trace_records"]["syncable_count"] == 1
    assert bucket["sync"]["eligible"] is True
    assert bucket["digest"].startswith("sha256:")
    assert bucket_manifest_path().exists()

    manifest = bucket_manifest(write=True, include_objects=True)
    assert manifest["trace_records"]["snapshot"]["objects"][0]["trace_id"] == "trace-bucket-manifest"
    assert manifest["raw_sources"]["object_count"] == 0
    assert manifest["trail_events"]["event_count"] == 0

    remote_root = tmp_path / "fake-bucket-remote"
    monkeypatch.setenv("OPENTRACES_FAKE_BUCKET_REMOTE_ROOT", str(remote_root))
    before = fake_remote_status()
    assert before["state"] == "missing"
    pushed = fake_remote_push()
    assert pushed["state"] == "pushed"
    assert pushed["files_copied"] >= 1
    after = fake_remote_status()
    assert after["state"] == "current"
    assert after["remote_digest"] == after["local_digest"]
    assert fake_remote_diff()["different"] is False

    newer = _trace("trace-local-only")
    newer.security.scanned = True
    newer.security.classifier_version = SECURITY_VERSION
    write_trace_record(
        newer,
        project_slug="demo",
        source_layer="canonical",
        legacy_mirror=False,
    )
    dirty = fake_remote_diff()
    assert dirty["different"] is True
    assert dirty["state"] == "local_ahead"

    pulled = fake_remote_pull(force=True)
    assert pulled["state"] == "pulled"
    assert [obj.trace_id for obj in iter_trace_record_objects()] == ["trace-bucket-manifest"]


def test_raw_source_artifact_is_bucket_local_and_manifested(tmp_path):
    from opentraces.core.bucket_store import (
        bucket_manifest,
        raw_source_snapshot,
        write_raw_source_artifact,
    )

    raw = tmp_path / "session.jsonl"
    raw.write_text('{"type":"user","message":"hello"}\n', encoding="utf-8")

    link = write_raw_source_artifact(
        raw,
        trace_id="trace-raw-1",
        project_slug="demo",
        source_kind="claude-code-session-jsonl",
        parser="claude-code",
    )

    assert link["content_digest"].startswith("sha256:")
    assert link["remote_sync"]["eligible"] is True
    assert link["remote_sync"]["scope"] == "private_bucket_only"
    assert link["remote_sync"]["publishable"] is False
    blob = Path(link["blob_path"])
    assert blob.parts[:3] == ("objects", "raw", "v1")

    snapshot = raw_source_snapshot(include_objects=True)
    assert snapshot["object_count"] == 1
    assert snapshot["remote_syncable_count"] == 1
    assert snapshot["objects"][0]["trace_id"] == "trace-raw-1"

    manifest = bucket_manifest(include_objects=True)
    assert manifest["raw_sources"]["object_count"] == 1
    assert manifest["raw_sources"]["remote_syncable_count"] == 1


def test_trail_event_log_exports_into_portable_bucket_segment(tmp_path):
    """Plan 080 §20 Resolution B: events mirror moved from
    ``bucket/events/trail/v1/<repo>/segments/`` to
    ``bucket/events/v1/batches/<seq>-<batch-id>.jsonl.gz`` (gzipped,
    deterministic mtime). The legacy schema version is gone; the new
    index file at ``bucket/events/v1/index.json`` declares
    ``opentraces.bucket.events.v2``.
    """
    import gzip
    from opentraces.core.bucket_store import sync_events_mirror
    from opentraces.core.trails import TrailEventDraft, append_event_batch

    _init_repo(tmp_path)
    append_event_batch(
        tmp_path,
        [
            TrailEventDraft(
                event_type="trace_snapshot_created",
                trace_id="trace-trail-export",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={"snapshot_id": "snapshot-1", "limitations": []},
            ),
        ],
        writer="test-fixture",
    )

    result = sync_events_mirror(tmp_path, repo_id="demo-repo")
    assert result["batches_written"] >= 1

    from opentraces.core import paths
    index_path = paths.bucket_dir() / "events" / "v1" / "index.json"
    assert index_path.exists()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["schema_version"] == "opentraces.bucket.events.v2"
    assert index["batch_count"] >= 1

    batches_dir = paths.bucket_dir() / "events" / "v1" / "batches"
    batch_files = sorted(batches_dir.glob("*.jsonl.gz"))
    assert len(batch_files) >= 1
    rows = gzip.decompress(batch_files[0].read_bytes()).decode("utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["trace_id"] == "trace-trail-export"


def test_incremental_event_mirror_equals_full_rebuild(tmp_path, monkeypatch):
    """Differential-cache guard: an incrementally-synced mirror is byte-identical
    to a from-scratch full rebuild (same filenames, same gzip bytes, same index
    counters) — the invariant behind ``bucket-events-mirror-replay-equals-git``."""
    import shutil
    from opentraces.core import paths
    from opentraces.core.bucket_store import sync_events_mirror
    from opentraces.core.trails import TrailEventDraft, append_event_batch
    from opentraces.core.trails.event_log import invalidate_read_events_cache

    def _append(tid, sid):
        append_event_batch(
            tmp_path,
            [TrailEventDraft(
                event_type="trace_snapshot_created", trace_id=tid, step_index=1,
                capture_method=["hook_posttooluse"],
                payload={"snapshot_id": sid, "limitations": []},
            )],
            writer="test-fixture",
        )

    _init_repo(tmp_path)
    _append("t1", "s1")
    _append("t2", "s2")
    sync_events_mirror(tmp_path, repo_id="demo")          # full (first time)
    invalidate_read_events_cache(tmp_path)
    _append("t3", "s3")
    _append("t4", "s4")
    inc_index = sync_events_mirror(tmp_path, repo_id="demo")  # incremental

    events_dir = paths.bucket_dir() / "events" / "v1"
    batches_dir = events_dir / "batches"
    inc_files = {p.name: p.read_bytes() for p in batches_dir.glob("*.jsonl.gz")}

    # Wipe the mirror and rebuild from scratch.
    shutil.rmtree(events_dir)
    invalidate_read_events_cache(tmp_path)
    full_index = sync_events_mirror(tmp_path, repo_id="demo")
    full_files = {p.name: p.read_bytes() for p in batches_dir.glob("*.jsonl.gz")}

    assert inc_files == full_files, "incremental mirror diverged from full rebuild"
    assert len(inc_files) == 4
    for key in ("batch_count", "last_batch_id", "latest_event_sequence"):
        assert inc_index[key] == full_index[key]
    assert inc_index["batch_count"] == 4


def test_manifest_traces_include_trace_record_store_entries():
    """Issue #31 — manifest-only readers agree with the object store.

    A trace seeded into the bucket TraceRecord object store ONLY (no live
    per-trace v2 envelope) must still appear in ``manifest.traces``: the
    manifest projection self-heals by materializing the per-trace envelope
    from canonical data. ``bucket verify --full`` then passes (the envelope is
    on disk) and the manifest count equals the object count.
    """

    from opentraces.core.bucket_store import (
        bucket_manifest,
        bucket_verify,
        trace_v1_json_path,
        write_trace_record,
    )

    write_trace_record(
        _trace("trace-heal-1"),
        project_slug="project-restored",
        source_layer="canonical",
    )

    manifest = bucket_manifest(write=True, include_objects=False)
    trace_ids = [row["trace_id"] for row in manifest["traces"]]
    assert "trace-heal-1" in trace_ids
    assert len(manifest["traces"]) == manifest["trace_records"]["object_count"]

    # Self-heal materialized the envelope on disk.
    assert trace_v1_json_path("project-restored", "trace-heal-1").exists()

    # Manifest consistency check (bucket verify check 3) is green.
    result = bucket_verify(full=True)
    assert result["ok"] is True, result["errors"]


def test_legacy_in_place_mirrors_never_auto_adopted(tmp_path):
    """Plan 085 S5 — read-in-place. A legacy ``traces/*.jsonl`` trace mirrored
    into the TraceRecord object store by ``sync_trace_records_from_local_stores``
    (what ``trace index rebuild`` runs) must NOT be auto-adopted into a per-trace
    v2 envelope / ``manifest.traces[]`` by the #31 manifest self-heal or the #28
    bucket-sourced repair pass while its in-place JSONL still exists. The legacy
    trace stays readable via the index; the bucket holds only 0.4+ captures.
    """

    from opentraces.core.bucket_store import (
        bucket_manifest,
        bucket_repair,
        bucket_status,
        iter_trace_record_objects,
        sync_trace_records_from_local_stores,
        trace_v1_json_path,
    )
    from opentraces.core.config import get_project_dir

    project = tmp_path / "legacy"
    _enroll_project(project, "feedfacefeedfacefeedfacefeedface")
    record = _scanned_trace("trace-legacy-in-place")
    _write_project_trace(project, record)
    slug = get_project_dir(project).name

    # What `trace index rebuild` does: mirror the legacy store into the
    # bucket's TraceRecord object store (query substrate).
    summary = sync_trace_records_from_local_stores()
    assert summary.written == 1
    assert [obj.trace_id for obj in iter_trace_record_objects()] == [
        "trace-legacy-in-place"
    ]

    # Manifest self-heal must skip the in-place mirror: no envelope, no row.
    manifest = bucket_manifest(write=True, include_objects=False)
    assert manifest["traces"] == []
    assert not trace_v1_json_path(slug, "trace-legacy-in-place").exists()

    # `bucket status` (the S5 journey surface) agrees.
    status = bucket_status()
    assert status["bucket"]["traces"] == []

    # The #28 bucket-sourced repair pass must skip it too.
    result = bucket_repair(dry_run=False)
    assert result["bucket_sourced_traces"] == 0
    manifest = bucket_manifest(write=False, include_objects=False)
    assert manifest["traces"] == []
    assert not trace_v1_json_path(slug, "trace-legacy-in-place").exists()

    # The legacy JSONL is untouched in place.
    traces_dir = get_project_dir(project) / "traces"
    assert (traces_dir / "trace-legacy-in-place.jsonl").exists()


def _simulate_record_only_ingest(project_dir: Path, record: TraceRecord, source_jsonl: Path) -> str:
    """Write the exact on-disk shape ingest.py's ``--trace-record-only`` path produces.

    Mirrors ``src/opentraces/core/ingest.py`` (the unconditional block after
    the staging-JSONL write): in-place ``traces/<trace_id>.jsonl``, a
    ``canonical`` TraceRecord object with ``legacy_mirror=True``, and the
    capture-time raw-source artifact. ``project_per_trace_exports`` is
    deliberately NOT called — the record-only fast path defers projection.
    Returns the project slug.
    """

    from opentraces.core.bucket_store import (
        write_raw_source_artifact,
        write_trace_record,
    )
    from opentraces.core.config import get_project_dir, get_project_traces_dir

    traces_dir = get_project_traces_dir(project_dir)
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{record.trace_id}.jsonl").write_text(record.to_jsonl_line() + "\n")
    slug = get_project_dir(project_dir).name
    write_trace_record(
        record,
        project_slug=slug,
        source_layer="canonical",
        legacy_mirror=True,
    )
    write_raw_source_artifact(
        source_jsonl,
        trace_id=record.trace_id,
        project_slug=slug,
        source_kind="claude-code-session-jsonl",
        parser="claude-code",
    )
    return slug


def test_record_only_ingest_is_materialized_by_manifest_self_heal(tmp_path):
    """PR #63 — record-only staged traces are NOT legacy mirrors.

    The ``--trace-record-only`` ingest fast path writes the in-place JSONL +
    TraceRecord object + raw-source link and defers the per-trace v2
    projection. The deferred projection must actually happen: the #31
    manifest self-heal materializes the envelope instead of skipping it as a
    plan-085-S5 read-in-place legacy mirror (the capture-time raw-source link
    is the provenance discriminator — legacy mirrors never have one).
    """

    from opentraces.core.bucket_store import (
        bucket_manifest,
        bucket_status,
        trace_v1_json_path,
    )

    project = tmp_path / "hot"
    _enroll_project(project, "cafef00dcafef00dcafef00dcafef00d")
    record = _scanned_trace("trace-record-only-1")
    source_jsonl = tmp_path / "session-record-only-1.jsonl"
    source_jsonl.write_text('{"type":"user","message":{"content":"hi"}}\n')
    slug = _simulate_record_only_ingest(project, record, source_jsonl)

    # The deferred projection happens at manifest time: row + envelope.
    manifest = bucket_manifest(write=True, include_objects=False)
    assert [row["trace_id"] for row in manifest["traces"]] == ["trace-record-only-1"]
    assert trace_v1_json_path(slug, "trace-record-only-1").exists()

    # `bucket status` (manifest-only reader) agrees.
    status = bucket_status()
    assert [row["trace_id"] for row in status["bucket"]["traces"]] == [
        "trace-record-only-1"
    ]

    # The in-place JSONL stays untouched (record-only contract: the staged
    # source is not consumed by materialization).
    from opentraces.core.config import get_project_dir

    assert (get_project_dir(project) / "traces" / "trace-record-only-1.jsonl").exists()


def test_record_only_ingest_is_materialized_by_bucket_repair(tmp_path):
    """PR #63 — `bucket repair` (and `bucket rebuild`) must project the
    deferred record-only trace via the #28 bucket-sourced pass instead of
    skipping it as a legacy in-place mirror.
    """

    from opentraces.core.bucket_store import (
        bucket_manifest,
        bucket_repair,
        rebuild_bucket_traces,
        trace_v1_json_path,
    )

    project = tmp_path / "hot"
    _enroll_project(project, "deadbeefdeadbeefdeadbeefdeadbeef")
    record = _scanned_trace("trace-record-only-2")
    source_jsonl = tmp_path / "session-record-only-2.jsonl"
    source_jsonl.write_text('{"type":"user","message":{"content":"hi"}}\n')
    slug = _simulate_record_only_ingest(project, record, source_jsonl)

    result = bucket_repair(dry_run=False)
    assert result["bucket_sourced_traces"] == 1
    assert trace_v1_json_path(slug, "trace-record-only-2").exists()

    manifest = bucket_manifest(write=False, include_objects=False)
    assert [row["trace_id"] for row in manifest["traces"]] == ["trace-record-only-2"]

    # `bucket rebuild --substrate traces` takes the same bucket-sourced pass.
    rebuilt = rebuild_bucket_traces()
    assert rebuilt["bucket_sourced_traces"] == 1

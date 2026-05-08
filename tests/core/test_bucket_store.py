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
    assert objects[0].envelope["security"]["privacy_tier"] == "medium"
    assert objects[0].envelope["security"]["syncable"] is True
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
    from opentraces.core.trace_index import query_index, rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _trace("trace-bucket-query", "Patch parser logic"))

    rebuild_index()
    assert [obj.trace_id for obj in iter_trace_record_objects()] == ["trace-bucket-query"]
    assert [packet.trace_id for packet in query_index(lex="parser")] == ["trace-bucket-query"]

    _write_project_trace(project, _trace("trace-bucket-query", "Patch renderer logic"))
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
    assert fake_remote_diff()["different"] is True

    pulled = fake_remote_pull()
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
    from opentraces.core.bucket_store import bucket_manifest, sync_trail_events_from_repo
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

    head = sync_trail_events_from_repo(tmp_path, repo_id="demo-repo")
    assert head["schema_version"] == "opentraces.bucket.trail_events_export.v1"
    assert head["event_count"] == 1
    assert head["segments"][0]["path"].startswith("events/trail/v1/")
    segment = Path(head["segments"][0]["path"])
    from opentraces.core import paths

    rows = (paths.bucket_dir() / segment).read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["trace_id"] == "trace-trail-export"

    manifest = bucket_manifest(include_objects=True)
    assert manifest["trail_events"]["repository_count"] == 1
    assert manifest["trail_events"]["event_count"] == 1

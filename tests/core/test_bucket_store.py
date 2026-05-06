"""Local bucket TraceRecord store behavior."""

from __future__ import annotations

import json
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
    assert trace_records_root() == paths.OPENTRACES_DIR / "bucket" / "trace-records"

    objects = iter_trace_record_objects()
    assert [obj.trace_id for obj in objects] == ["trace-bucket-1"]
    assert objects[0].source_layer == "canonical"
    assert objects[0].project_slug != "_staging"
    assert objects[0].envelope["record_hash"].startswith("sha256:")
    assert "bucket_version" not in objects[0].envelope
    assert "written_at" not in objects[0].envelope
    assert "source" not in objects[0].envelope
    assert "trace_id" not in objects[0].envelope

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

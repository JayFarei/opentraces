"""Local bucket-shaped search projection behavior."""

from __future__ import annotations

import json
from pathlib import Path

from opentraces_schema import Agent, Observation, Step, ToolCall, TraceRecord


def _enroll_project(project_dir: Path, project_id: str) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".opentraces.json").write_text(
        json.dumps({"marker_version": "2", "project_id": project_id})
    )


def _write_project_trace(project_dir: Path, record: TraceRecord) -> None:
    from opentraces.core.config import get_project_traces_dir

    traces_dir = get_project_traces_dir(project_dir)
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{record.trace_id}.jsonl").write_text(record.model_dump_json() + "\n")


def _trace() -> TraceRecord:
    return TraceRecord(
        trace_id="trace-local-search-projection",
        session_id="session-local-search-projection",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "Build a clack prompt and prove the parser path"},
        dependencies=["@clack/prompts", "pytest"],
        steps=[
            Step(
                step_index=1,
                role="user",
                content="Use clack to add an interactive prompt, then run the parser test.",
            ),
            Step(
                step_index=2,
                role="agent",
                content="Plan:\n- inspect prompt code\n- edit the parser prompt\n- run pytest",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-read",
                        tool_name="Read",
                        input={"file_path": "src/prompt.ts"},
                    ),
                    ToolCall(
                        tool_call_id="tc-edit",
                        tool_name="Edit",
                        input={
                            "file_path": "src/prompt.ts",
                            "old_string": "text({ message: 'Name' })",
                            "new_string": "confirm({ message: 'Ship it?' })",
                        },
                    ),
                    ToolCall(
                        tool_call_id="tc-test",
                        tool_name="Bash",
                        input={"command": "pytest tests/test_parser.py"},
                    ),
                ],
                observations=[
                    Observation(
                        source_call_id="tc-test",
                        content="tests/test_parser.py . 1 passed",
                        output_summary="1 passed",
                    )
                ],
            ),
        ],
        outcome={"success": True, "committed": False},
    )


def test_search_projection_writes_immutable_local_bucket_build(tmp_path):
    from opentraces.core import paths
    from opentraces.core.search_projection import (
        SEARCH_DOC_SCHEMA_VERSION,
        build_search_projection,
        search_projection_status,
    )
    from opentraces.core.trace_index import rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace())

    rebuild_index()
    summary = build_search_projection()

    assert summary.root_path == paths.OPENTRACES_DIR / "bucket" / "projections" / "search" / "v1"
    assert summary.doc_count == summary.unit_count
    assert summary.doc_count > 1
    assert summary.trace_count == 1
    assert summary.docs_path.exists()
    assert summary.sqlite_path.exists()
    assert summary.manifest_path.exists()
    assert summary.current_path.exists()

    docs = [json.loads(line) for line in summary.docs_path.read_text().splitlines()]
    trace_doc = next(doc for doc in docs if doc["doc_type"] == "trace")
    assert trace_doc["schema_version"] == SEARCH_DOC_SCHEMA_VERSION
    assert trace_doc["trace_id"] == "trace-local-search-projection"
    assert trace_doc["doc_id"] == "sd:tu:trace-local-search-projection:trace"
    assert "clack" in trace_doc["text"].lower()
    assert trace_doc["content_hash"]
    assert "ot://trace/trace-local-search-projection/map" in trace_doc["evidence_refs"]

    pointer = json.loads(summary.current_path.read_text())
    assert pointer["build_id"] == summary.build_id
    assert pointer["manifest_path"] == f"builds/{summary.build_id}/manifest.json"

    status = search_projection_status()
    assert status["state"] == "ok"
    assert status["build_id"] == summary.build_id
    assert status["doc_count"] == summary.doc_count
    assert status["sqlite_path"] == str(summary.sqlite_path)
    assert status["embedding_ready"] is False

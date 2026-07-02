"""CLI coverage for local Trace Index projection commands."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces_schema import Agent, Step, ToolCall, TraceRecord


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
        trace_id="trace-local-index-cli",
        session_id="session-local-index-cli",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "Patch local clack prompt workflow"},
        dependencies=["@clack/prompts"],
        steps=[
            Step(
                step_index=1,
                role="user",
                content="Patch the local clack prompt workflow and run a focused test.",
            ),
            Step(
                step_index=2,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-edit",
                        tool_name="Edit",
                        input={
                            "file_path": "src/workflow.py",
                            "old_string": "return False",
                            "new_string": "return True",
                        },
                    ),
                    ToolCall(
                        tool_call_id="tc-test",
                        tool_name="Bash",
                        input={"command": "pytest tests/test_workflow.py"},
                    ),
                ],
            ),
        ],
        outcome={"success": True, "committed": False},
    )


def _mongo_trace() -> TraceRecord:
    return TraceRecord(
        trace_id="trace-local-semantic-mongo",
        session_id="session-local-semantic-mongo",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "Patch database client setup"},
        dependencies=["pymongo"],
        steps=[
            Step(
                step_index=1,
                role="user",
                content="Patch the database client setup and add a smoke test.",
            ),
            Step(
                step_index=2,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-write",
                        tool_name="Write",
                        input={
                            "file_path": "src/db.py",
                            "content": "from pymongo import MongoClient\nclient = MongoClient(uri)\n",
                        },
                    ),
                    ToolCall(
                        tool_call_id="tc-test",
                        tool_name="Bash",
                        input={"command": "pytest tests/test_db.py"},
                    ),
                ],
            ),
        ],
        outcome={"success": True, "committed": False},
    )


def test_trace_index_rebuild_and_status_emit_local_search_projection(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace())

    # `trace map`/`trace get`/`trace slice` still read the legacy Trace Index,
    # which capture-time keep-warm owns now (the snapshot rebuild below must
    # not touch it). Build it the way maintenance does.
    from opentraces.core.trace_index import rebuild_index

    rebuild_index()

    runner = CliRunner()
    rebuilt = runner.invoke(main, ["trace", "index", "rebuild", "--json"])
    assert rebuilt.exit_code == 0, rebuilt.output
    payload = json.loads(rebuilt.output)
    assert payload["status"] == "ok"
    assert payload["search_snapshot"]["trace_count"] == 1
    assert payload["search_snapshot"]["schema_version"]
    assert payload["search_snapshot"]["source_hash"]
    assert Path(payload["search_snapshot"]["path"]).exists()
    # The legacy index is no longer rebuilt (or reported) by this command.
    assert "index" not in payload

    status = runner.invoke(main, ["trace", "index", "status", "--json"])
    assert status.exit_code == 0, status.output
    status_payload = json.loads(status.output)
    assert status_payload["status"] == "ok"
    snapshot = status_payload["search_snapshot"]
    assert snapshot["state"] == "ok"
    assert snapshot["trace_count"] == 1
    assert snapshot["dirty"] is False
    assert snapshot["wal_exists"] is False
    assert snapshot["shm_exists"] is False
    # Status reports the exact build the rebuild produced.
    assert snapshot["source_hash"] == payload["search_snapshot"]["source_hash"]

    query = runner.invoke(
        main,
        ["trace", "query", "--lex", "clack", "--json"],
    )
    assert query.exit_code == 0, query.output
    query_payload = json.loads(query.output)
    assert query_payload["source"] == "snapshot"
    assert query_payload["total"] >= 1
    assert query_payload["candidates"][0]["trace_id"] == "trace-local-index-cli"
    # Lexical queries ride the snapshot's FTS table; the legacy
    # projection_lexical score part is gone (BM25 can round to 0.0 on a
    # one-doc corpus, so assert the shape + path, not a positive score).
    assert query_payload["search_diagnostics"]["used_search_snapshot"] is True
    assert query_payload["search_diagnostics"]["used_fts"] is True
    assert set(query_payload["candidates"][0]["score_parts"]) <= {"snapshot_fts"}

    # --source projection is no longer a query-time lifecycle selector.
    legacy_source = runner.invoke(
        main,
        ["trace", "query", "--source", "projection", "--lex", "clack", "--json"],
    )
    assert legacy_source.exit_code == 2
    assert "no longer a query-time lifecycle selector" in legacy_source.output

    get_result = runner.invoke(
        main,
        ["trace", "get", "trace-local-index-cli", "--json"],
    )
    assert get_result.exit_code == 0, get_result.output
    get_payload = json.loads(get_result.output)
    # v7: bounded overview + uniform L5 envelope (opentraces.trace.get.v1).
    assert get_payload["schema_version"] == "opentraces.trace.get.v1"
    assert get_payload["trace"]["trace_id"] == "trace-local-index-cli"
    assert get_payload["trace"]["step_count"] >= 1

    slice_result = runner.invoke(
        main,
        [
            "trace",
            "slice",
            "trace-local-index-cli",
            "--from-step",
            "1",
            "--to-step",
            "2",
            "--json",
        ],
    )
    assert slice_result.exit_code == 0, slice_result.output
    slice_payload = json.loads(slice_result.output)
    assert slice_payload["slices"][0]["trace_id"] == "trace-local-index-cli"

    burst_slice = runner.invoke(
        main,
        ["trace", "slice", "trace-local-index-cli", "--template", "bursts", "--json"],
    )
    assert burst_slice.exit_code == 0, burst_slice.output
    burst_payload = json.loads(burst_slice.output)
    assert burst_payload["slices"][0]["steps"]
    assert "trace_record_unavailable" not in burst_payload["slices"][0]["limitations"]

    workflow_file = tmp_path / "classic-local-dataset.md"
    workflow_file.write_text(
        "---\nname: classic-local-dataset\ndescription: local E2E dataset workflow\n---\n"
        "Use `ot trace query --source projection` and `ot trace slice` to select rows.\n"
    )
    dataset = runner.invoke(
        main,
        [
            "dataset",
            "new",
            "local-e2e",
            "--workflow",
            str(workflow_file),
            "--query-name",
            "local-clack",
            "--query-source",
            "projection",
            "--query-semantic",
            "interactive prompts",
            "--json",
        ],
    )
    assert dataset.exit_code == 0, dataset.output
    dataset_payload = json.loads(dataset.output)
    assert dataset_payload["dataset"]["manifest"]["workflow"]["skill"] == "classic-local-dataset"
    candidate_query = dataset_payload["dataset"]["manifest"]["candidate_query"]
    assert candidate_query["name"] == "local-clack"
    assert candidate_query["args"] == {"semantic": "interactive prompts", "source": "projection"}


def test_trace_query_semantic_uses_projection_aliases(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _mongo_trace())
    # A second, non-mongo trace proves the alias expansion actually filters.
    _write_project_trace(project, _trace())

    runner = CliRunner()
    rebuilt = runner.invoke(main, ["trace", "index", "--json"])
    assert rebuilt.exit_code == 0, rebuilt.output

    result = runner.invoke(
        main,
        ["trace", "query", "--semantic", "mongodb", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["source"] == "snapshot"
    assert payload["semantic_query"]["concept_ids"] == ["service:mongodb"]
    # The "mongodb" alias resolves through the indexed concept table: only the
    # pymongo trace matches, even though the query text never appears in it.
    assert [packet["trace_id"] for packet in payload["candidates"]] == [
        "trace-local-semantic-mongo"
    ]
    # Semantic hits are concept-table joins, not FTS scores, in the snapshot.
    assert payload["candidates"][0]["score_parts"] == {}
    assert payload["search_diagnostics"]["used_search_snapshot"] is True
    assert payload["search_diagnostics"]["used_fts"] is False
    assert payload["search_diagnostics"]["raw_trace_scan"] is False

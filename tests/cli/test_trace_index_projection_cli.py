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
        task={"description": "Patch local prompt workflow"},
        steps=[
            Step(
                step_index=1,
                role="user",
                content="Patch the local prompt workflow and run a focused test.",
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


def test_trace_index_rebuild_and_status_emit_local_search_projection(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace())

    runner = CliRunner()
    rebuilt = runner.invoke(main, ["trace", "index", "rebuild", "--json"])
    assert rebuilt.exit_code == 0, rebuilt.output
    payload = json.loads(rebuilt.output)
    assert payload["status"] == "ok"
    assert payload["index"]["trace_count"] == 1
    assert payload["index"]["unit_count"] > 1
    assert payload["search_projection"]["doc_count"] == payload["index"]["unit_count"]
    assert "/bucket/projections/search/v1/builds/" in payload["search_projection"]["build_path"]

    status = runner.invoke(main, ["trace", "index", "status", "--json"])
    assert status.exit_code == 0, status.output
    status_payload = json.loads(status.output)
    assert status_payload["index"]["exists"] is True
    assert status_payload["index"]["trace_count"] == 1
    assert status_payload["search_projection"]["state"] == "ok"
    assert status_payload["search_projection"]["build_id"] == payload["search_projection"]["build_id"]

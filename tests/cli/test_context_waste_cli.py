"""CLI proof for `trace map/get --waste` (plan 086)."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
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


def _waste_trace() -> TraceRecord:
    def read(idx, minute):
        return Step(
            step_index=idx,
            role="agent",
            timestamp=f"2026-05-28T10:{minute:02d}:00Z",
            tool_calls=[ToolCall(tool_call_id=f"tc{idx}", tool_name="Read", input={"file_path": "src/app.py"})],
            observations=[Observation(source_call_id=f"tc{idx}", content="body")],
        )

    return TraceRecord(
        trace_id="trace-waste-cli",
        session_id="session-waste-cli",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "waste"},
        steps=[
            Step(step_index=1, role="user", content="read the file repeatedly"),
            read(2, 0),
            read(3, 5),
            read(4, 10),
        ],
    )


def _rebuild(runner, project):
    res = runner.invoke(main, ["trace", "query", "--project", project.name, "--force-rebuild", "--json"])
    assert res.exit_code == 0, res.output


def test_waste_get_and_map_parity(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _waste_trace())

    runner = CliRunner()
    _rebuild(runner, project)

    via_get = runner.invoke(main, ["trace", "get", "trace-waste-cli", "--waste", "--json"])
    via_map = runner.invoke(main, ["trace", "map", "trace-waste-cli", "--waste", "--json"])
    assert via_get.exit_code == 0, via_get.output
    assert via_map.exit_code == 0, via_map.output
    # Surface parity: byte-identical payloads.
    assert via_get.output == via_map.output

    payload = json.loads(via_get.output)
    assert payload["status"] == "ok"
    waste = payload["waste"]
    assert waste["schema_version"] == "opentraces.context_waste.v1"
    assert waste["summary"]["repeated_file_read_count"] == 1
    assert waste["fidelity"] == "record"


def test_waste_determinism(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _waste_trace())
    runner = CliRunner()
    _rebuild(runner, project)
    a = runner.invoke(main, ["trace", "get", "trace-waste-cli", "--waste", "--json"])
    b = runner.invoke(main, ["trace", "get", "trace-waste-cli", "--waste", "--json"])
    assert a.output == b.output


def test_waste_run_intel_mutually_exclusive(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _waste_trace())
    runner = CliRunner()
    _rebuild(runner, project)
    res = runner.invoke(main, ["trace", "get", "trace-waste-cli", "--waste", "--run-intel", "--json"])
    assert res.exit_code == 2, res.output

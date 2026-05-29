"""CLI proof for `trace map/get --run-intel` (plan 086)."""

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


def _run_intel_trace() -> TraceRecord:
    def bash(idx, command, obs):
        return Step(
            step_index=idx,
            role="agent",
            tool_calls=[ToolCall(tool_call_id=f"tc{idx}", tool_name="Bash", input={"command": command})],
            observations=[Observation(source_call_id=f"tc{idx}", content=obs)],
        )

    return TraceRecord(
        trace_id="trace-ri-cli",
        session_id="session-ri-cli",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "run intel"},
        steps=[
            Step(step_index=1, role="user", content="run the tests"),
            bash(2, "pytest", "exit code 1\nFAILED"),
            Step(step_index=3, role="user", content="actually that is wrong, try again"),
            bash(4, "pytest", "exit code 0\ntests passed"),
        ],
    )


def _rebuild(runner, project):
    res = runner.invoke(main, ["trace", "query", "--project", project.name, "--force-rebuild", "--json"])
    assert res.exit_code == 0, res.output


def test_run_intel_get_and_map_parity(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _run_intel_trace())

    runner = CliRunner()
    _rebuild(runner, project)

    via_get = runner.invoke(main, ["trace", "get", "trace-ri-cli", "--run-intel", "--json"])
    via_map = runner.invoke(main, ["trace", "map", "trace-ri-cli", "--run-intel", "--json"])
    assert via_get.exit_code == 0, via_get.output
    assert via_map.exit_code == 0, via_map.output
    assert via_get.output == via_map.output

    payload = json.loads(via_get.output)
    assert set(payload) == {"status", "trace_id", "signals", "counts"}
    assert payload["counts"]["failure"] == 1
    assert payload["counts"]["recovery"] == 1
    assert payload["counts"]["resteer"] == 1
    assert sum(payload["counts"].values()) == len(payload["signals"])


def test_run_intel_determinism(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _run_intel_trace())
    runner = CliRunner()
    _rebuild(runner, project)
    a = runner.invoke(main, ["trace", "get", "trace-ri-cli", "--run-intel", "--json"])
    b = runner.invoke(main, ["trace", "get", "trace-ri-cli", "--run-intel", "--json"])
    assert a.output == b.output

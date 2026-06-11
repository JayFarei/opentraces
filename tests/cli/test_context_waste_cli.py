"""CLI proof for `trace map/get --waste` (plan 086)."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces_schema import Agent, Metrics, Observation, Step, ToolCall, TraceRecord


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
    res = runner.invoke(main, ["trace", "index", "--json"])
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
    # Flat envelope (issue #56): schema_version + status live at the top level,
    # no `waste` nesting — mirrors run_intel / trace compare.
    assert set(payload) == {
        "schema_version",
        "status",
        "trace_id",
        "fidelity",
        "thresholds",
        "findings",
        "summary",
        "limitations",
    }
    assert payload["status"] == "ok"
    assert payload["schema_version"] == "opentraces.context_waste.v2"
    assert payload["trace_id"] == "trace-waste-cli"
    assert payload["summary"]["repeated_file_read_count"] == 1
    assert payload["fidelity"] == "record"


def test_waste_determinism(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _waste_trace())
    runner = CliRunner()
    _rebuild(runner, project)
    a = runner.invoke(main, ["trace", "get", "trace-waste-cli", "--waste", "--json"])
    b = runner.invoke(main, ["trace", "get", "trace-waste-cli", "--waste", "--json"])
    assert a.output == b.output


def test_waste_threshold_overrides_work_on_trace_map(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _waste_trace())
    runner = CliRunner()
    _rebuild(runner, project)

    res = runner.invoke(
        main,
        [
            "trace",
            "map",
            "trace-waste-cli",
            "--waste",
            "--large-output-chars",
            "1",
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["thresholds"]["large_output_chars"] == 1
    assert payload["summary"]["large_output_count"] == 3


def test_waste_run_intel_mutually_exclusive(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _waste_trace())
    runner = CliRunner()
    _rebuild(runner, project)
    res = runner.invoke(main, ["trace", "get", "trace-waste-cli", "--waste", "--run-intel", "--json"])
    assert res.exit_code == 2, res.output


def _compare_trace(trace_id: str, steps_count: int) -> TraceRecord:
    rec = TraceRecord(
        trace_id=trace_id,
        session_id=f"sess-{trace_id}",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "compare"},
        steps=[Step(step_index=i, role="user", content="x") for i in range(1, steps_count + 1)],
    )
    rec.metrics = Metrics(total_steps=steps_count, total_input_tokens=100 * steps_count)
    return rec


def test_trace_intelligence_envelopes_share_top_level_keys(tmp_path):
    """Issue #56 regression guard: every trace-intelligence --json envelope
    (--waste, --run-intel, trace compare) carries `schema_version` AND `status`
    at the TOP level of the payload — no per-detector nesting drift."""
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _waste_trace())
    _write_project_trace(project, _compare_trace("cmp-a", 10))
    _write_project_trace(project, _compare_trace("cmp-b", 6))
    runner = CliRunner()
    _rebuild(runner, project)

    waste = runner.invoke(main, ["trace", "get", "trace-waste-cli", "--waste", "--json"])
    run_intel = runner.invoke(main, ["trace", "get", "trace-waste-cli", "--run-intel", "--json"])
    compare = runner.invoke(main, ["trace", "compare", "cmp-a", "cmp-b", "--json"])
    for res in (waste, run_intel, compare):
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output)
        assert "schema_version" in payload, payload
        assert "status" in payload, payload
        assert payload["status"] == "ok", payload

    assert json.loads(waste.output)["schema_version"] == "opentraces.context_waste.v2"
    assert json.loads(run_intel.output)["schema_version"] == "opentraces.run_intel.v1"
    assert json.loads(compare.output)["schema_version"] == "opentraces.trace_compare.v1"

"""CLI proof for `trace compare <a> <b>` (plan 086)."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces_schema import Agent, Metrics, Step, TraceRecord


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


def _trace(trace_id, steps_count, tokens) -> TraceRecord:
    rec = TraceRecord(
        trace_id=trace_id,
        session_id=f"sess-{trace_id}",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "compare"},
        steps=[Step(step_index=i, role="user", content="x") for i in range(1, steps_count + 1)],
    )
    rec.metrics = Metrics(total_steps=steps_count, total_input_tokens=tokens)
    return rec


def _rebuild(runner, project):
    res = runner.invoke(main, ["trace", "query", "--project", project.name, "--force-rebuild", "--json"])
    assert res.exit_code == 0, res.output


def test_trace_compare_json(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _trace("cmp-a", 10, 1000))
    _write_project_trace(project, _trace("cmp-b", 6, 400))

    runner = CliRunner()
    _rebuild(runner, project)

    res = runner.invoke(main, ["trace", "compare", "cmp-a", "cmp-b", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["status"] == "ok"
    assert payload["schema_version"] == "opentraces.trace_compare.v1"
    assert payload["fidelity"] == {"a": "record", "b": "record"}
    delta = payload["delta"]
    assert set(delta) == {"metrics", "quality", "bursts", "signals", "security"}
    assert delta["metrics"]["total_input_tokens"] == {"a": 1000, "b": 400, "delta": -600}
    for block in ("metrics", "bursts", "signals", "security"):
        assert delta[block] is not None
    assert payload["quality_included"] is True


def test_trace_compare_no_quality(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _trace("cmp-a", 10, 1000))
    _write_project_trace(project, _trace("cmp-b", 6, 400))
    runner = CliRunner()
    _rebuild(runner, project)

    res = runner.invoke(main, ["trace", "compare", "cmp-a", "cmp-b", "--no-quality", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["quality_included"] is False
    assert payload["delta"]["quality"] is None


def test_trace_compare_unresolved_exits_6(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _trace("cmp-a", 10, 1000))
    runner = CliRunner()
    _rebuild(runner, project)

    res = runner.invoke(main, ["trace", "compare", "bogus1", "bogus2", "--json"])
    assert res.exit_code == 6, res.output

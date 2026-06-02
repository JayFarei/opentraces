"""Plan 087 U5 — ``trace index refresh`` CLI command + doctor freshness.

``trace index refresh`` is the explicit warm-keeping verb (the manual companion
to the best-effort hooks): it runs the digest-gated cheap sync and reports what
changed. ``opentraces doctor`` gains a search-projection freshness section that
reports fresh/stale WITHOUT a heavy repair.
"""
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from opentraces_schema import Agent, Step, ToolCall, TraceRecord

from opentraces.cli import main
from opentraces.core import search_projection as sp
from opentraces.core import trace_index as ti


def _enroll_project(project_dir: Path, project_id: str) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".opentraces.json").write_text(
        json.dumps({"marker_version": "2", "project_id": project_id})
    )


def _write_project_trace(project_dir: Path, record: TraceRecord) -> None:
    from opentraces.core.config import get_project_traces_dir

    traces_dir = get_project_traces_dir(project_dir)
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{record.trace_id}.jsonl").write_text(
        record.model_dump_json() + "\n"
    )


def _trace_with(trace_id: str, term: str) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": f"Work involving {term}"},
        dependencies=["pytest"],
        steps=[
            Step(step_index=1, role="user", content=f"Use {term}."),
            Step(
                step_index=2,
                role="agent",
                content=f"Editing {term}.",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc",
                        tool_name="Edit",
                        input={
                            "file_path": f"src/{term}.ts",
                            "old_string": "a",
                            "new_string": "b",
                        },
                    ),
                ],
            ),
        ],
        outcome={"success": True, "committed": False},
    )


def _warm(project: Path) -> None:
    ti.rebuild_index()
    sp.build_search_projection()
    ti.cheap_sync_query_state(query_source="index")
    ti.cheap_sync_query_state(query_source="projection")


def test_trace_index_refresh_syncs_new_trace(tmp_path, monkeypatch):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    _write_project_trace(project, _trace_with("trace-gamma", "gamma"))

    rebuild_calls = {"n": 0}
    real_rebuild = ti.rebuild_index

    def spy_rebuild(*args, **kwargs):
        rebuild_calls["n"] += 1
        return real_rebuild(*args, **kwargs)

    monkeypatch.setattr(ti, "rebuild_index", spy_rebuild)

    runner = CliRunner()
    result = runner.invoke(main, ["trace", "index", "refresh", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["synced"] is True
    assert "trace-gamma" in set(payload["changed_trace_ids"])
    assert rebuild_calls["n"] == 0, "refresh must not full-rebuild"

    page = ti.query_index_page(lex="gamma")
    assert any(c.trace_id == "trace-gamma" for c in page.candidates)


def test_trace_index_refresh_steady_state(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    runner = CliRunner()
    result = runner.invoke(main, ["trace", "index", "refresh", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["synced"] is False


def test_doctor_reports_search_projection_freshness(tmp_path, monkeypatch):
    """doctor's trace_index section reports search-projection freshness without
    a heavy repair."""
    from opentraces.core import doctor as doctor_mod

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    build_calls = {"n": 0}
    real_build = sp.build_search_projection

    def spy_build(*args, **kwargs):
        build_calls["n"] += 1
        return real_build(*args, **kwargs)

    monkeypatch.setattr(sp, "build_search_projection", spy_build)

    section = doctor_mod._trace_index_status()
    assert "search_projection_freshness" in section
    fr = section["search_projection_freshness"]
    assert fr["state"] == "ok"
    assert fr["fresh"] is True
    assert build_calls["n"] == 0, "doctor freshness must not build the projection"

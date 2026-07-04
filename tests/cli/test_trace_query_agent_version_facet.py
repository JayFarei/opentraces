"""``trace query --facet agent.version=<v>`` after ``trace index rebuild`` (#212).

Model + harness metadata scoping needs ``agent.version`` as a first-class
Trace Index facet, mirroring the existing ``model``/``agent.name`` facets
(``core/trace_index.py``'s ``_trace_facets``/``_skill_invocation_trace_facets``).
``core.search_projection.SEARCH_DOC_SCHEMA_VERSION`` is bumped exactly once
for this change (v2 -> v3).
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces_schema import Agent, Step, TraceRecord


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


def _trace(trace_id: str, *, agent_version: str | None) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="claude-code", model="anthropic/claude-sonnet-4", version=agent_version),
        task={"description": f"Work on {trace_id}"},
        steps=[
            Step(step_index=1, role="user", content=f"Work on {trace_id}"),
            Step(step_index=2, role="agent", content="Done."),
        ],
    )


def test_search_doc_schema_version_bumped_once_for_agent_version_facet() -> None:
    from opentraces.core.search_projection import SEARCH_DOC_SCHEMA_VERSION

    assert SEARCH_DOC_SCHEMA_VERSION == "opentraces.search_doc.v3"


def test_trace_query_facet_agent_version_after_index_rebuild(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    _enroll_project(project, "d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1")
    _write_project_trace(project, _trace("trace-212-versioned", agent_version="2.1.143"))
    _write_project_trace(project, _trace("trace-212-no-version", agent_version=None))

    runner = CliRunner()
    rebuild = runner.invoke(main, ["trace", "index", "rebuild", "--json"])
    assert rebuild.exit_code == 0, rebuild.output

    result = runner.invoke(
        main,
        ["trace", "query", "--facet", "agent.version=2.1.143", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [c["trace_id"] for c in payload["candidates"]] == ["trace-212-versioned"]

    # The null-version trace never matches a version scope.
    other = runner.invoke(
        main,
        ["trace", "query", "--facet", "agent.version=9.9.9", "--json"],
    )
    assert other.exit_code == 0, other.output
    other_payload = json.loads(other.output)
    assert other_payload["candidates"] == []

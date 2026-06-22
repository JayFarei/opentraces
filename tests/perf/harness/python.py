from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .fixtures import PerfFixture, build_fixture
from .models import PerfScenario


def build_python_callable(
    base_dir: Path,
    home_dir: Path,
    scenario: PerfScenario,
) -> tuple[Callable[[], Any], dict[str, Any]]:
    fixture = build_fixture(base_dir, home_dir, scenario.fixture, scenario.tier)
    target = scenario.target
    if target == "watcher.run_once":
        from opentraces.watcher import daemon as _daemon

        return (
            lambda: _daemon.run_once(fixture.project_dir),
            _metadata(fixture, scenario),
        )
    if target == "ingest.scan_project":
        from opentraces.core.ingest import scan_project

        return (
            lambda: scan_project(fixture.project_dir),
            _metadata(fixture, scenario),
        )
    if target == "graph.load":
        from opentraces.clients.text import graph_renderer as _gr

        opts = _gr.RenderOptions(
            width=80,
            color=False,
            mode="commit",
            show_entities=bool(scenario.params.get("show_entities", True)),
            limit=int(scenario.params.get("limit", 20)),
            page=1,
        )
        return (
            lambda: _gr.load_commits_from_repo(fixture.project_dir, opts),
            _metadata(fixture, scenario),
        )
    if target == "core.inverse_blame":
        from opentraces.core import inverse_blame as _ib

        trace_id = fixture.primary_trace_id
        assert trace_id is not None
        return (
            lambda: _ib.compute(fixture.project_dir, trace_id),
            _metadata(fixture, scenario, trace_id=trace_id),
        )
    raise ValueError(f"unsupported python target {target!r}")


def _metadata(fixture: PerfFixture, scenario: PerfScenario, **extra: Any) -> dict[str, Any]:
    data = {
        "scenario": scenario.name,
        "project_dir": str(fixture.project_dir),
        "trace_count": len(fixture.trace_ids),
        "commit_count": len(fixture.commit_shas),
    }
    data.update(extra)
    return data

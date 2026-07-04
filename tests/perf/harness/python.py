from __future__ import annotations

import os
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
    if target == "capsule.redact_companion_text":
        # #209 (W1): forced serial dispatch. This budget guards the "cheap
        # wins" (memoized pattern building, the entropy upper-bound skip, the
        # anonymize_paths fast-path guard) against a throughput regression on
        # every PR; it deliberately does NOT exercise the ProcessPoolExecutor
        # path (spawning a pool per repetition would make a per-PR smoke
        # budget slow and pool-startup-noisy) — the parallel win itself is
        # proven separately by the real-CLI Part B throughput gate (#209).
        os.environ["OPENTRACES_CAPSULE_REDACT_WORKERS"] = "1"
        from opentraces.core.capsule.companions import redact_companion_text

        assert fixture.companion_text is not None
        text = fixture.companion_text
        return (
            lambda: redact_companion_text(text),
            _metadata(fixture, scenario, companion_bytes=len(text.encode("utf-8"))),
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

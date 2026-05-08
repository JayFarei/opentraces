from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from opentraces.clients.tui import OpenTracesApp
from opentraces.clients.web.server import create_app

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
    if target == "graph_api.load_blame":
        from opentraces.clients.web import graph_api

        sha = fixture.blame_sha
        assert sha is not None
        return (
            lambda: graph_api.load_blame(fixture.project_dir, sha),
            _metadata(fixture, scenario, sha=sha),
        )
    if target == "graph_api.load_inverse_blame":
        from opentraces.clients.web import graph_api

        trace_id = fixture.primary_trace_id
        assert trace_id is not None
        return (
            lambda: graph_api.load_inverse_blame(fixture.project_dir, trace_id),
            _metadata(fixture, scenario, trace_id=trace_id),
        )
    if target.startswith("web."):
        return _build_web_callable(fixture, scenario)
    if target.startswith("tui."):
        return _build_tui_callable(fixture, scenario)
    raise ValueError(f"unsupported python target {target!r}")


@contextmanager
def _cwd(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _build_web_callable(fixture: PerfFixture, scenario: PerfScenario) -> tuple[Callable[[], Any], dict[str, Any]]:
    def run() -> Any:
        with _cwd(fixture.project_dir):
            app = create_app(
                staging_dir=str(fixture.staging_dir),
                state_path=str(fixture.state_path),
            )
            client = app.test_client()
            if scenario.target == "web.api_traces":
                return client.get("/api/traces?limit=500").get_json()
            if scenario.target == "web.api_context":
                return client.get("/api/context").get_json()
            if scenario.target == "web.api_stats":
                return client.get("/api/stats").get_json()
            if scenario.target == "web.api_refresh":
                return client.post("/api/refresh").get_json()
            if scenario.target == "web.api_trace_detail":
                trace_id = fixture.primary_trace_id
                assert trace_id is not None
                return client.get(f"/api/trace/{trace_id}/detail").get_json()
            if scenario.target == "web.api_graph":
                return client.get("/api/graph?limit=20&page=1").get_json()
            if scenario.target == "web.api_blame":
                sha = fixture.blame_sha
                assert sha is not None
                return client.get(f"/api/blame/{sha}").get_json()
            if scenario.target == "web.api_trace_tree":
                trace_id = fixture.primary_trace_id
                assert trace_id is not None
                return client.get(f"/api/traces/{trace_id}/tree").get_json()
        raise ValueError(f"unsupported web target {scenario.target!r}")

    return run, _metadata(fixture, scenario)


def _build_tui_callable(fixture: PerfFixture, scenario: PerfScenario) -> tuple[Callable[[], Any], dict[str, Any]]:
    async def startup() -> None:
        with _cwd(fixture.project_dir):
            app = OpenTracesApp(staging_dir=fixture.staging_dir, limit=500)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()

    async def navigation() -> None:
        with _cwd(fixture.project_dir):
            app = OpenTracesApp(staging_dir=fixture.staging_dir, limit=500)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                for key in ("2", "j", "j", "]", "[", "a", "5"):
                    await pilot.press(key)
                    await pilot.pause()

    async def review_actions() -> None:
        with _cwd(fixture.project_dir):
            app = OpenTracesApp(staging_dir=fixture.staging_dir, limit=500)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                for key in (
                    "2",
                    "space",
                    "3",
                    "space",
                    "u",
                    "i",
                    "i",
                    "question_mark",
                    "question_mark",
                    "5",
                    "right_square_bracket",
                    "left_square_bracket",
                ):
                    await pilot.press(key)
                    await pilot.pause()

    if scenario.target == "tui.startup":
        return lambda: asyncio.run(startup()), _metadata(fixture, scenario)
    if scenario.target == "tui.navigation":
        return lambda: asyncio.run(navigation()), _metadata(fixture, scenario)
    if scenario.target == "tui.review_actions":
        return lambda: asyncio.run(review_actions()), _metadata(fixture, scenario)
    raise ValueError(f"unsupported tui target {scenario.target!r}")


def _metadata(fixture: PerfFixture, scenario: PerfScenario, **extra: Any) -> dict[str, Any]:
    data = {
        "scenario": scenario.name,
        "project_dir": str(fixture.project_dir),
        "trace_count": len(fixture.trace_ids),
        "commit_count": len(fixture.commit_shas),
    }
    data.update(extra)
    return data

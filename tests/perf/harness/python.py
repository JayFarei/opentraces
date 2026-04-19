from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from click.testing import CliRunner

from opentraces.cli import main
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
    if target == "publish.push":
        return _build_push_callable(fixture, scenario)
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

    async def refresh() -> None:
        # Exercises the `r` binding: push RefreshRunnerModal, let the
        # worker-driven scan_project complete, pop the modal, and settle.
        # Directly measures the TUI-initiated ingest path that iter-1
        # (gnhf #8) made cheaper.
        with _cwd(fixture.project_dir):
            app = OpenTracesApp(staging_dir=fixture.staging_dir, limit=500)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                await pilot.press("r")
                # RefreshRunnerModal runs the scan on a worker. Pause
                # until the worker finishes and the modal dismisses.
                for _ in range(8):
                    await pilot.pause()

    if scenario.target == "tui.startup":
        return lambda: asyncio.run(startup()), _metadata(fixture, scenario)
    if scenario.target == "tui.navigation":
        return lambda: asyncio.run(navigation()), _metadata(fixture, scenario)
    if scenario.target == "tui.review_actions":
        return lambda: asyncio.run(review_actions()), _metadata(fixture, scenario)
    if scenario.target == "tui.refresh":
        return lambda: asyncio.run(refresh()), _metadata(fixture, scenario)
    raise ValueError(f"unsupported tui target {scenario.target!r}")


def _build_push_callable(fixture: PerfFixture, scenario: PerfScenario) -> tuple[Callable[[], Any], dict[str, Any]]:
    from opentraces.cli import publish as publish_mod
    from opentraces.core.config import Config
    from opentraces.publish.huggingface.remote_index import RemoteIndex
    from opentraces.publish.huggingface.upload import UploadResult

    class _FakeUploader:
        def __init__(self, token: str, repo_id: str) -> None:
            self.token = token
            self.repo_id = repo_id
            self.api = type(
                "_FakeApi",
                (),
                {
                    "upload_file": staticmethod(lambda **_: None),
                },
            )()

        def ensure_repo_exists(self, private: bool = True) -> None:
            return None

        def fetch_remote_index(self) -> RemoteIndex:
            return RemoteIndex.empty(self.repo_id)

        def upload_traces(self, records: list[Any]) -> UploadResult:
            return UploadResult(
                success=True,
                trace_count=len(records),
                shard_name="data/traces_perf.jsonl",
                repo_url=f"https://huggingface.co/datasets/{self.repo_id}",
                error=None,
            )

        def fetch_all_remote_traces(self) -> list[Any]:
            return []

        def set_gated(self) -> None:
            return None

    def run() -> Any:
        runner = CliRunner()
        cfg = Config(hf_token="perf-token", dataset_visibility="private")
        with _cwd(fixture.project_dir):
            from unittest.mock import patch

            with patch.object(publish_mod, "load_config", return_value=cfg), patch(
                "huggingface_hub.HfApi.whoami",
                return_value={"name": "alice"},
            ), patch.object(publish_mod, "_refresh_dataset_card", return_value=None), patch(
                "opentraces.publish.huggingface.upload.HFUploader",
                _FakeUploader,
            ):
                result = runner.invoke(main, ["push", "-y"])
        if result.exit_code != 0:
            raise AssertionError(result.output)
        return result.output

    return run, _metadata(fixture, scenario)


def _metadata(fixture: PerfFixture, scenario: PerfScenario, **extra: Any) -> dict[str, Any]:
    data = {
        "scenario": scenario.name,
        "project_dir": str(fixture.project_dir),
        "trace_count": len(fixture.trace_ids),
        "commit_count": len(fixture.commit_shas),
    }
    data.update(extra)
    return data

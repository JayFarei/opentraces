from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import (
    _choose_remote_interactively_async,
    _current_project_session_dir,
    main,
)
from opentraces.config import Config


class _FakeOption:
    def __init__(self, value: str, label: str):
        self.value = value
        self.label = label


def test_current_project_session_dir_found(tmp_path, monkeypatch):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    expected = projects_root / project_dir.resolve().as_posix().replace("/", "-")
    expected.mkdir()

    monkeypatch.setattr(
        "opentraces.cli.load_config",
        lambda: Config(projects_path=str(projects_root)),
    )

    assert _current_project_session_dir(project_dir) == expected


def test_current_project_session_dir_missing(tmp_path, monkeypatch):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()

    projects_root = tmp_path / "projects"
    projects_root.mkdir()

    monkeypatch.setattr(
        "opentraces.cli.load_config",
        lambda: Config(projects_path=str(projects_root)),
    )

    assert _current_project_session_dir(project_dir) is None


def test_choose_remote_interactively_async_inside_event_loop(monkeypatch):
    prompts_module = ModuleType("pyclack.prompts")
    core_module = ModuleType("pyclack.core")

    async def fake_select(_prompt, _options, **kwargs):
        return "alice/existing-traces"

    async def fake_text(_prompt, **kwargs):
        return "alice/opentraces"

    prompts_module.select = fake_select
    prompts_module.text = fake_text
    core_module.Option = _FakeOption

    monkeypatch.setitem(sys.modules, "pyclack.prompts", prompts_module)
    monkeypatch.setitem(sys.modules, "pyclack.core", core_module)
    monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: True)
    monkeypatch.setattr("opentraces.cli.load_config", lambda: Config(hf_token="hf_test"))
    monkeypatch.setattr("opentraces.cli._auth_identity", lambda _token: {"name": "alice"})

    class FakeUploader:
        def __init__(self, token: str | None, repo_id: str):
            self.token = token
            self.repo_id = repo_id

        def list_opentraces_datasets(self, username: str):
            assert username == "alice"
            return [{"id": "alice/existing-traces", "private": True}]

    monkeypatch.setattr("opentraces.upload.hf_hub.HFUploader", FakeUploader)

    async def run_test():
        return await _choose_remote_interactively_async("alice/opentraces")

    assert asyncio.run(run_test()) == ("alice/existing-traces", "private")


def test_init_import_existing_flag_imports_backlog(tmp_path, monkeypatch):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    (session_dir / "one.jsonl").write_text("{}\n")

    monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: False)
    monkeypatch.setattr("opentraces.cli._install_capture_hook", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("opentraces.cli._install_skill", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("opentraces.cli._current_project_session_dir", lambda _project_dir, cfg=None: session_dir)

    calls: list[tuple[Path, Path]] = []

    def fake_capture(existing_dir: Path, current_project_dir: Path, cfg=None):
        calls.append((existing_dir, current_project_dir))
        return (1, 0)

    monkeypatch.setattr("opentraces.cli._capture_sessions_into_project", fake_capture)

    runner = CliRunner()
    prev_cwd = Path.cwd()
    try:
        import os

        os.chdir(project_dir)
        result = runner.invoke(
            main,
            ["init", "--review-policy", "review", "--push-policy", "manual", "--import-existing", "--no-hook"],
        )
    finally:
        os.chdir(prev_cwd)

    assert result.exit_code == 0, result.output
    assert calls == [(session_dir, project_dir)]
    assert "Imported existing: 1 (0 errors)" in result.output


def test_init_start_fresh_skips_backlog_import(tmp_path, monkeypatch):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    (session_dir / "one.jsonl").write_text("{}\n")

    monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: False)
    monkeypatch.setattr("opentraces.cli._install_capture_hook", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("opentraces.cli._install_skill", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("opentraces.cli._current_project_session_dir", lambda _project_dir, cfg=None: session_dir)

    calls: list[tuple[Path, Path]] = []

    def fake_capture(existing_dir: Path, current_project_dir: Path, cfg=None):
        calls.append((existing_dir, current_project_dir))
        return (1, 0)

    monkeypatch.setattr("opentraces.cli._capture_sessions_into_project", fake_capture)

    runner = CliRunner()
    prev_cwd = Path.cwd()
    try:
        import os

        os.chdir(project_dir)
        result = runner.invoke(
            main,
            ["init", "--review-policy", "review", "--push-policy", "manual", "--start-fresh", "--no-hook"],
        )
    finally:
        os.chdir(prev_cwd)

    assert result.exit_code == 0, result.output
    assert calls == []
    assert "Existing sessions were left untouched" in result.output

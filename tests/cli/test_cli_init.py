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
from opentraces.core.config import Config, load_project_config, save_project_config


class _FakeOption:
    def __init__(self, value: str, label: str, hint: str | None = None):
        self.value = value
        self.label = label
        self.hint = hint


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

    async def fake_confirm(_prompt, **kwargs):
        return True

    prompts_module.select = fake_select
    prompts_module.text = fake_text
    prompts_module.confirm = fake_confirm
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

        def list_user_datasets(self, username: str):
            assert username == "alice"
            return [{"id": "alice/existing-traces", "private": True, "tagged": True}]

    monkeypatch.setattr("opentraces.publish.huggingface.upload.HFUploader", FakeUploader)

    async def run_test():
        return await _choose_remote_interactively_async("alice/opentraces")

    assert asyncio.run(run_test()) == ("alice/existing-traces", "private")


def test_init_import_existing_flag_imports_backlog(tmp_path, monkeypatch):
    """--import-existing wires the backlog into the inbox.

    Phase-1 live-session ingestion replaced ``_capture_sessions_into_project``
    with ``scan_project`` as the single source of truth. This test now
    asserts the new call shape.
    """
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    (session_dir / "one.jsonl").write_text("{}\n")

    monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: False)
    monkeypatch.setattr("opentraces.cli._install_capture_hook", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("opentraces.cli._install_skill", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("opentraces.cli._current_project_session_dir", lambda _project_dir, cfg=None: session_dir)

    calls: list[Path] = []

    class _Report:
        results = [object()]
        created = 1
        refreshed = 0
        new_generations = 0
        noops = 0
        errored = 0

    def fake_scan(project_dir: Path, **kwargs):
        calls.append(Path(project_dir))
        return _Report()

    monkeypatch.setattr("opentraces.core.ingest.scan_project", fake_scan)

    runner = CliRunner()
    prev_cwd = Path.cwd()
    try:
        import os

        os.chdir(project_dir)
        result = runner.invoke(
            main,
            ["init", "--import-existing"],
        )
    finally:
        os.chdir(prev_cwd)

    assert result.exit_code == 0, result.output
    assert calls == [project_dir], (
        "init should route backlog import through scan_project"
    )
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

    calls: list[Path] = []

    def fake_scan(project_dir: Path, **kwargs):
        calls.append(Path(project_dir))
        class _R:
            results = []
            created = refreshed = new_generations = noops = errored = 0
        return _R()

    monkeypatch.setattr("opentraces.core.ingest.scan_project", fake_scan)

    runner = CliRunner()
    prev_cwd = Path.cwd()
    try:
        import os

        os.chdir(project_dir)
        result = runner.invoke(
            main,
            ["init", "--start-fresh"],
        )
    finally:
        os.chdir(prev_cwd)

    assert result.exit_code == 0, result.output
    assert calls == [], "--start-fresh must not invoke scan_project"
    assert "Existing traces were left untouched" in result.output


def test_reinit_import_existing_reparses_existing_project(tmp_path, monkeypatch):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    save_project_config(
        project_dir,
        {
            "mode": "review",
            "review_policy": "review",
            "push_policy": "manual",
            "agents": ["claude-code"],
            "visibility": "private",
        },
    )

    monkeypatch.setattr("opentraces.cli._plan043_finalize_identity", lambda _project: None)
    monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: False)
    monkeypatch.setattr("opentraces.cli._install_capture_hook", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("opentraces.cli._install_skill", lambda *_args, **_kwargs: False)

    calls: list[tuple[Path, bool, bool]] = []

    class _Report:
        results = [object(), object()]
        created = 1
        refreshed = 1
        new_generations = 0
        noops = 0
        errored = 0

    def fake_scan(project_dir: Path, **kwargs):
        calls.append((
            Path(project_dir),
            bool(kwargs.get("reparse")),
            bool(kwargs.get("reconcile_trails")),
        ))
        return _Report()

    monkeypatch.setattr("opentraces.core.ingest.scan_project", fake_scan)

    runner = CliRunner()
    prev_cwd = Path.cwd()
    try:
        import os

        os.chdir(project_dir)
        result = runner.invoke(main, ["init", "--import-existing"])
    finally:
        os.chdir(prev_cwd)

    assert result.exit_code == 0, result.output
    assert calls == [(project_dir, True, False)]
    assert "Re-imported existing traces: 2 (0 errors, 0 unchanged)" in result.output


def test_reinit_agent_option_merges_existing_project_agents(tmp_path, monkeypatch):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    save_project_config(
        project_dir,
        {
            "mode": "review",
            "review_policy": "review",
            "push_policy": "manual",
            "agents": ["claude-code"],
            "visibility": "private",
        },
    )

    monkeypatch.setattr("opentraces.cli._plan043_finalize_identity", lambda _project: None)
    monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: False)

    hook_calls: list[list[str]] = []
    skill_calls: list[list[str]] = []

    def fake_hook(_project: Path, agents: list[str]):
        hook_calls.append(list(agents))
        return True

    def fake_skill(_project: Path, agents: list[str]):
        skill_calls.append(list(agents))
        return True

    monkeypatch.setattr("opentraces.cli._install_capture_hook", fake_hook)
    monkeypatch.setattr("opentraces.cli._install_skill", fake_skill)

    runner = CliRunner()
    prev_cwd = Path.cwd()
    try:
        import os

        os.chdir(project_dir)
        result = runner.invoke(main, ["init", "--agent", "codex-cli", "--start-fresh"])
    finally:
        os.chdir(prev_cwd)

    assert result.exit_code == 0, result.output
    assert load_project_config(project_dir)["agents"] == ["claude-code", "codex-cli"]
    assert hook_calls == [["codex-cli"]]
    assert skill_calls == [["codex-cli"]]
    assert "Agents updated: claude-code, codex-cli" in result.output


def test_hidden_scan_trace_record_only_skips_side_substrates(tmp_path, monkeypatch):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    save_project_config(
        project_dir,
        {
            "mode": "review",
            "review_policy": "review",
            "push_policy": "manual",
            "agents": ["claude-code"],
            "visibility": "private",
        },
    )

    calls: list[dict[str, object]] = []

    class _Report:
        results = []
        created = refreshed = new_generations = noops = errored = 0

    def fake_scan(project_dir: Path, **kwargs):
        calls.append({"project_dir": Path(project_dir), **kwargs})
        return _Report()

    monkeypatch.setattr("opentraces.core.ingest.scan_project", fake_scan)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "_scan",
            "--reparse",
            "--trace-record-only",
            "--project",
            str(project_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "project_dir": project_dir,
            "reparse": True,
            "paths": None,
            "reconcile_trails": False,
            "emit_substrate_events": False,
        }
    ]

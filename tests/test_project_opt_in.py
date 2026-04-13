"""Opt-in guarantees: capture/TUI/web/push must refuse uninitialized projects."""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.config import (
    Config,
    opted_in_projects,
    project_is_opted_in,
    register_project,
    save_project_config,
    unregister_project,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Isolate ~/.opentraces for tests that mutate the registry."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    from opentraces.core import paths as _paths
    from opentraces.core import config as _config

    opentraces_dir = home / ".opentraces"
    opentraces_dir.mkdir()
    for mod in (_paths, _config):
        monkeypatch.setattr(mod, "OPENTRACES_DIR", opentraces_dir)
        monkeypatch.setattr(mod, "CONFIG_PATH", opentraces_dir / "config.json")
        monkeypatch.setattr(mod, "CREDENTIALS_PATH", opentraces_dir / "credentials")
        monkeypatch.setattr(mod, "STAGING_DIR", opentraces_dir / "staging")
        monkeypatch.setattr(mod, "STATE_PATH", opentraces_dir / "state.json")
        monkeypatch.setattr(mod, "UPLOADED_DIR", opentraces_dir / "uploaded")
    return opentraces_dir


class TestProjectIsOptedIn:
    def test_returns_false_without_config(self, tmp_path) -> None:
        assert project_is_opted_in(tmp_path) is False

    def test_returns_true_after_save(self, tmp_path) -> None:
        save_project_config(tmp_path, {"review_policy": "review"})
        assert project_is_opted_in(tmp_path) is True


class TestRegistry:
    def test_register_is_idempotent(self, tmp_path) -> None:
        cfg = Config()
        assert register_project(cfg, tmp_path) is True
        assert register_project(cfg, tmp_path) is False
        assert str(tmp_path.resolve()) in opted_in_projects(cfg)

    def test_unregister_removes(self, tmp_path) -> None:
        cfg = Config()
        register_project(cfg, tmp_path)
        assert unregister_project(cfg, tmp_path) is True
        assert unregister_project(cfg, tmp_path) is False
        assert opted_in_projects(cfg) == []


class TestCaptureGate:
    def test_capture_noops_without_init(
        self, runner, isolated_home, tmp_path
    ) -> None:
        # A project dir that has NOT been initialized.
        project = tmp_path / "uninitialized"
        project.mkdir()
        result = runner.invoke(
            main, ["_capture", "--project-dir", str(project)]
        )
        assert result.exit_code == 0, result.output
        # Gate short-circuits before anything writes to disk.
        assert not (project / ".opentraces").exists()


class TestTUIGate:
    def test_tui_refuses_without_init(
        self, runner, isolated_home, tmp_path
    ) -> None:
        # CliRunner's isolated_filesystem to chdir into an uninitialized dir.
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(main, ["tui"])
            assert result.exit_code == 2
            assert "has not opted in" in result.output

    def test_tui_accepts_after_init(
        self, runner, isolated_home, tmp_path, monkeypatch
    ) -> None:
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            save_project_config(Path(td), {"review_policy": "review"})

            # Stub the TUI app so we don't actually launch Textual.
            launched: dict[str, bool] = {"ran": False}

            class _StubApp:
                def __init__(self, *a, **kw) -> None:
                    pass

                def run(self) -> None:
                    launched["ran"] = True

            import opentraces.clients.tui as tui_mod
            monkeypatch.setattr(tui_mod, "OpenTracesApp", _StubApp)

            result = runner.invoke(main, ["tui"])
            assert result.exit_code == 0, result.output
            assert launched["ran"] is True


class TestPushGate:
    def test_push_refuses_without_init(
        self, runner, isolated_home, tmp_path
    ) -> None:
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(main, ["push"])
            assert result.exit_code == 2
            assert "has not opted in" in result.output


class TestProjectsList:
    def test_empty_registry(self, runner, isolated_home) -> None:
        result = runner.invoke(main, ["projects", "list"])
        assert result.exit_code == 0
        assert "No projects have opted in" in result.output

    def test_lists_registered(
        self, runner, isolated_home, tmp_path
    ) -> None:
        from opentraces.core.config import load_config, save_config

        project = tmp_path / "proj"
        project.mkdir()
        save_project_config(project, {"review_policy": "auto"})

        cfg = load_config()
        register_project(cfg, project)
        save_config(cfg)

        result = runner.invoke(main, ["projects", "list"])
        assert result.exit_code == 0
        assert str(project.resolve()) in result.output

    def test_flags_stale_registry_entries(
        self, runner, isolated_home, tmp_path
    ) -> None:
        """Path registered but ``.opentraces/`` missing → visible warning."""
        from opentraces.core.config import load_config, save_config

        project = tmp_path / "ghost"
        project.mkdir()
        cfg = load_config()
        register_project(cfg, project)
        save_config(cfg)
        # No save_project_config — so on-disk state is missing.

        result = runner.invoke(main, ["projects", "list"])
        assert result.exit_code == 0
        assert "registered but .opentraces/ missing" in result.output

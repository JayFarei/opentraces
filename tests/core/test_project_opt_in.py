"""Opt-in guarantees for current project-scoped local commands."""
from __future__ import annotations

import json
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
    projects_dir = opentraces_dir / "projects"
    projects_dir.mkdir()
    for mod in (_paths, _config):
        monkeypatch.setattr(mod, "OPENTRACES_DIR", opentraces_dir)
        monkeypatch.setattr(mod, "CONFIG_PATH", opentraces_dir / "config.json")
        monkeypatch.setattr(mod, "CREDENTIALS_PATH", opentraces_dir / "credentials")
        monkeypatch.setattr(mod, "PROJECTS_DIR", projects_dir)
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
        slug = cfg.projects[str(tmp_path.resolve())].slug
        # Autouse tests monkeypatch PROJECTS_DIR per test; import it lazily so
        # this assertion follows the patched location.
        from opentraces.core.config import PROJECTS_DIR

        manifest = PROJECTS_DIR / slug / "project.json"
        assert json.loads(manifest.read_text())["path"] == str(tmp_path.resolve())

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
        assert not (project / ".opentraces.json").exists()
        assert not (project / ".opentraces").exists()


class TestStatusGate:
    def test_status_refuses_without_init(
        self, runner, isolated_home, tmp_path
    ) -> None:
        # CliRunner's isolated_filesystem to chdir into an uninitialized dir.
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(main, ["status"])
            assert result.exit_code == 3
            assert "Not an opentraces project" in result.output

    def test_status_accepts_after_init(
        self, runner, isolated_home, tmp_path
    ) -> None:
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            save_project_config(Path(td), {"review_policy": "review"})

            result = runner.invoke(main, ["status"])
            assert result.exit_code == 0, result.output
            assert "0 traces in inbox" in result.output

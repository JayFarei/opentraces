"""Tests for git-parity ot remote verbs (Step 4 of CLI restructure).

The new verbs:
  ot remote add <name> <url> [--public|--private]
  ot remote set-url <name> <url>
  ot remote rename <old> <new>
  ot remote remove <name>
  ot remote use <name>
  ot remote list [-v]

Plus the HF URL shortcut: <url> accepts either a full `hf://user/repo` URL
or the short form `user/repo` (auto-expanded because HF is the default).

This test file isolates ProjectConfig / state / global config under a
tmp_path HOME so the verbs never touch the real filesystem.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.paths import MARKER_FILENAME


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Isolate HOME so config/state are scoped to tmp_path.

    The ProjectConfig + global config layer reads paths from
    core/paths.py, which derives them from Path.home(). Pointing
    HOME at tmp_path/home gives each test a clean slate.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    from opentraces.core import paths as paths_mod
    from opentraces.core import config as config_mod

    opentraces_dir = home / ".opentraces"
    projects_dir = opentraces_dir / "projects"
    monkeypatch.setattr(paths_mod, "OPENTRACES_DIR", opentraces_dir)
    monkeypatch.setattr(paths_mod, "CONFIG_PATH", opentraces_dir / "config.json")
    monkeypatch.setattr(paths_mod, "CREDENTIALS_PATH", opentraces_dir / "credentials")
    monkeypatch.setattr(paths_mod, "PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(config_mod, "OPENTRACES_DIR", opentraces_dir)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", opentraces_dir / "config.json")
    monkeypatch.setattr(config_mod, "CREDENTIALS_PATH", opentraces_dir / "credentials")
    monkeypatch.setattr(config_mod, "PROJECTS_DIR", projects_dir)
    return home


@pytest.fixture
def project_dir(tmp_path, monkeypatch, isolated_home):
    """A clean project directory with the marker pre-written."""
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)

    from opentraces.core.config import save_project_config

    save_project_config(project, {"review_policy": "review"})
    return project


@pytest.fixture
def runner():
    return CliRunner()


def _read_marker(project_dir) -> dict:
    return json.loads((project_dir / MARKER_FILENAME).read_text())


# ---------------------------------------------------------------------------
# ot remote add <name> <url>
# ---------------------------------------------------------------------------


class TestRemoteAdd:
    def test_add_with_full_url_records_url_and_active(self, runner, project_dir) -> None:
        result = runner.invoke(
            main, ["remote", "add", "origin", "hf://me/dataset", "--private"]
        )
        assert result.exit_code == 0, result.output

        marker = _read_marker(project_dir)
        assert marker["remotes"] == {"origin": {"url": "hf://me/dataset", "visibility": "private"}}
        assert marker["active_remote"] == "origin"

    def test_add_short_form_auto_expands(self, runner, project_dir) -> None:
        """`ot remote add origin me/dataset` should auto-expand to hf://me/dataset
        because HF is the default backend."""
        result = runner.invoke(main, ["remote", "add", "origin", "me/dataset", "--private"])
        assert result.exit_code == 0, result.output

        marker = _read_marker(project_dir)
        assert marker["remotes"]["origin"]["url"] == "hf://me/dataset"

    def test_add_second_remote_does_not_change_active(self, runner, project_dir) -> None:
        runner.invoke(main, ["remote", "add", "origin", "me/a", "--private"])
        runner.invoke(main, ["remote", "add", "upstream", "me/b", "--private"])

        marker = _read_marker(project_dir)
        assert set(marker["remotes"]) == {"origin", "upstream"}
        # First add wins for active_remote.
        assert marker["active_remote"] == "origin"

    def test_add_uses_default_visibility_when_unspecified(self, runner, project_dir) -> None:
        """When neither --public nor --private is given, fall back to
        ProjectConfig.default_visibility (which itself defaults to 'private')."""
        result = runner.invoke(main, ["remote", "add", "origin", "me/dataset"])
        assert result.exit_code == 0, result.output

        marker = _read_marker(project_dir)
        assert marker["remotes"]["origin"]["visibility"] == "private"

    def test_add_public_flag(self, runner, project_dir) -> None:
        result = runner.invoke(
            main, ["remote", "add", "origin", "me/dataset", "--public"]
        )
        assert result.exit_code == 0, result.output

        marker = _read_marker(project_dir)
        assert marker["remotes"]["origin"]["visibility"] == "public"

    def test_add_duplicate_name_errors(self, runner, project_dir) -> None:
        runner.invoke(main, ["remote", "add", "origin", "me/a", "--private"])
        result = runner.invoke(main, ["remote", "add", "origin", "me/b", "--private"])
        assert result.exit_code != 0
        assert "already exists" in result.output.lower() or "exists" in result.output.lower()


# ---------------------------------------------------------------------------
# ot remote set-url
# ---------------------------------------------------------------------------


class TestRemoteSetUrl:
    def test_set_url_updates_existing_remote(self, runner, project_dir) -> None:
        runner.invoke(main, ["remote", "add", "origin", "me/old", "--private"])
        result = runner.invoke(main, ["remote", "set-url", "origin", "me/new"])
        assert result.exit_code == 0, result.output

        marker = _read_marker(project_dir)
        assert marker["remotes"]["origin"]["url"] == "hf://me/new"

    def test_set_url_unknown_remote_errors(self, runner, project_dir) -> None:
        result = runner.invoke(main, ["remote", "set-url", "ghost", "me/x"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# ot remote rename
# ---------------------------------------------------------------------------


class TestRemoteRename:
    def test_rename_updates_config(self, runner, project_dir) -> None:
        runner.invoke(main, ["remote", "add", "origin", "me/a", "--private"])
        result = runner.invoke(main, ["remote", "rename", "origin", "upstream"])
        assert result.exit_code == 0, result.output

        marker = _read_marker(project_dir)
        assert "origin" not in marker["remotes"]
        assert "upstream" in marker["remotes"]
        # active_remote follows the rename.
        assert marker["active_remote"] == "upstream"

    def test_rename_unknown_remote_errors(self, runner, project_dir) -> None:
        result = runner.invoke(main, ["remote", "rename", "ghost", "upstream"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# ot remote remove <name>
# ---------------------------------------------------------------------------


class TestRemoteRemoveByName:
    def test_remove_named_remote(self, runner, project_dir) -> None:
        runner.invoke(main, ["remote", "add", "origin", "me/a", "--private"])
        runner.invoke(main, ["remote", "add", "upstream", "me/b", "--private"])

        result = runner.invoke(main, ["remote", "remove", "origin"])
        assert result.exit_code == 0, result.output

        marker = _read_marker(project_dir)
        assert "origin" not in marker["remotes"]
        assert "upstream" in marker["remotes"]
        # active_remote was origin; should be cleared (or rotated to upstream).
        # We accept either: cleared (None) or rotated to the only remaining one.
        assert marker.get("active_remote") in (None, "upstream")

    def test_remove_unknown_errors(self, runner, project_dir) -> None:
        result = runner.invoke(main, ["remote", "remove", "ghost"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# ot remote use <name>
# ---------------------------------------------------------------------------


class TestRemoteUse:
    def test_use_changes_active_remote(self, runner, project_dir) -> None:
        runner.invoke(main, ["remote", "add", "origin", "me/a", "--private"])
        runner.invoke(main, ["remote", "add", "upstream", "me/b", "--private"])

        result = runner.invoke(main, ["remote", "use", "upstream"])
        assert result.exit_code == 0, result.output

        marker = _read_marker(project_dir)
        assert marker["active_remote"] == "upstream"

    def test_use_unknown_errors(self, runner, project_dir) -> None:
        result = runner.invoke(main, ["remote", "use", "ghost"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# ot remote list
# ---------------------------------------------------------------------------


class TestRemoteList:
    def test_list_with_no_remotes(self, runner, project_dir) -> None:
        result = runner.invoke(main, ["remote", "list"])
        assert result.exit_code == 0, result.output
        # Acceptable output: "no remotes" or empty / informative.
        assert result.output is not None

    def test_list_shows_all_remotes(self, runner, project_dir) -> None:
        runner.invoke(main, ["remote", "add", "origin", "me/a", "--private"])
        runner.invoke(main, ["remote", "add", "upstream", "me/b", "--public"])

        result = runner.invoke(main, ["remote", "list"])
        assert result.exit_code == 0, result.output
        assert "origin" in result.output
        assert "upstream" in result.output

    def test_list_marks_active(self, runner, project_dir) -> None:
        runner.invoke(main, ["remote", "add", "origin", "me/a", "--private"])
        runner.invoke(main, ["remote", "add", "upstream", "me/b", "--public"])
        runner.invoke(main, ["remote", "use", "upstream"])

        result = runner.invoke(main, ["remote", "list"])
        # Some marker on the active line — could be "*", "(active)", etc.
        # Just assert upstream's line indicates active somehow.
        upstream_line = [ln for ln in result.output.splitlines() if "upstream" in ln]
        assert upstream_line
        # Heuristic: active marker present in some form
        assert any(
            "*" in ln or "active" in ln.lower() or "->" in ln for ln in upstream_line
        )

    def test_list_verbose_shows_urls(self, runner, project_dir) -> None:
        runner.invoke(main, ["remote", "add", "origin", "me/a", "--private"])
        result = runner.invoke(main, ["remote", "list", "-v"])
        assert result.exit_code == 0, result.output
        assert "hf://me/a" in result.output

"""Tests for the simplified ``ot remote`` surface.

Current verbs (after the single-remote simplification):
  ot remote add <repo>       — connect to an existing HF dataset
  ot remote create <repo>    — create a new HF dataset and connect
  ot remote remove [repo]    — disconnect; --delete-remote also deletes on HF
  ot remote list [-v]        — list connected remotes

``<repo>`` is ``owner/name``, bare ``name`` (prefixed with the authenticated
HF user), or ``hf://owner/name``. HF-side probe / create / delete are
factored as module-level helpers so tests can monkeypatch them.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.paths import MARKER_FILENAME


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Isolate HOME so config/state are scoped to tmp_path."""
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
    """Clean project directory with the opt-in marker written."""
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)

    from opentraces.core.config import save_project_config

    save_project_config(project, {"review_policy": "review"})
    return project


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def authed(monkeypatch):
    """Pretend we're authenticated as ``me`` on HuggingFace."""
    monkeypatch.setattr(
        "opentraces.cli.load_config",
        lambda: type("C", (), {"hf_token": "fake-token"})(),
    )
    monkeypatch.setattr(
        "opentraces.cli._auth_identity", lambda *a, **k: {"name": "me"}
    )


def _read_marker(project_dir) -> dict:
    return json.loads((project_dir / MARKER_FILENAME).read_text())


def _stub_probe(monkeypatch, existing: dict | None):
    """Stub ``_remote_probe`` to return ``existing`` regardless of repo_id.

    ``existing`` is the dict ``{"private": bool}`` (or None for "missing").
    """
    monkeypatch.setattr("opentraces.cli._remote_probe", lambda repo_id, token: existing)


def _stub_create(monkeypatch, result: bool):
    monkeypatch.setattr("opentraces.cli._remote_create",
                        lambda repo_id, private, token: result)


def _stub_delete(monkeypatch, sink: list):
    monkeypatch.setattr("opentraces.cli._remote_delete",
                        lambda repo_id, token: sink.append(repo_id))


# ---------------------------------------------------------------------------
# ot remote add <repo>
# ---------------------------------------------------------------------------


class TestRemoteAdd:
    def test_add_existing_dataset_connects(self, runner, project_dir, authed, monkeypatch):
        _stub_probe(monkeypatch, {"private": True})
        result = runner.invoke(main, ["remote", "add", "me/dataset"])
        assert result.exit_code == 0, result.output

        marker = _read_marker(project_dir)
        assert marker["remotes"] == {
            "me/dataset": {"url": "hf://me/dataset", "visibility": "private"}
        }
        assert marker["active_remote"] == "me/dataset"

    def test_add_public_visibility_inferred_from_hf(self, runner, project_dir, authed, monkeypatch):
        _stub_probe(monkeypatch, {"private": False})
        result = runner.invoke(main, ["remote", "add", "me/dataset"])
        assert result.exit_code == 0, result.output
        assert _read_marker(project_dir)["remotes"]["me/dataset"]["visibility"] == "public"

    def test_add_short_form_uses_authed_username(self, runner, project_dir, authed, monkeypatch):
        _stub_probe(monkeypatch, {"private": True})
        result = runner.invoke(main, ["remote", "add", "my-traces"])
        assert result.exit_code == 0, result.output
        assert "me/my-traces" in _read_marker(project_dir)["remotes"]

    def test_add_hf_url_prefix(self, runner, project_dir, authed, monkeypatch):
        _stub_probe(monkeypatch, {"private": True})
        result = runner.invoke(main, ["remote", "add", "hf://me/dataset"])
        assert result.exit_code == 0, result.output
        assert "me/dataset" in _read_marker(project_dir)["remotes"]

    def test_add_missing_dataset_errors_with_hint_to_create(self, runner, project_dir, authed, monkeypatch):
        _stub_probe(monkeypatch, None)
        result = runner.invoke(main, ["remote", "add", "me/ghost"])
        assert result.exit_code != 0
        assert "remote create" in result.output

    def test_add_duplicate_errors(self, runner, project_dir, authed, monkeypatch):
        _stub_probe(monkeypatch, {"private": True})
        runner.invoke(main, ["remote", "add", "me/a"])
        result = runner.invoke(main, ["remote", "add", "me/a"])
        assert result.exit_code != 0
        assert "already connected" in result.output.lower()


# ---------------------------------------------------------------------------
# ot remote create <repo>
# ---------------------------------------------------------------------------


class TestRemoteCreate:
    def test_create_new_dataset_connects(self, runner, project_dir, authed, monkeypatch):
        _stub_create(monkeypatch, True)
        result = runner.invoke(main, ["remote", "create", "me/fresh"])
        assert result.exit_code == 0, result.output

        marker = _read_marker(project_dir)
        assert marker["remotes"]["me/fresh"]["visibility"] == "private"
        assert marker["active_remote"] == "me/fresh"

    def test_create_public(self, runner, project_dir, authed, monkeypatch):
        _stub_create(monkeypatch, True)
        result = runner.invoke(main, ["remote", "create", "me/fresh", "--public"])
        assert result.exit_code == 0, result.output
        assert _read_marker(project_dir)["remotes"]["me/fresh"]["visibility"] == "public"

    def test_create_existing_errors_with_hint_to_add(self, runner, project_dir, authed, monkeypatch):
        _stub_create(monkeypatch, False)
        result = runner.invoke(main, ["remote", "create", "me/taken"])
        assert result.exit_code != 0
        assert "remote add" in result.output

    def test_create_requires_auth(self, runner, project_dir, monkeypatch):
        monkeypatch.setattr(
            "opentraces.cli.load_config",
            lambda: type("C", (), {"hf_token": None})(),
        )
        result = runner.invoke(main, ["remote", "create", "me/fresh"])
        assert result.exit_code != 0
        assert "login" in result.output.lower()


# ---------------------------------------------------------------------------
# ot remote remove [repo] [--delete-remote]
# ---------------------------------------------------------------------------


class TestRemoteRemove:
    def test_remove_with_single_remote_needs_no_arg(self, runner, project_dir, authed, monkeypatch):
        _stub_probe(monkeypatch, {"private": True})
        runner.invoke(main, ["remote", "add", "me/a"])

        result = runner.invoke(main, ["remote", "remove"])
        assert result.exit_code == 0, result.output
        assert _read_marker(project_dir).get("remotes", {}) == {}

    def test_remove_ambiguous_errors_when_multiple(self, runner, project_dir, authed, monkeypatch):
        _stub_probe(monkeypatch, {"private": True})
        runner.invoke(main, ["remote", "add", "me/a"])
        runner.invoke(main, ["remote", "add", "me/b"])

        result = runner.invoke(main, ["remote", "remove"])
        assert result.exit_code != 0
        assert "specify" in result.output.lower() or "multiple" in result.output.lower()

    def test_remove_by_name(self, runner, project_dir, authed, monkeypatch):
        _stub_probe(monkeypatch, {"private": True})
        runner.invoke(main, ["remote", "add", "me/a"])
        runner.invoke(main, ["remote", "add", "me/b"])

        result = runner.invoke(main, ["remote", "remove", "me/a"])
        assert result.exit_code == 0, result.output
        remaining = _read_marker(project_dir)["remotes"]
        assert set(remaining) == {"me/b"}

    def test_remove_unknown_errors(self, runner, project_dir, authed, monkeypatch):
        _stub_probe(monkeypatch, {"private": True})
        runner.invoke(main, ["remote", "add", "me/a"])
        result = runner.invoke(main, ["remote", "remove", "ghost"])
        assert result.exit_code != 0

    def test_remove_without_any_errors(self, runner, project_dir):
        result = runner.invoke(main, ["remote", "remove"])
        assert result.exit_code != 0

    def test_delete_remote_calls_hf(self, runner, project_dir, authed, monkeypatch):
        _stub_probe(monkeypatch, {"private": True})
        runner.invoke(main, ["remote", "add", "me/a"])

        deleted: list = []
        _stub_delete(monkeypatch, deleted)

        result = runner.invoke(main, ["remote", "remove", "--delete-remote", "--yes"])
        assert result.exit_code == 0, result.output
        assert deleted == ["me/a"]


# ---------------------------------------------------------------------------
# ot remote list
# ---------------------------------------------------------------------------


class TestRemoteList:
    def test_list_with_no_remotes(self, runner, project_dir):
        result = runner.invoke(main, ["remote", "list"])
        assert result.exit_code == 0, result.output
        assert "no remotes" in result.output.lower()

    def test_list_shows_all_remotes(self, runner, project_dir, authed, monkeypatch):
        _stub_probe(monkeypatch, {"private": True})
        runner.invoke(main, ["remote", "add", "me/a"])
        runner.invoke(main, ["remote", "add", "me/b"])

        result = runner.invoke(main, ["remote", "list"])
        assert result.exit_code == 0, result.output
        assert "me/a" in result.output
        assert "me/b" in result.output

    def test_list_marks_active(self, runner, project_dir, authed, monkeypatch):
        _stub_probe(monkeypatch, {"private": True})
        runner.invoke(main, ["remote", "add", "me/a"])
        runner.invoke(main, ["remote", "add", "me/b"])

        result = runner.invoke(main, ["remote", "list"])
        # First add becomes active; its line carries the '*' marker.
        active_lines = [ln for ln in result.output.splitlines() if "me/a" in ln]
        assert active_lines and "*" in active_lines[0]

    def test_list_verbose_shows_urls(self, runner, project_dir, authed, monkeypatch):
        _stub_probe(monkeypatch, {"private": True})
        runner.invoke(main, ["remote", "add", "me/a"])
        result = runner.invoke(main, ["remote", "list", "-v"])
        assert result.exit_code == 0, result.output
        assert "hf://me/a" in result.output

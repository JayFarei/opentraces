"""Step 9: ot config set rewrite — proper key/value setter with --append.

The legacy ot config set was append-only for specific list keys
(--exclude, --redact). Step 9 makes it a generic
  ot config set <key> <value> [--append] [--project|--global]
while preserving the legacy flags for back-compat.

Step 10 (separate, depends on this) adds --project to the setup
subcommands using the same scope helper.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.paths import MARKER_FILENAME


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
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
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    from opentraces.core.config import save_project_config
    save_project_config(project, {"review_policy": "review"})
    return project


@pytest.fixture
def runner():
    return CliRunner()


class TestConfigSetGenericGlobal:
    def test_set_scalar_global(self, runner, isolated_home) -> None:
        """`ot config set classifier_sensitivity high` writes to global config."""
        result = runner.invoke(main, ["config", "set", "classifier_sensitivity", "high"])
        assert result.exit_code == 0, result.output

        global_cfg = json.loads((isolated_home / ".opentraces" / "config.json").read_text())
        assert global_cfg.get("classifier_sensitivity") == "high"

    def test_set_unknown_key_errors(self, runner, isolated_home) -> None:
        result = runner.invoke(main, ["config", "set", "completely_made_up_key", "x"])
        assert result.exit_code != 0


class TestConfigSetProject:
    def test_set_with_project_flag_writes_marker(self, runner, project_dir) -> None:
        """--project writes to <repo>/.opentraces.json instead of global."""
        result = runner.invoke(
            main, ["config", "set", "review_policy", "auto", "--project"]
        )
        assert result.exit_code == 0, result.output

        marker = json.loads((project_dir / MARKER_FILENAME).read_text())
        assert marker.get("review_policy") == "auto"


class TestConfigSetAppend:
    def test_append_to_list_key(self, runner, isolated_home) -> None:
        """`ot config set custom_redact_strings foo --append` appends."""
        runner.invoke(main, ["config", "set", "custom_redact_strings", "foo", "--append"])
        runner.invoke(main, ["config", "set", "custom_redact_strings", "bar", "--append"])

        global_cfg = json.loads((isolated_home / ".opentraces" / "config.json").read_text())
        assert "foo" in global_cfg.get("custom_redact_strings", [])
        assert "bar" in global_cfg.get("custom_redact_strings", [])


class TestConfigSetLegacyFlags:
    """Back-compat: --exclude, --redact, --pricing-file flags still work
    until Step 15 removes them."""

    def test_legacy_redact_flag_still_works(self, runner, isolated_home) -> None:
        result = runner.invoke(main, ["config", "set", "--redact", "secret-key-123"])
        assert result.exit_code == 0, result.output

        global_cfg = json.loads((isolated_home / ".opentraces" / "config.json").read_text())
        assert "secret-key-123" in global_cfg.get("custom_redact_strings", [])

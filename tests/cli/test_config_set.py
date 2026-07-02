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
        """`ot config set projects_path <dir>` writes to global config."""
        result = runner.invoke(main, ["config", "set", "projects_path", "/tmp/claude-projects"])
        assert result.exit_code == 0, result.output

        global_cfg = json.loads((isolated_home / ".opentraces" / "config.json").read_text())
        assert global_cfg.get("projects_path") == "/tmp/claude-projects"

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


class TestConfigSetGenericRedactAppend:
    """``ot config set custom_redact_strings <value> --append`` is the
    canonical way to add a redaction string after the legacy --redact flag
    was removed in the simplification pass."""

    def test_append_redact_string(self, runner, isolated_home) -> None:
        result = runner.invoke(
            main, ["config", "set", "custom_redact_strings", "secret-key-123", "--append"],
        )
        assert result.exit_code == 0, result.output

        global_cfg = json.loads((isolated_home / ".opentraces" / "config.json").read_text())
        assert "secret-key-123" in global_cfg.get("custom_redact_strings", [])


class TestConfigSetProjectBooleanCoercion:
    """Issue #160 V6 — ``config set --project excluded false`` must persist a
    real boolean, not the truthy string ``"false"`` (the coercion footgun:
    ``is_project_excluded`` reads the marker for a falsy value, so the string
    form never actually opts a project back in)."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("false", False),
            ("0", False),
            ("no", False),
            ("true", True),
            ("1", True),
            ("yes", True),
        ],
    )
    def test_excluded_boolean_round_trip(self, runner, project_dir, raw, expected) -> None:
        result = runner.invoke(
            main, ["config", "set", "excluded", raw, "--project"]
        )
        assert result.exit_code == 0, result.output

        marker = json.loads((project_dir / MARKER_FILENAME).read_text())
        # The load-bearing assertion: a real bool, never the string "false"/"true".
        assert marker.get("excluded") is expected, marker

    def test_excluded_false_actually_reopts_in(self, runner, project_dir) -> None:
        """The footgun's real-world symptom: a truthy string never turns
        `excluded` back off, since is_project_excluded reads the marker for
        a falsy value. Prove the fixed boolean form does."""
        from opentraces.core.config import is_project_excluded, load_config

        runner.invoke(main, ["config", "set", "excluded", "true", "--project"])
        runner.invoke(main, ["config", "set", "excluded", "false", "--project"])

        cfg = load_config()
        assert is_project_excluded(cfg, str(project_dir)) is False

    def test_invalid_boolean_value_errors(self, runner, project_dir) -> None:
        result = runner.invoke(
            main, ["config", "set", "excluded", "maybe", "--project"]
        )
        assert result.exit_code != 0

    def test_non_boolean_portable_field_unaffected(self, runner, project_dir) -> None:
        """Only the boolean-typed portable fields get coerced; string fields
        like review_policy persist the raw string exactly as before."""
        result = runner.invoke(
            main, ["config", "set", "review_policy", "auto", "--project"]
        )
        assert result.exit_code == 0, result.output
        marker = json.loads((project_dir / MARKER_FILENAME).read_text())
        assert marker.get("review_policy") == "auto"


class TestConfigGet:
    def test_get_known_key(self, runner, isolated_home) -> None:
        result = runner.invoke(main, ["config", "get", "config_version"])
        assert result.exit_code == 0, result.output
        assert result.output.strip()

    def test_get_unknown_key_errors(self, runner, isolated_home) -> None:
        result = runner.invoke(main, ["config", "get", "not_a_real_key"])
        assert result.exit_code != 0

    @pytest.mark.parametrize(
        "dead_key", ["dataset_visibility", "classifier_sensitivity", "push_policy"]
    )
    def test_get_deleted_dead_field_errors(self, runner, isolated_home, dead_key) -> None:
        """#160 V5: the three dead config fields are DELETED, not readable.

        `dataset_visibility` had zero readers, top-level
        `classifier_sensitivity` was a no-op duplicate of
        `security.classifier.sensitivity`, and `push_policy` was
        display-only (hard-coded "manual")."""
        result = runner.invoke(main, ["config", "get", dead_key])
        assert result.exit_code != 0
        result = runner.invoke(main, ["config", "set", dead_key, "x"])
        assert result.exit_code != 0

    def test_get_json_envelope(self, runner, isolated_home) -> None:
        result = runner.invoke(main, ["--json", "config", "get", "config_version"])
        assert result.exit_code == 0, result.output
        doc = json.loads(result.output)
        assert doc["status"] == "ok"
        assert doc["schema_version"] == "opentraces.config.get.v1"
        assert doc["key"] == "config_version"
        assert doc["value"]
        assert doc["source"] == "global"

    def test_get_after_set_round_trips(self, runner, isolated_home) -> None:
        runner.invoke(main, ["config", "set", "projects_path", "/tmp/claude-projects"])
        result = runner.invoke(main, ["--json", "config", "get", "projects_path"])
        doc = json.loads(result.output)
        assert doc["value"] == "/tmp/claude-projects"

    def test_get_masks_hf_token(self, runner, isolated_home) -> None:
        from opentraces.core.config import save_credentials

        # hf_token is never persisted via config.json (see save_config's
        # exclude={"hf_token"}); it is resolved dynamically from the
        # credential store, so write it there rather than through the Config
        # model directly.
        save_credentials("hf_super_secret_token")

        result = runner.invoke(main, ["--json", "config", "get", "hf_token"])
        doc = json.loads(result.output)
        assert doc["value"] == "***"

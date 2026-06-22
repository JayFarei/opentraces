"""CLI tests for Plan 032 surfaces: setup trufflehog, doctor, llm-review."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from opentraces.cli import main, SENTINEL


def _extract_json(output: str) -> dict:
    """Pull the JSON payload emitted after the SENTINEL."""
    idx = output.find(SENTINEL)
    assert idx >= 0, f"no sentinel in output:\n{output}"
    payload = output[idx + len(SENTINEL):].strip()
    return json.loads(payload)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point the config module at a tmp HOME so tests don't clobber user state."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    from opentraces.core import paths as _paths

    opentraces_dir = home / ".opentraces"
    opentraces_dir.mkdir()
    projects_dir = opentraces_dir / "projects"
    projects_dir.mkdir()

    from opentraces.core import config as _config
    for mod in (_paths, _config):
        monkeypatch.setattr(mod, "OPENTRACES_DIR", opentraces_dir)
        monkeypatch.setattr(mod, "CONFIG_PATH", opentraces_dir / "config.json")
        monkeypatch.setattr(mod, "CREDENTIALS_PATH", opentraces_dir / "credentials")
        monkeypatch.setattr(mod, "PROJECTS_DIR", projects_dir)
    from opentraces.core import doctor as _doctor
    monkeypatch.setattr(_doctor, "find_trufflehog", lambda: None)
    monkeypatch.setattr(_doctor, "_trace_index_status", lambda: {"health": "not-built"})
    monkeypatch.setattr(_doctor, "_trail_event_log_status", lambda _cwd: {"status": "not-found"})
    monkeypatch.setattr(_doctor, "_post_commit_hook_status", lambda _cwd: {"installed": False})
    monkeypatch.setattr(_doctor, "_attribution_status", lambda _cwd: {"health": "not-initialized"})
    monkeypatch.setattr(_doctor, "_entity_parser_status", lambda: {"installed": False})
    monkeypatch.setattr(
        _doctor,
        "_watcher_status",
        lambda: {
            "platform": "test",
            "installed": False,
            "running": False,
            "last_run_at": None,
            "interval_seconds": None,
            "unit_path": None,
            "health": "not-installed",
        },
    )
    monkeypatch.setattr(_doctor, "_hook_installers", lambda: [])
    return opentraces_dir


class TestSetupTruffleHogVerify:
    def test_setup_security_help_uses_optional_tool_language(self, runner, isolated_config) -> None:
        result = runner.invoke(main, ["setup", "--help"], color=False)
        assert result.exit_code == 0, result.output
        assert "optional deep secret detector" in result.output
        assert "optional dataset-row reviewer" in result.output
        assert "Tier 1.5" not in result.output
        assert "Tier 2" not in result.output

    def test_setup_trufflehog_help_uses_optional_tool_language(self, runner, isolated_config) -> None:
        result = runner.invoke(main, ["setup", "trufflehog", "--help"], color=False)
        assert result.exit_code == 0, result.output
        assert "optional deep secret detector" in result.output
        assert "Tier 1.5" not in result.output

    def test_verify_missing_binary(self, runner, isolated_config, monkeypatch) -> None:
        monkeypatch.setattr(
            "opentraces.security.trufflehog.shutil.which", lambda _: None
        )
        result = runner.invoke(main, ["setup", "trufflehog", "--verify"])
        assert result.exit_code == 3
        payload = _extract_json(result.output)
        assert payload["status"] == "error"
        assert payload["error"]["code"] == "TRUFFLEHOG_MISSING"

    def test_verify_present_enables_config(
        self, runner, isolated_config, monkeypatch
    ) -> None:
        import subprocess

        monkeypatch.setattr(
            "opentraces.security.trufflehog.shutil.which",
            lambda _: "/bin/trufflehog",
        )

        def fake_run(cmd, **kwargs):
            r = subprocess.CompletedProcess(cmd, 0)
            r.stdout = "trufflehog 3.94.3\n"
            r.stderr = ""
            return r

        monkeypatch.setattr(
            "opentraces.security.trufflehog.subprocess.run", fake_run
        )
        result = runner.invoke(main, ["setup", "trufflehog", "--verify"])
        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        assert payload["trufflehog_enabled"] is True
        assert "3.94.3" in payload["trufflehog_version"]


class TestSetupReviewLLM:
    def test_non_interactive_writes_global_config(
        self, runner, isolated_config, monkeypatch,
    ) -> None:
        # Stub the reachability probe so tests don't touch the network.
        import opentraces.cli.setup_review_llm as _inst
        monkeypatch.setattr(
            _inst, "_test_review_llm",
            lambda *a, **k: (True, "stubbed: reachable"),
        )

        result = runner.invoke(main, [
            "setup", "llm-review",
            "--api-format", "openai-compat",
            "--base-url", "http://localhost:11434/v1",
            "--model", "gemma3n:e4b",
            "--no-interactive",
        ])
        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        assert payload["status"] == "ok"
        assert payload["llm_review"]["enabled"] is True
        assert payload["llm_review"]["api_format"] == "openai-compat"
        assert payload["llm_review"]["model"] == "gemma3n:e4b"

        # Round-trips to disk.
        from opentraces.core.config import load_config
        cfg = load_config()
        assert cfg.security.llm_review.enabled is True
        assert cfg.security.llm_review.model == "gemma3n:e4b"

    def test_print_dumps_current_config(self, runner, isolated_config) -> None:
        result = runner.invoke(main, ["setup", "llm-review", "--print"])
        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        # Default shipped config has llm_review present but disabled.
        assert payload["llm_review"]["enabled"] is False
        assert payload["llm_review"]["api_format"] == "openai-compat"

    def test_disable_flips_config_off(self, runner, isolated_config) -> None:
        from opentraces.core.config import Config, save_config
        cfg = Config()
        cfg.security.llm_review.enabled = True
        save_config(cfg)

        result = runner.invoke(main, ["setup", "llm-review", "--disable"])
        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        assert payload["llm_review"]["enabled"] is False


class TestSetupTruffleHogDisable:
    def test_disable_flips_config_off(self, runner, isolated_config) -> None:
        # Write a config that has it enabled so --disable has something to flip.
        from opentraces.core.config import Config, save_config
        cfg = Config()
        cfg.security.trufflehog.enabled = True
        save_config(cfg)

        result = runner.invoke(main, ["setup", "trufflehog", "--disable"])
        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        assert payload["trufflehog_enabled"] is False


class TestSetupReviewPolicyRemoved:
    def test_review_policy_setup_command_is_not_registered(self, runner, isolated_config) -> None:
        result = runner.invoke(main, ["setup", "review-policy", "--help"])
        assert result.exit_code == 2
        assert "No such command" in result.output


class TestDoctor:
    def test_reports_security_version(self, runner, isolated_config) -> None:
        result = runner.invoke(main, ["doctor"])
        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        assert payload["status"] == "ok"
        assert payload["doctor"]["security_version"]
        assert "trufflehog" in payload["doctor"]

    def test_enabled_but_missing_exits_nonzero(
        self, runner, isolated_config, monkeypatch
    ) -> None:
        from opentraces.core.config import Config, save_config
        cfg = Config()
        cfg.security.trufflehog.enabled = True
        save_config(cfg)

        monkeypatch.setattr(
            "opentraces.security.trufflehog.shutil.which", lambda _: None
        )
        result = runner.invoke(main, ["doctor"])
        assert result.exit_code == 3
        payload = _extract_json(result.output)
        assert "ENABLED-BUT-MISSING" in payload["doctor"]["trufflehog"]["status"]

    def test_reports_tool_list(self, runner, isolated_config) -> None:
        """The security tool registry plus the synthetic LLM-review and
        Human-review entries are surfaced under ``security.tools``."""
        result = runner.invoke(main, ["doctor"])
        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        tools = payload["doctor"]["security"]["tools"]
        names = [t["name"] for t in tools]
        assert names == [
            "Regex patterns",
            "Shannon entropy",
            "TruffleHog",
            "Privacy-filter (HF NER)",
            "LLM PII",
            "Business-logic signals",
            "Path anonymiser",
            "Capsule scope (field exclusion)",
            "Content classifier",
            "LLM trace review",
            "Human review",
        ]
        by_name = {t["name"]: t for t in tools}
        assert by_name["Regex patterns"]["state"] == "disabled"
        assert by_name["Shannon entropy"]["state"] == "disabled"
        assert by_name["TruffleHog"]["state"] == "disabled"
        assert by_name["Privacy-filter (HF NER)"]["state"] == "disabled"
        assert by_name["LLM PII"]["state"] == "disabled"
        assert by_name["Path anonymiser"]["state"] == "disabled"
        assert by_name["Content classifier"]["state"] == "disabled"
        assert by_name["LLM trace review"]["state"] == "disabled"
        assert by_name["Human review"]["state"] in {
            "required", "not-required", "not-initialized",
        }

        # Installer-backed tools surface commands. Lightweight local tools can
        # still be invoked directly with `opentraces security sanitize --tools`.
        assert by_name["TruffleHog"]["enable_cmd"]
        assert by_name["TruffleHog"]["disable_cmd"]
        assert by_name["Regex patterns"]["enable_cmd"] is None

    def test_security_flag_trims_output(self, runner, isolated_config) -> None:
        result = runner.invoke(main, ["doctor", "--security"])
        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        doc = payload["doctor"]
        assert "security" in doc
        # Focused subview omits installer-level checks.
        assert "hooks" not in doc
        assert "post_processors" not in doc
        assert "hf_auth" not in doc

    def test_security_flag_still_exits_nonzero_on_missing_binary(
        self, runner, isolated_config, monkeypatch
    ) -> None:
        from opentraces.core.config import Config, save_config
        cfg = Config()
        cfg.security.trufflehog.enabled = True
        save_config(cfg)
        monkeypatch.setattr(
            "opentraces.security.trufflehog.shutil.which", lambda _: None
        )
        result = runner.invoke(main, ["doctor", "--security"])
        assert result.exit_code == 3
        payload = _extract_json(result.output)
        th = next(
            t for t in payload["doctor"]["security"]["tools"]
            if t["name"] == "TruffleHog"
        )
        assert th["state"] == "missing"


class TestLegacyReviewCommandsRemoved:
    def test_root_push_is_not_registered(self, runner, isolated_config) -> None:
        result = runner.invoke(main, ["push", "--help"])
        assert result.exit_code == 2
        assert "No such command" in result.output

    def test_root_llm_review_is_not_registered(self, runner, isolated_config) -> None:
        result = runner.invoke(main, ["llm-review", "--help"])
        assert result.exit_code == 2
        assert "No such command" in result.output

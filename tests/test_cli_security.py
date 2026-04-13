"""CLI tests for Plan 032 surfaces: setup trufflehog, doctor, review-llm."""

from __future__ import annotations

import json
from pathlib import Path

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
    monkeypatch.setattr(_paths, "OPENTRACES_DIR", opentraces_dir)
    monkeypatch.setattr(_paths, "CONFIG_PATH", opentraces_dir / "config.json")
    monkeypatch.setattr(_paths, "CREDENTIALS_PATH", opentraces_dir / "credentials")
    monkeypatch.setattr(_paths, "STAGING_DIR", opentraces_dir / "staging")
    monkeypatch.setattr(_paths, "STATE_PATH", opentraces_dir / "state.json")
    monkeypatch.setattr(_paths, "UPLOADED_DIR", opentraces_dir / "uploaded")

    from opentraces.core import config as _config
    monkeypatch.setattr(_config, "CONFIG_PATH", opentraces_dir / "config.json")
    monkeypatch.setattr(_config, "CREDENTIALS_PATH", opentraces_dir / "credentials")
    monkeypatch.setattr(_config, "OPENTRACES_DIR", opentraces_dir)
    monkeypatch.setattr(_config, "STAGING_DIR", opentraces_dir / "staging")
    monkeypatch.setattr(_config, "STATE_PATH", opentraces_dir / "state.json")
    monkeypatch.setattr(_config, "UPLOADED_DIR", opentraces_dir / "uploaded")
    return opentraces_dir


class TestSetupTruffleHogVerify:
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
        import opentraces.cli.installers as _inst
        monkeypatch.setattr(
            _inst, "_test_review_llm",
            lambda *a, **k: (True, "stubbed: reachable"),
        )

        result = runner.invoke(main, [
            "setup", "review-llm",
            "--provider", "openai",
            "--base-url", "http://localhost:11434/v1",
            "--model", "gemma3n:e4b",
            "--no-interactive",
        ])
        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        assert payload["status"] == "ok"
        assert payload["review_llm"]["enabled"] is True
        assert payload["review_llm"]["provider"] == "openai"
        assert payload["review_llm"]["model"] == "gemma3n:e4b"

        # Round-trips to disk.
        from opentraces.core.config import load_config
        cfg = load_config()
        assert cfg.security.review_llm.enabled is True
        assert cfg.security.review_llm.model == "gemma3n:e4b"

    def test_print_dumps_current_config(self, runner, isolated_config) -> None:
        result = runner.invoke(main, ["setup", "review-llm", "--print"])
        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        # Default shipped config has review_llm present but disabled.
        assert payload["review_llm"]["enabled"] is False
        assert payload["review_llm"]["provider"] == "openai"

    def test_disable_flips_config_off(self, runner, isolated_config) -> None:
        from opentraces.core.config import Config, save_config
        cfg = Config()
        cfg.security.review_llm.enabled = True
        save_config(cfg)

        result = runner.invoke(main, ["setup", "review-llm", "--disable"])
        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        assert payload["review_llm"]["enabled"] is False


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


class TestPushLLMReviewGate:
    def test_blocks_when_no_verdict_exists(
        self, runner, isolated_config, monkeypatch, tmp_path
    ) -> None:
        # Monkeypatch config so push gets past the auth check.
        from opentraces import cli as _cli

        class _Cfg:
            hf_token = "hf_fake"
        monkeypatch.setattr(_cli, "load_config", lambda: _Cfg())

        staging = isolated_config / "staging"
        staging.mkdir()
        monkeypatch.setattr(
            "opentraces.core.config.get_project_staging_dir", lambda _: staging
        )
        monkeypatch.setattr(
            "opentraces.core.inbox.load_traces",
            lambda _p: [{"trace_id": "t1", "metadata": {}}],
        )

        result = runner.invoke(main, ["push", "--llm-review"])
        assert result.exit_code == 3, result.output
        payload = _extract_json(result.output)
        assert payload["error"]["code"] == "LLM_REVIEW_BLOCKED"

    def test_blocks_when_verdict_is_no(
        self, runner, isolated_config, monkeypatch
    ) -> None:
        from opentraces import cli as _cli

        class _Cfg:
            hf_token = "hf_fake"
        monkeypatch.setattr(_cli, "load_config", lambda: _Cfg())

        staging = isolated_config / "staging"
        staging.mkdir()
        monkeypatch.setattr(
            "opentraces.core.config.get_project_staging_dir", lambda _: staging
        )
        monkeypatch.setattr(
            "opentraces.core.inbox.load_traces",
            lambda _p: [{
                "trace_id": "t1",
                "metadata": {
                    "llm_review": {
                        "status": "complete",
                        "shareable": "no",
                        "missed_sensitive_data": "yes",
                    },
                },
            }],
        )
        result = runner.invoke(main, ["push", "--llm-review"])
        assert result.exit_code == 3
        payload = _extract_json(result.output)
        assert payload["error"]["code"] == "LLM_REVIEW_BLOCKED"


class TestReviewLLMFilters:
    """--scope and --trace narrow the slow review to a subset."""

    def test_scope_committed_skips_staged_only_traces(
        self, runner, isolated_config, monkeypatch,
    ) -> None:
        from opentraces.cli import installers as _inst

        # Three fake records with distinct ids; state says only one is COMMITTED.
        records = [
            {"trace_id": "aaa1", "content_hash": "h1", "steps": []},
            {"trace_id": "bbb2", "content_hash": "h2", "steps": []},
            {"trace_id": "ccc3", "content_hash": "h3", "steps": []},
        ]

        class _FakeState:
            def get_trace(self, tid):
                return {"ccc3": {"status": "committed"},
                        "aaa1": {"status": "staged"},
                        "bbb2": {"status": "staged"}}.get(tid)

        monkeypatch.setattr(_inst, "load_traces", lambda _p: records, raising=False)
        monkeypatch.setattr("opentraces.core.inbox.load_traces",
                            lambda _p: records)
        monkeypatch.setattr("opentraces.core.state.StateManager",
                            lambda _p: _FakeState())
        monkeypatch.setattr(
            "opentraces.core.config.get_project_staging_dir",
            lambda _: isolated_config / "staging",
        )
        (isolated_config / "staging").mkdir(exist_ok=True)

        result = runner.invoke(
            main, ["review-llm", "--dry-run", "--provider", "fake",
                   "--scope", "committed"],
        )
        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        assert payload["scope"] == "committed"
        assert payload["matched"] == 1
        assert payload["total_available"] == 3

    def test_trace_prefix_selects_single_record(
        self, runner, isolated_config, monkeypatch,
    ) -> None:
        from opentraces.cli import installers as _inst

        records = [
            {"trace_id": "8a3f1c2d", "content_hash": "h", "steps": []},
            {"trace_id": "b4c90011", "content_hash": "h", "steps": []},
        ]
        monkeypatch.setattr(_inst, "load_traces", lambda _p: records, raising=False)
        monkeypatch.setattr("opentraces.core.inbox.load_traces",
                            lambda _p: records)
        monkeypatch.setattr(
            "opentraces.core.config.get_project_staging_dir",
            lambda _: isolated_config / "staging",
        )
        (isolated_config / "staging").mkdir(exist_ok=True)

        result = runner.invoke(
            main, ["review-llm", "--dry-run", "--provider", "fake",
                   "--trace", "8a3f"],
        )
        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        assert payload["matched"] == 1
        assert payload["trace_ids"] == ["8a3f"]


class TestReviewLLMDryRun:
    def test_dry_run_no_provider_call(self, runner, isolated_config, monkeypatch) -> None:
        # Empty staging dir is fine — dry-run never calls the provider.
        staging = isolated_config / "staging"
        staging.mkdir(exist_ok=True)
        # Patch the project-local staging lookup to this path.
        monkeypatch.setattr(
            "opentraces.core.config.get_project_staging_dir",
            lambda _: staging,
        )
        monkeypatch.setattr(
            "opentraces.core.inbox.load_traces", lambda _path: []
        )
        result = runner.invoke(
            main, ["review-llm", "--dry-run", "--provider", "fake"]
        )
        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        assert payload["dry_run"] is True
        assert payload["sessions"] == 0
        assert "estimate" in payload

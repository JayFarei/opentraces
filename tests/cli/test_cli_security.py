"""CLI tests for Plan 032 surfaces: setup trufflehog, doctor, llm-review."""

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
    projects_dir = opentraces_dir / "projects"
    projects_dir.mkdir()

    from opentraces.core import config as _config
    for mod in (_paths, _config):
        monkeypatch.setattr(mod, "OPENTRACES_DIR", opentraces_dir)
        monkeypatch.setattr(mod, "CONFIG_PATH", opentraces_dir / "config.json")
        monkeypatch.setattr(mod, "CREDENTIALS_PATH", opentraces_dir / "credentials")
        monkeypatch.setattr(mod, "PROJECTS_DIR", projects_dir)
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


class TestSetupReviewPolicy:
    def test_errors_without_init(self, runner, isolated_config, tmp_path) -> None:
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(main, ["setup", "review-policy", "--auto"])
            assert result.exit_code == 2, result.output
            payload = _extract_json(result.output)
            assert payload["error"] == "not-initialized"

    def test_flips_review_to_auto(self, runner, isolated_config, tmp_path) -> None:
        from opentraces.core.config import save_project_config, load_project_config
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            save_project_config(Path(td), {"review_policy": "review"})
            result = runner.invoke(main, ["setup", "review-policy", "--auto"])
            assert result.exit_code == 0, result.output
            payload = _extract_json(result.output)
            assert payload["review_policy"] == "auto"
            assert payload["previous"] == "review"
            assert load_project_config(Path(td))["review_policy"] == "auto"

    def test_conflicting_flags_rejected(self, runner, isolated_config, tmp_path) -> None:
        from opentraces.core.config import save_project_config
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            save_project_config(Path(td), {"review_policy": "review"})
            result = runner.invoke(
                main, ["setup", "review-policy", "--auto", "--review"]
            )
            assert result.exit_code == 2

    def test_print_shows_current(self, runner, isolated_config, tmp_path) -> None:
        from opentraces.core.config import save_project_config
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            save_project_config(Path(td), {"review_policy": "auto"})
            result = runner.invoke(main, ["setup", "review-policy", "--print"])
            assert result.exit_code == 0, result.output
            payload = _extract_json(result.output)
            assert payload["review_policy"] == "auto"


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

    def test_reports_tier_list(self, runner, isolated_config) -> None:
        result = runner.invoke(main, ["doctor"])
        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        tiers = payload["doctor"]["security"]["tiers"]
        names = [t["name"] for t in tiers]
        assert names == [
            "Regex patterns",
            "Shannon entropy",
            "TruffleHog",
            "LLM trace review",
            "Human review",
        ]
        by_name = {t["name"]: t for t in tiers}
        assert by_name["Regex patterns"]["state"] == "always-on"
        assert by_name["Shannon entropy"]["state"] == "always-on"
        assert by_name["TruffleHog"]["state"] == "disabled"
        assert by_name["LLM trace review"]["state"] == "disabled"
        # Human review state is project-policy-driven.
        assert by_name["Human review"]["state"] in {
            "required", "not-required", "not-initialized",
        }
        # Each toggleable tier surfaces its own command.
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
            t for t in payload["doctor"]["security"]["tiers"]
            if t["name"] == "TruffleHog"
        )
        assert th["state"] == "missing"


class TestPushLLMReviewGate:
    """The gate fires before auth so we can exercise it without a real HF token.

    These tests install a fake StateManager that reports ``t1`` as
    COMMITTED — post-scope-fix the gate only checks the staged set,
    so the trace has to be COMMITTED (i.e. "staged" in display vocab)
    for it to be considered.
    """

    @staticmethod
    def _install_fakes(monkeypatch, isolated_config, trace_records):
        from opentraces import cli as _cli

        class _Cfg:
            hf_token = "hf_fake"
        monkeypatch.setattr(_cli, "load_config", lambda: _Cfg())

        staging = isolated_config / "staging"
        staging.mkdir(exist_ok=True)
        monkeypatch.setattr(
            "opentraces.core.config.get_project_traces_dir", lambda _: staging
        )
        monkeypatch.setattr(
            "opentraces.core.inbox.load_traces", lambda _p: trace_records
        )

        committed_ids = {r["trace_id"] for r in trace_records}

        class _FakeEntry:
            def __init__(self, trace_id: str) -> None:
                self.trace_id = trace_id

        class _FakeState:
            def __init__(self, *a, **kw) -> None:
                pass

            def get_traces_by_status(self, _status):
                return [_FakeEntry(tid) for tid in committed_ids]

            def get_trace(self, tid):
                return None

            # Swallow the rest of the StateManager surface the CLI may
            # still poke at after the gate.
            def __getattr__(self, name):
                def _noop(*a, **kw):
                    return None
                return _noop

        # publish.py imports StateManager inside the function body, so
        # monkey-patch the source module.
        monkeypatch.setattr("opentraces.core.state.StateManager", _FakeState)

    def test_blocks_when_no_verdict_exists(
        self, runner, isolated_config, monkeypatch, tmp_path
    ) -> None:
        self._install_fakes(
            monkeypatch, isolated_config,
            [{"trace_id": "t1", "metadata": {}}],
        )
        result = runner.invoke(main, ["push", "--llm-review"])
        assert result.exit_code == 3, result.output
        payload = _extract_json(result.output)
        assert payload["error"]["code"] == "LLM_REVIEW_BLOCKED"

    def test_blocks_when_verdict_is_no(
        self, runner, isolated_config, monkeypatch
    ) -> None:
        self._install_fakes(
            monkeypatch, isolated_config,
            [{
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

    def test_scope_staged_skips_inbox_only_traces(
        self, runner, isolated_config, monkeypatch,
    ) -> None:
        """``--scope staged`` matches only post-add traces (TraceStatus.COMMITTED).

        Display vocab: STAGED status → "inbox", COMMITTED status → "staged".
        """
        from opentraces.cli import installers as _inst

        # Three fake records with distinct ids; state says only one is COMMITTED
        # (which is "staged" in the display vocabulary).
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
            "opentraces.core.config.get_project_traces_dir",
            lambda _: isolated_config / "staging",
        )
        (isolated_config / "staging").mkdir(exist_ok=True)

        result = runner.invoke(
            main, ["llm-review", "--dry-run", "--api-format", "fake",
                   "--scope", "staged"],
        )
        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        assert payload["scope"] == "staged"
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
            "opentraces.core.config.get_project_traces_dir",
            lambda _: isolated_config / "staging",
        )
        (isolated_config / "staging").mkdir(exist_ok=True)

        result = runner.invoke(
            main, ["llm-review", "--dry-run", "--api-format", "fake",
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
            "opentraces.core.config.get_project_traces_dir",
            lambda _: staging,
        )
        monkeypatch.setattr(
            "opentraces.core.inbox.load_traces", lambda _path: []
        )
        result = runner.invoke(
            main, ["llm-review", "--dry-run", "--api-format", "fake"]
        )
        assert result.exit_code == 0, result.output
        payload = _extract_json(result.output)
        assert payload["dry_run"] is True
        assert payload["sessions"] == 0
        assert "estimate" in payload

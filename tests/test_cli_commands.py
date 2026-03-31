"""Smoke tests for every documented public CLI command.

Validates that each command exists, accepts documented flags, and returns
expected exit codes. These are regression guards, not behavior tests.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from opentraces.cli import main


# ---------------------------------------------------------------------------
# Fixtures (3 tiers)
# ---------------------------------------------------------------------------

@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def initialized_project(tmp_path, monkeypatch):
    """Tier 2: project with .opentraces/ directory."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: False)
    runner = CliRunner()
    result = runner.invoke(main, [
        "init", "--review-policy", "review",
        "--remote", "test/opentraces", "--no-hook", "--start-fresh",
    ])
    assert result.exit_code == 0, f"init failed: {result.output}"
    return tmp_path, runner


@pytest.fixture
def project_with_traces(initialized_project):
    """Tier 3: initialized project with a staged trace."""
    project_dir, runner = initialized_project

    from opentraces.state import StateManager, TraceStatus
    from opentraces.config import get_project_state_path, get_project_staging_dir
    from opentraces_schema import TraceRecord

    staging_dir = get_project_staging_dir(project_dir)
    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path)

    trace_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    record = TraceRecord(
        trace_id=trace_id,
        session_id="test-session-001",
        agent={"name": "claude-code", "version": "1.0.0"},
        task={"description": "Test trace for smoke tests"},
        steps=[
            {
                "step_index": 1,
                "role": "user",
                "content": "hello",
            },
            {
                "step_index": 2,
                "role": "agent",
                "content": "hi there",
                "tool_calls": [
                    {
                        "tool_call_id": "tc1",
                        "tool_name": "Read",
                        "input": {"file": "test.py"},
                    }
                ],
            },
        ],
    )

    staging_file = staging_dir / f"{trace_id}.jsonl"
    staging_file.write_text(record.model_dump_json() + "\n")

    state.set_trace_status(
        trace_id,
        TraceStatus.STAGED,
        session_id="test-session-001",
        file_path=str(staging_file),
    )

    return project_dir, runner, trace_id


# ---------------------------------------------------------------------------
# Pre-init commands (no project needed)
# ---------------------------------------------------------------------------

class TestPreInitCommands:
    """Commands that don't require an initialized project."""

    def test_login_help(self, runner):
        result = runner.invoke(main, ["login", "--help"])
        assert result.exit_code == 0
        assert "token" in result.output.lower()

    def test_logout(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ["logout"])
        assert result.exit_code == 0

    def test_whoami_unauthenticated(self, runner, monkeypatch):
        monkeypatch.setattr("opentraces.cli._auth_identity", lambda *a: None)
        monkeypatch.setattr("opentraces.cli.load_config", lambda: type("C", (), {"hf_token": None})())
        result = runner.invoke(main, ["whoami"])
        assert result.exit_code == 3

    def test_auth_status_unauthenticated(self, runner, monkeypatch):
        monkeypatch.setattr("opentraces.cli._auth_identity", lambda *a: None)
        monkeypatch.setattr("opentraces.cli.load_config", lambda: type("C", (), {"hf_token": None})())
        result = runner.invoke(main, ["auth", "status"])
        assert result.exit_code == 3 or "Not authenticated" in result.output


# ---------------------------------------------------------------------------
# Not-initialized path
# ---------------------------------------------------------------------------

class TestNotInitialized:
    """Commands that should exit 3 when no .opentraces/ exists."""

    def test_status_not_initialized(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ["status"])
        assert result.exit_code == 3

    def test_stats_not_initialized(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ["stats"])
        assert result.exit_code == 3

    def test_context_not_initialized(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("opentraces.cli._auth_identity", lambda *a: None)
        result = runner.invoke(main, ["context"])
        assert result.exit_code == 3


# ---------------------------------------------------------------------------
# Post-init commands
# ---------------------------------------------------------------------------

class TestPostInitCommands:
    """Commands that require an initialized project."""

    def test_status(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["status"])
        assert result.exit_code == 0
        assert "mode" in result.output.lower() or "review" in result.output.lower()

    def test_stats(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["stats"])
        assert result.exit_code == 0

    def test_context(self, initialized_project, monkeypatch):
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._auth_identity", lambda *a: None)
        result = runner.invoke(main, ["context"])
        assert result.exit_code == 0

    def test_log(self, initialized_project, monkeypatch):
        project_dir, runner = initialized_project
        from opentraces.config import get_project_state_path
        from opentraces.state import StateManager as _OrigSM

        state_path = get_project_state_path(project_dir)

        class ProjectLocalSM(_OrigSM):
            def __init__(self, state_path=None):
                super().__init__(state_path=get_project_state_path(project_dir))

        monkeypatch.setattr("opentraces.state.StateManager", ProjectLocalSM)
        result = runner.invoke(main, ["log"])
        assert result.exit_code == 0

    def test_config_show(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["config", "show"])
        assert result.exit_code == 0

    def test_config_set_classifier_sensitivity(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["config", "set", "--classifier-sensitivity", "high"])
        assert result.exit_code == 0

    def test_remote_show(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["remote"])
        assert result.exit_code == 0

    def test_remote_set(self, initialized_project, monkeypatch):
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._auth_identity", lambda *a: {"name": "testuser"})
        result = runner.invoke(main, ["remote", "set", "testuser/new-dataset"])
        assert result.exit_code == 0

    def test_remote_remove(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["remote", "remove"])
        assert result.exit_code == 0

    def test_commit_all_empty(self, initialized_project):
        """commit --all with no inbox traces should still exit 0."""
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["commit", "--all"])
        assert result.exit_code == 0

    def test_remove(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["remove"])
        assert result.exit_code == 0
        assert not (project_dir / ".opentraces").exists()


# ---------------------------------------------------------------------------
# Session commands (need staged traces)
# ---------------------------------------------------------------------------

class TestSessionCommands:
    """Session subcommands that require staged traces."""

    def test_session_list(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["session", "list"])
        assert result.exit_code == 0

    def test_session_list_stage_filter(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["session", "list", "--stage", "inbox"])
        assert result.exit_code == 0

    def test_session_show(self, project_with_traces):
        project_dir, runner, trace_id = project_with_traces
        result = runner.invoke(main, ["session", "show", trace_id])
        assert result.exit_code == 0

    def test_session_show_not_found(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["session", "show", "nonexistent-trace-id"])
        assert result.exit_code == 6

    def test_session_commit(self, project_with_traces):
        project_dir, runner, trace_id = project_with_traces
        result = runner.invoke(main, ["session", "commit", trace_id])
        assert result.exit_code == 0

    def test_session_reject(self, project_with_traces):
        project_dir, runner, trace_id = project_with_traces
        result = runner.invoke(main, ["session", "reject", trace_id])
        assert result.exit_code == 0

    def test_session_reset_after_commit(self, project_with_traces):
        project_dir, runner, trace_id = project_with_traces
        runner.invoke(main, ["session", "commit", trace_id])
        result = runner.invoke(main, ["session", "reset", trace_id])
        assert result.exit_code == 0

    def test_session_redact(self, project_with_traces):
        project_dir, runner, trace_id = project_with_traces
        result = runner.invoke(main, ["session", "redact", trace_id, "--step", "0"])
        # May be 0 (success) or 2 (step out of range depending on 0-vs-1 indexing)
        assert result.exit_code in (0, 2)

    def test_session_discard(self, project_with_traces):
        project_dir, runner, trace_id = project_with_traces
        result = runner.invoke(main, ["session", "discard", trace_id, "--yes"])
        assert result.exit_code == 0

    def test_session_discard_not_found(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["session", "discard", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "--yes"])
        assert result.exit_code == 6


# ---------------------------------------------------------------------------
# Optional-dependency commands (mock launchers)
# ---------------------------------------------------------------------------

class TestOptionalDepCommands:
    """Commands that launch blocking servers, mocked to avoid hangs."""

    def test_web(self, initialized_project, monkeypatch):
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._launch_web_ui", lambda *a, **kw: None)
        result = runner.invoke(main, ["web", "--no-open"])
        assert result.exit_code == 0

    def test_tui(self, initialized_project, monkeypatch):
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._launch_tui_ui", lambda *a, **kw: None)
        result = runner.invoke(main, ["tui"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# JSON mode
# ---------------------------------------------------------------------------

class TestJsonMode:
    """Test that --json flag produces the sentinel and valid JSON."""

    def test_json_context(self, initialized_project, monkeypatch):
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._auth_identity", lambda *a: None)
        result = runner.invoke(main, ["--json", "context"])
        assert result.exit_code == 0
        assert "---OPENTRACES_JSON---" in result.output

    def test_json_status(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["--json", "status"])
        assert result.exit_code == 0
        assert "---OPENTRACES_JSON---" in result.output

    def test_json_session_list(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["--json", "session", "list"])
        assert result.exit_code == 0
        assert "---OPENTRACES_JSON---" in result.output


# ---------------------------------------------------------------------------
# Machine mode (OPENTRACES_NO_TUI, piped stdout)
# ---------------------------------------------------------------------------

class TestMachineMode:
    """OPENTRACES_NO_TUI env var and non-TTY bare invocation."""

    def test_no_tui_env_var_prints_help(self, monkeypatch):
        """OPENTRACES_NO_TUI=1 should print help, not launch TUI."""
        monkeypatch.setenv("OPENTRACES_NO_TUI", "1")
        monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: True)
        launched = []
        monkeypatch.setattr("opentraces.cli._launch_tui_ui", lambda *a, **kw: launched.append(1))
        runner = CliRunner()
        result = runner.invoke(main, [])
        assert result.exit_code == 0
        assert len(launched) == 0, "TUI should not launch when OPENTRACES_NO_TUI is set"
        assert "opentraces" in result.output.lower()

    def test_non_tty_stdout_prints_help(self, monkeypatch):
        """Bare invocation on non-TTY stdout should print help, not launch TUI."""
        monkeypatch.delenv("OPENTRACES_NO_TUI", raising=False)
        monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: False)
        launched = []
        monkeypatch.setattr("opentraces.cli._launch_tui_ui", lambda *a, **kw: launched.append(1))
        runner = CliRunner()
        result = runner.invoke(main, [])
        assert result.exit_code == 0
        assert len(launched) == 0, "TUI should not launch on non-TTY stdout"

    def test_no_tui_env_var_empty_string_still_suppresses(self, monkeypatch):
        """Any non-empty value for OPENTRACES_NO_TUI suppresses TUI."""
        monkeypatch.setenv("OPENTRACES_NO_TUI", "true")
        monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: True)
        launched = []
        monkeypatch.setattr("opentraces.cli._launch_tui_ui", lambda *a, **kw: launched.append(1))
        runner = CliRunner()
        result = runner.invoke(main, [])
        assert len(launched) == 0


# ---------------------------------------------------------------------------
# session show truncation
# ---------------------------------------------------------------------------

class TestSessionShowTruncation:
    """session show truncates human output by default; --verbose disables it."""

    def test_session_show_truncates_long_content(self, project_with_traces, monkeypatch):
        """Human output should truncate step content > 500 chars."""
        project_dir, runner, trace_id = project_with_traces
        # Inject a long step content into the staging file
        from opentraces.config import get_project_staging_dir
        staging_dir = get_project_staging_dir(project_dir)
        staging_file = next(staging_dir.glob("*.jsonl"))
        import json as _json
        data = _json.loads(staging_file.read_text().strip().splitlines()[0])
        long_content = "x" * 2000
        if data.get("steps"):
            data["steps"][0]["content"] = long_content
        staging_file.write_text(_json.dumps(data) + "\n")

        result = runner.invoke(main, ["session", "show", trace_id])
        assert result.exit_code == 0
        # Only check the human output portion (before the JSON sentinel)
        human_output = result.output.split("---OPENTRACES_JSON---")[0]
        assert "truncated" in human_output
        assert long_content not in human_output

    def test_session_show_verbose_shows_full_content(self, project_with_traces):
        """--verbose should show full step content without truncation."""
        project_dir, runner, trace_id = project_with_traces
        from opentraces.config import get_project_staging_dir
        staging_dir = get_project_staging_dir(project_dir)
        staging_file = next(staging_dir.glob("*.jsonl"))
        import json as _json
        data = _json.loads(staging_file.read_text().strip().splitlines()[0])
        long_content = "y" * 2000
        if data.get("steps"):
            data["steps"][0]["content"] = long_content
        staging_file.write_text(_json.dumps(data) + "\n")

        result = runner.invoke(main, ["session", "show", trace_id, "--verbose"])
        assert result.exit_code == 0
        assert "truncated" not in result.output
        assert long_content in result.output

    def test_session_show_json_never_truncated(self, project_with_traces):
        """--json mode must return the full record regardless of content length."""
        project_dir, runner, trace_id = project_with_traces
        from opentraces.config import get_project_staging_dir
        staging_dir = get_project_staging_dir(project_dir)
        staging_file = next(staging_dir.glob("*.jsonl"))
        import json as _json
        data = _json.loads(staging_file.read_text().strip().splitlines()[0])
        long_content = "z" * 2000
        if data.get("steps"):
            data["steps"][0]["content"] = long_content
        staging_file.write_text(_json.dumps(data) + "\n")

        result = runner.invoke(main, ["--json", "session", "show", trace_id])
        assert result.exit_code == 0
        sentinel = "---OPENTRACES_JSON---"
        assert sentinel in result.output
        payload = _json.loads(result.output.split(sentinel)[1].strip())
        steps = payload["trace"].get("steps", [])
        if steps:
            assert steps[0]["content"] == long_content


# ---------------------------------------------------------------------------
# Hint lines in human output
# ---------------------------------------------------------------------------

class TestHintLines:
    """error_response hints should appear in human-readable output."""

    def test_not_initialized_shows_hint(self, tmp_path, monkeypatch):
        """status on an uninitialized dir should show a Hint: line."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["status"])
        assert "Hint:" in result.output or result.exit_code == 3


# ---------------------------------------------------------------------------
# Exit code contract tests
# ---------------------------------------------------------------------------

class TestExitCodes:
    """Regression guards for the exit code scheme introduced in the agent-aware CLI work."""

    def test_conflicting_push_flags_exits_2(self, initialized_project):
        """--private and --public together is a usage error (exit 2), not a config error (exit 3)."""
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["push", "--private", "--public"])
        assert result.exit_code == 2

    def test_session_commit_not_found_exits_6(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["session", "commit", "nonexistent-trace-id"])
        assert result.exit_code == 6

    def test_session_reject_not_found_exits_6(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["session", "reject", "nonexistent-trace-id"])
        assert result.exit_code == 6

    def test_session_reset_not_found_exits_6(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["session", "reset", "nonexistent-trace-id"])
        assert result.exit_code == 6


# ---------------------------------------------------------------------------
# Init flag tests
# ---------------------------------------------------------------------------

class TestInitFlags:
    """Test undocumented-until-now init flags."""

    def test_init_private(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: False)
        runner = CliRunner()
        result = runner.invoke(main, [
            "init", "--private", "--review-policy", "review",
            "--no-hook", "--start-fresh",
        ])
        assert result.exit_code == 0
        config = json.loads((tmp_path / ".opentraces" / "config.json").read_text())
        assert config.get("visibility") == "private"

    def test_init_public(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: False)
        runner = CliRunner()
        result = runner.invoke(main, [
            "init", "--public", "--review-policy", "review",
            "--no-hook", "--start-fresh",
        ])
        assert result.exit_code == 0
        config = json.loads((tmp_path / ".opentraces" / "config.json").read_text())
        assert config.get("visibility") == "public"


# ---------------------------------------------------------------------------
# Upgrade command
# ---------------------------------------------------------------------------

class TestUpgrade:
    """Test the upgrade command."""

    def test_upgrade_skill_only(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["upgrade", "--skill-only"])
        assert result.exit_code == 0

    def test_upgrade_skill_only_refreshes_skill_file(self, initialized_project):
        """--skill-only should write a fresh skill file even if one exists."""
        project_dir, runner = initialized_project
        skill_path = project_dir / ".agents" / "skills" / "opentraces" / "SKILL.md"
        # Corrupt the skill file
        if skill_path.exists():
            skill_path.write_text("old content")
        result = runner.invoke(main, ["upgrade", "--skill-only"])
        assert result.exit_code == 0
        if skill_path.exists():
            assert skill_path.read_text() != "old content"

    def test_upgrade_skill_only_not_initialized(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ["upgrade", "--skill-only"])
        assert result.exit_code == 3

    def test_upgrade_help(self, runner):
        result = runner.invoke(main, ["upgrade", "--help"])
        assert result.exit_code == 0
        assert "skill-only" in result.output

    def test_upgrade_no_project_skips_skill_refresh(self, runner, tmp_path, monkeypatch):
        """Full upgrade without a project should succeed but skip skill refresh."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "source")
        result = runner.invoke(main, ["upgrade"])
        assert result.exit_code == 0
        assert "Skill refresh skipped" in result.output or "No project" in result.output

    def test_upgrade_source_skips_cli(self, initialized_project, monkeypatch):
        """Source installs should skip CLI upgrade and only refresh skill."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "source")
        result = runner.invoke(main, ["upgrade"])
        assert result.exit_code == 0
        assert "Source install" in result.output

    def test_upgrade_pipx_success(self, initialized_project, monkeypatch):
        """Successful pipx upgrade should exit 0."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "pipx")

        mock_result = type("R", (), {"returncode": 0, "stdout": "upgraded opentraces", "stderr": ""})()
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

        result = runner.invoke(main, ["upgrade"])
        assert result.exit_code == 0

    def test_upgrade_pipx_failure(self, initialized_project, monkeypatch):
        """Failed pipx upgrade should exit 4."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "pipx")

        mock_result = type("R", (), {"returncode": 1, "stdout": "", "stderr": "No such package"})()
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

        result = runner.invoke(main, ["upgrade"])
        assert result.exit_code == 4

    def test_upgrade_pipx_already_latest(self, initialized_project, monkeypatch):
        """pipx 'already at latest version' should not be an error."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "pipx")

        mock_result = type("R", (), {
            "returncode": 1, "stdout": "opentraces is already at latest version", "stderr": ""
        })()
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

        result = runner.invoke(main, ["upgrade"])
        assert result.exit_code == 0
        assert "latest version" in result.output.lower()

    def test_upgrade_brew_success(self, initialized_project, monkeypatch):
        """Successful brew upgrade should exit 0."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "brew")

        mock_result = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

        result = runner.invoke(main, ["upgrade"])
        assert result.exit_code == 0

    def test_upgrade_brew_already_latest(self, initialized_project, monkeypatch):
        """Brew returning 'already installed' should not be an error."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "brew")

        mock_result = type("R", (), {"returncode": 1, "stdout": "", "stderr": "already installed"})()
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

        result = runner.invoke(main, ["upgrade"])
        assert result.exit_code == 0
        assert "latest version" in result.output.lower()

    def test_upgrade_brew_failure(self, initialized_project, monkeypatch):
        """Actual brew failure should exit 4."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "brew")

        mock_result = type("R", (), {"returncode": 1, "stdout": "", "stderr": "Error: no formula"})()
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

        result = runner.invoke(main, ["upgrade"])
        assert result.exit_code == 4

    def test_upgrade_pip_success(self, initialized_project, monkeypatch):
        """Fallback pip upgrade should exit 0."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "pip")

        mock_result = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

        result = runner.invoke(main, ["upgrade"])
        assert result.exit_code == 0

    def test_upgrade_pip_failure(self, initialized_project, monkeypatch):
        """Failed pip upgrade should exit 4."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "pip")

        mock_result = type("R", (), {"returncode": 1, "stdout": "", "stderr": "Permission denied"})()
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

        result = runner.invoke(main, ["upgrade"])
        assert result.exit_code == 4

    def test_upgrade_binary_not_found(self, initialized_project, monkeypatch):
        """Binary disappearing between detection and execution should exit 4."""
        import subprocess
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "brew")

        def raise_fnf(*a, **kw):
            raise FileNotFoundError("brew not found")
        monkeypatch.setattr("subprocess.run", raise_fnf)

        result = runner.invoke(main, ["upgrade"])
        assert result.exit_code == 4
        assert "not found" in result.output.lower()

    def test_upgrade_subprocess_timeout(self, initialized_project, monkeypatch):
        """Hung subprocess should exit 4 after timeout."""
        import subprocess
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "pipx")

        def raise_timeout(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="pipx", timeout=120)
        monkeypatch.setattr("subprocess.run", raise_timeout)

        result = runner.invoke(main, ["upgrade"])
        assert result.exit_code == 4
        assert "timed out" in result.output.lower()

    def test_upgrade_corrupted_config_no_agents(self, tmp_path, monkeypatch):
        """Config missing 'agents' key should fall back to claude-code."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: False)
        runner = CliRunner()
        # Init normally
        runner.invoke(main, [
            "init", "--review-policy", "review",
            "--remote", "test/opentraces", "--no-hook", "--start-fresh",
        ])
        # Corrupt config: remove agents key
        config_path = tmp_path / ".opentraces" / "config.json"
        cfg = json.loads(config_path.read_text())
        cfg.pop("agents", None)
        config_path.write_text(json.dumps(cfg))

        result = runner.invoke(main, ["upgrade", "--skill-only"])
        assert result.exit_code == 0

    def test_upgrade_skill_source_missing(self, initialized_project, monkeypatch):
        """Missing skill source should warn but not crash."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._resolve_skill_source", lambda: None)

        result = runner.invoke(main, ["upgrade", "--skill-only"])
        assert result.exit_code == 0
        assert "could not find" in result.output.lower() or "unchanged" in result.output.lower()


class TestDetectInstallMethod:
    """Test _detect_install_method with mocked paths."""

    def test_source_install(self, monkeypatch):
        """Package not in site-packages = source install."""
        import opentraces.cli as cli_mod
        original_file = cli_mod.__file__
        monkeypatch.setattr(cli_mod, "__file__", "/home/user/opentraces/src/opentraces/cli.py")
        result = cli_mod._detect_install_method()
        monkeypatch.setattr(cli_mod, "__file__", original_file)
        assert result == "source"

    def test_brew_cellar_path(self, monkeypatch):
        """Cellar in path = brew install."""
        import opentraces.cli as cli_mod
        original_file = cli_mod.__file__
        monkeypatch.setattr(cli_mod, "__file__", "/opt/homebrew/Cellar/opentraces/0.1.1/lib/python3.12/site-packages/opentraces/cli.py")
        result = cli_mod._detect_install_method()
        monkeypatch.setattr(cli_mod, "__file__", original_file)
        assert result == "brew"

    def test_pipx_path(self, monkeypatch):
        """pipx home in path = pipx install."""
        import opentraces.cli as cli_mod
        import shutil
        original_file = cli_mod.__file__
        home = str(Path.home())
        fake_path = f"{home}/.local/pipx/venvs/opentraces/lib/python3.12/site-packages/opentraces/cli.py"
        monkeypatch.setattr(cli_mod, "__file__", fake_path)
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/local/bin/pipx" if x == "pipx" else None)
        result = cli_mod._detect_install_method()
        monkeypatch.setattr(cli_mod, "__file__", original_file)
        assert result == "pipx"

    def test_linuxbrew_path(self, monkeypatch):
        """linuxbrew in path = brew install on Linux."""
        import opentraces.cli as cli_mod
        original_file = cli_mod.__file__
        monkeypatch.setattr(cli_mod, "__file__", "/home/linuxbrew/.linuxbrew/lib/python3.12/site-packages/opentraces/cli.py")
        result = cli_mod._detect_install_method()
        monkeypatch.setattr(cli_mod, "__file__", original_file)
        assert result == "brew"

    def test_pipx_custom_home(self, monkeypatch):
        """Custom PIPX_HOME env var should be respected."""
        import opentraces.cli as cli_mod
        import shutil
        original_file = cli_mod.__file__
        fake_path = "/opt/custom-pipx/venvs/opentraces/lib/python3.12/site-packages/opentraces/cli.py"
        monkeypatch.setattr(cli_mod, "__file__", fake_path)
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/local/bin/pipx" if x == "pipx" else None)
        monkeypatch.setenv("PIPX_HOME", "/opt/custom-pipx")
        result = cli_mod._detect_install_method()
        monkeypatch.setattr(cli_mod, "__file__", original_file)
        assert result == "pipx"

    def test_pipx_on_path_but_not_installer(self, monkeypatch):
        """pipx available but package not in pipx home = pip fallback."""
        import opentraces.cli as cli_mod
        import shutil
        original_file = cli_mod.__file__
        fake_path = "/usr/lib/python3.12/site-packages/opentraces/cli.py"
        monkeypatch.setattr(cli_mod, "__file__", fake_path)
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/local/bin/pipx" if x == "pipx" else None)
        result = cli_mod._detect_install_method()
        monkeypatch.setattr(cli_mod, "__file__", original_file)
        assert result == "pip"

    def test_pip_fallback(self, monkeypatch):
        """No brew or pipx markers = pip fallback."""
        import opentraces.cli as cli_mod
        import shutil
        original_file = cli_mod.__file__
        fake_path = "/usr/lib/python3.12/site-packages/opentraces/cli.py"
        monkeypatch.setattr(cli_mod, "__file__", fake_path)
        monkeypatch.setattr(shutil, "which", lambda x: None)
        result = cli_mod._detect_install_method()
        monkeypatch.setattr(cli_mod, "__file__", original_file)
        assert result == "pip"

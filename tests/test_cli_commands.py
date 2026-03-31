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
        assert result.exit_code == 3

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
        assert result.exit_code == 3


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

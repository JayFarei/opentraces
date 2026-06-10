"""Smoke tests for every documented public CLI command.

Validates that each command exists, accepts documented flags, and returns
expected exit codes. These are regression guards, not behavior tests.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

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
    result = runner.invoke(main, ["init", "--start-fresh"])
    assert result.exit_code == 0, f"init failed: {result.output}"
    return tmp_path, runner


@pytest.fixture
def project_with_traces(initialized_project):
    """Tier 3: initialized project with a staged trace."""
    project_dir, runner = initialized_project

    from opentraces.core.state import StateManager, TraceStatus
    from opentraces.core.config import get_project_state_path, get_project_traces_dir
    from opentraces_schema import TraceRecord

    staging_dir = get_project_traces_dir(project_dir)
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
        result = runner.invoke(main, ["auth", "login", "--help"])
        assert result.exit_code == 0
        assert "token" in result.output.lower()

    def test_logout(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ["auth", "logout"])
        assert result.exit_code == 0

    def test_auth_whoami_unauthenticated(self, runner, monkeypatch):
        monkeypatch.setattr("opentraces.cli._auth_identity", lambda *a: None)
        monkeypatch.setattr("opentraces.cli.load_config", lambda: type("C", (), {"hf_token": None})())
        result = runner.invoke(main, ["auth", "whoami"])
        assert result.exit_code == 3 or "Not authenticated" in result.output


# ---------------------------------------------------------------------------
# Current public command tree
# ---------------------------------------------------------------------------

class TestPublicCommandTree:
    """Smoke-test the unreleased simplified command surface."""

    def test_status_not_initialized(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ["status"])
        assert result.exit_code == 3

    def test_status_json_after_init(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["--json", "status"])
        assert result.exit_code == 0
        assert "---OPENTRACES_JSON---" in result.output

    def test_config_show(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["config", "show"])
        assert result.exit_code == 0

    def test_config_set_classifier_sensitivity(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["config", "set", "classifier_sensitivity", "high"])
        assert result.exit_code == 0

    @pytest.mark.parametrize(
        "command",
        [
            "list",
            "show",
            "add",
            "reject",
            "push",
            "pull",
            "web",
            "tui",
            "remote",
            "reset",
            "redact",
            "discard",
            "llm-review",
            "export",
            "stats",
            "log",
            "assess",
            "blame",
            "graph",
            "resume",
        ],
    )
    def test_legacy_flat_roots_are_not_registered(self, runner, command):
        result = runner.invoke(main, [command, "--help"])
        assert result.exit_code == 2
        assert "No such command" in result.output

    @pytest.mark.parametrize("command", ["trace", "trail", "workflow", "dataset"])
    def test_canonical_groups_have_help(self, runner, command):
        result = runner.invoke(main, [command, "--help"])
        assert result.exit_code == 0
        assert "Commands:" in result.output


# ---------------------------------------------------------------------------
# Bare invocation: legacy TUI autolaunch is decommissioned.
# ---------------------------------------------------------------------------

class TestMachineMode:
    """Bare invocation always prints help and never launches the legacy TUI."""

    def test_no_tui_env_var_prints_help(self, monkeypatch):
        """Legacy env var remains harmless while bare invocation prints help."""
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
        """Any non-empty legacy env var value still leaves help-mode behavior."""
        monkeypatch.setenv("OPENTRACES_NO_TUI", "true")
        monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: True)
        launched = []
        monkeypatch.setattr("opentraces.cli._launch_tui_ui", lambda *a, **kw: launched.append(1))
        runner = CliRunner()
        result = runner.invoke(main, [])
        assert result.exit_code == 0
        assert len(launched) == 0

    def test_interactive_bare_invocation_prints_help(self, monkeypatch):
        """Interactive bare invocation no longer launches the legacy TUI."""
        monkeypatch.delenv("OPENTRACES_NO_TUI", raising=False)
        monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: True)
        launched = []
        monkeypatch.setattr("opentraces.cli._launch_tui_ui", lambda *a, **kw: launched.append(1))
        result = CliRunner().invoke(main, [])
        assert result.exit_code == 0
        assert len(launched) == 0
        assert "opentraces" in result.output.lower()


# ---------------------------------------------------------------------------
# session show truncation
# ---------------------------------------------------------------------------

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

class TestInitFlags:
    """Removed init flags stay removed because this surface is unreleased."""

    @pytest.mark.parametrize(
        "flag",
        ["--private", "--public", "--review-policy", "--push-policy", "--remote", "--no-hook"],
    )
    def test_legacy_init_flags_error(self, tmp_path, monkeypatch, flag):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: False)
        runner = CliRunner()
        args = ["init", flag]
        if flag in {"--review-policy", "--push-policy", "--remote"}:
            args.append("value")
        result = runner.invoke(main, args)
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Upgrade command
# ---------------------------------------------------------------------------

class TestUpgrade:
    """Test the upgrade command."""

    def test_upgrade_skill_only(self, initialized_project):
        project_dir, runner = initialized_project
        result = runner.invoke(main, ["setup", "upgrade", "--skill-only"])
        assert result.exit_code == 0

    def test_upgrade_skill_only_refreshes_skill_file(self, initialized_project):
        """--skill-only should write a fresh skill file even if one exists."""
        project_dir, runner = initialized_project
        skill_path = project_dir / ".agents" / "skills" / "opentraces" / "SKILL.md"
        # Corrupt the skill file
        if skill_path.exists():
            skill_path.write_text("old content")
        result = runner.invoke(main, ["setup", "upgrade", "--skill-only"])
        assert result.exit_code == 0
        if skill_path.exists():
            assert skill_path.read_text() != "old content"

    def test_upgrade_skill_only_not_initialized(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ["setup", "upgrade", "--skill-only"])
        assert result.exit_code == 3

    def test_upgrade_help(self, runner):
        result = runner.invoke(main, ["setup", "upgrade", "--help"])
        assert result.exit_code == 0
        assert "skill-only" in result.output

    def test_upgrade_no_project_skips_skill_refresh(self, runner, tmp_path, monkeypatch):
        """Full upgrade without a project should succeed but skip skill refresh."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "source")
        result = runner.invoke(main, ["setup", "upgrade"])
        assert result.exit_code == 0
        assert "Skill refresh skipped" in result.output or "No project" in result.output

    def test_upgrade_source_skips_cli(self, initialized_project, monkeypatch):
        """Source installs should skip CLI upgrade and only refresh skill."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "source")
        result = runner.invoke(main, ["setup", "upgrade"])
        assert result.exit_code == 0
        assert "Source install" in result.output

    def test_upgrade_pipx_success(self, initialized_project, monkeypatch):
        """Successful pipx upgrade should exit 0."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "pipx")

        mock_result = type("R", (), {"returncode": 0, "stdout": "upgraded opentraces", "stderr": ""})()
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

        result = runner.invoke(main, ["setup", "upgrade"])
        assert result.exit_code == 0

    def test_upgrade_pipx_failure(self, initialized_project, monkeypatch):
        """Failed pipx upgrade should exit 4."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "pipx")

        mock_result = type("R", (), {"returncode": 1, "stdout": "", "stderr": "No such package"})()
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

        result = runner.invoke(main, ["setup", "upgrade"])
        assert result.exit_code == 4

    def test_upgrade_pipx_already_latest(self, initialized_project, monkeypatch):
        """pipx 'already at latest version' should not be an error."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "pipx")

        mock_result = type("R", (), {
            "returncode": 1, "stdout": "opentraces is already at latest version", "stderr": ""
        })()
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

        result = runner.invoke(main, ["setup", "upgrade"])
        assert result.exit_code == 0
        assert "latest version" in result.output.lower()

    def test_upgrade_brew_success(self, initialized_project, monkeypatch):
        """Successful brew upgrade should exit 0."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "brew")

        mock_result = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

        result = runner.invoke(main, ["setup", "upgrade"])
        assert result.exit_code == 0

    def test_upgrade_brew_already_latest(self, initialized_project, monkeypatch):
        """Brew returning 'already installed' should not be an error."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "brew")

        mock_result = type("R", (), {"returncode": 1, "stdout": "", "stderr": "already installed"})()
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

        result = runner.invoke(main, ["setup", "upgrade"])
        assert result.exit_code == 0
        assert "latest version" in result.output.lower()

    def test_upgrade_brew_failure(self, initialized_project, monkeypatch):
        """Actual brew failure should exit 4."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "brew")

        mock_result = type("R", (), {"returncode": 1, "stdout": "", "stderr": "Error: no formula"})()
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

        result = runner.invoke(main, ["setup", "upgrade"])
        assert result.exit_code == 4

    def test_upgrade_pip_success(self, initialized_project, monkeypatch):
        """Fallback pip upgrade should exit 0."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "pip")

        mock_result = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

        result = runner.invoke(main, ["setup", "upgrade"])
        assert result.exit_code == 0

    def test_upgrade_pip_failure(self, initialized_project, monkeypatch):
        """Failed pip upgrade should exit 4."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "pip")

        mock_result = type("R", (), {"returncode": 1, "stdout": "", "stderr": "Permission denied"})()
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

        result = runner.invoke(main, ["setup", "upgrade"])
        assert result.exit_code == 4

    def test_upgrade_binary_not_found(self, initialized_project, monkeypatch):
        """Binary disappearing between detection and execution should exit 4."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._detect_install_method", lambda: "brew")

        def raise_fnf(*a, **kw):
            raise FileNotFoundError("brew not found")
        monkeypatch.setattr("subprocess.run", raise_fnf)

        result = runner.invoke(main, ["setup", "upgrade"])
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

        result = runner.invoke(main, ["setup", "upgrade"])
        assert result.exit_code == 4
        assert "timed out" in result.output.lower()

    def test_upgrade_corrupted_config_no_agents(self, tmp_path, monkeypatch):
        """Config missing 'agents' key should fall back to claude-code."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: False)
        runner = CliRunner()
        # Init normally
        runner.invoke(main, ["init", "--start-fresh"])
        # Corrupt config: remove agents key
        config_path = tmp_path / ".opentraces.json"
        cfg = json.loads(config_path.read_text())
        cfg.pop("agents", None)
        config_path.write_text(json.dumps(cfg))

        result = runner.invoke(main, ["setup", "upgrade", "--skill-only"])
        assert result.exit_code == 0

    def test_upgrade_skill_source_missing(self, initialized_project, monkeypatch):
        """Missing skill source should warn but not crash."""
        project_dir, runner = initialized_project
        monkeypatch.setattr("opentraces.cli._resolve_skill_source", lambda: None)

        result = runner.invoke(main, ["setup", "upgrade", "--skill-only"])
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
        # CI runners preinstall pipx with PIPX_HOME=/opt/pipx; the detector
        # compares against that root, so the fake ~/.local/pipx path never
        # matches unless the env var is cleared.
        monkeypatch.delenv("PIPX_HOME", raising=False)
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


# ---------------------------------------------------------------------------
# hooks command group
# ---------------------------------------------------------------------------

class TestHooksCommands:
    """Tests for opentraces setup claude-code."""

    def test_setup_help(self, runner):
        result = runner.invoke(main, ["setup", "--help"])
        assert result.exit_code == 0
        assert "claude-code" in result.output
        assert "git" in result.output

    def test_hooks_install_help(self, runner):
        result = runner.invoke(main, ["setup", "claude-code", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output
        assert "--hooks-dir" in result.output
        assert "--settings-file" in result.output

    def test_hooks_install_dry_run_writes_nothing(self, tmp_path, runner):
        hooks_dir = tmp_path / "hooks"
        settings_file = tmp_path / "settings.json"
        result = runner.invoke(main, [
            "setup", "claude-code", "--dry-run",
            "--hooks-dir", str(hooks_dir),
            "--settings-file", str(settings_file),
        ])
        assert result.exit_code == 0
        assert "dry-run" in result.output
        assert not hooks_dir.exists()
        assert not settings_file.exists()

    def test_hooks_install_copies_scripts(self, tmp_path, runner):
        hooks_dir = tmp_path / "hooks"
        settings_file = tmp_path / "settings.json"
        result = runner.invoke(main, [
            "setup", "claude-code",
            "--hooks-dir", str(hooks_dir),
            "--settings-file", str(settings_file),
        ])
        assert result.exit_code == 0
        assert (hooks_dir / "opentraces_on_pre_tool_use.py").exists()
        assert (hooks_dir / "opentraces_on_tool_use.py").exists()
        assert (hooks_dir / "opentraces_on_stop.py").exists()
        assert (hooks_dir / "opentraces_on_compact.py").exists()

    def test_hooks_install_creates_settings_with_hooks(self, tmp_path, runner):
        hooks_dir = tmp_path / "hooks"
        settings_file = tmp_path / "settings.json"
        result = runner.invoke(main, [
            "setup", "claude-code",
            "--hooks-dir", str(hooks_dir),
            "--settings-file", str(settings_file),
        ])
        assert result.exit_code == 0
        settings = json.loads(settings_file.read_text())
        assert "PreToolUse" in settings["hooks"]
        assert "PostToolUse" in settings["hooks"]
        assert "Stop" in settings["hooks"]
        assert "PostCompact" in settings["hooks"]
        # Each event should have exactly one hook entry
        assert len(settings["hooks"]["PreToolUse"]) == 1
        assert len(settings["hooks"]["PostToolUse"]) == 1
        assert len(settings["hooks"]["Stop"]) == 1
        assert len(settings["hooks"]["PostCompact"]) == 1

    def test_hooks_install_idempotent_no_duplicates(self, tmp_path, runner):
        hooks_dir = tmp_path / "hooks"
        settings_file = tmp_path / "settings.json"
        args = [
            "setup", "claude-code",
            "--hooks-dir", str(hooks_dir),
            "--settings-file", str(settings_file),
        ]
        runner.invoke(main, args)
        runner.invoke(main, args)  # second run

        settings = json.loads(settings_file.read_text())
        assert len(settings["hooks"]["PreToolUse"]) == 1
        assert len(settings["hooks"]["PostToolUse"]) == 1
        assert len(settings["hooks"]["Stop"]) == 1
        assert len(settings["hooks"]["PostCompact"]) == 1

    def test_hooks_install_uses_current_python_interpreter(self, tmp_path, runner):
        hooks_dir = tmp_path / "hooks"
        settings_file = tmp_path / "settings.json"
        result = runner.invoke(main, [
            "setup", "claude-code",
            "--hooks-dir", str(hooks_dir),
            "--settings-file", str(settings_file),
        ])
        assert result.exit_code == 0
        settings = json.loads(settings_file.read_text())
        expected_prefix = f"{shlex.quote(sys.executable)} "
        for entries in settings["hooks"].values():
            command = entries[0]["hooks"][0]["command"]
            assert command.startswith(expected_prefix)

    def test_hooks_install_replaces_stale_python3_hook_commands(self, tmp_path, runner):
        hooks_dir = tmp_path / "hooks"
        settings_file = tmp_path / "settings.json"
        stale = f"python3 {hooks_dir}/opentraces_on_tool_use.py"
        settings_file.write_text(json.dumps({
            "hooks": {
                "PostToolUse": [
                    {"hooks": [{"type": "command", "command": stale}]},
                    {"hooks": [{"type": "command", "command": "echo keep-me"}]},
                ],
            },
        }))
        result = runner.invoke(main, [
            "setup", "claude-code",
            "--hooks-dir", str(hooks_dir),
            "--settings-file", str(settings_file),
        ])
        assert result.exit_code == 0
        settings = json.loads(settings_file.read_text())
        commands = [
            hook["command"]
            for entry in settings["hooks"]["PostToolUse"]
            for hook in entry["hooks"]
        ]
        assert stale not in commands
        assert "echo keep-me" in commands
        assert any(command.startswith(f"{shlex.quote(sys.executable)} ") for command in commands)

    def test_hooks_install_merges_with_existing_hooks(self, tmp_path, runner):
        """Existing hooks in settings.json are preserved."""
        hooks_dir = tmp_path / "hooks"
        settings_file = tmp_path / "settings.json"
        # Pre-populate settings with an unrelated hook
        settings_file.write_text(json.dumps({
            "hooks": {
                "Stop": [{"type": "command", "command": "echo existing"}],
            }
        }))
        result = runner.invoke(main, [
            "setup", "claude-code",
            "--hooks-dir", str(hooks_dir),
            "--settings-file", str(settings_file),
        ])
        assert result.exit_code == 0
        settings = json.loads(settings_file.read_text())

        def _commands(entries):
            out = []
            for entry in entries:
                inner = entry.get("hooks")
                if isinstance(inner, list):
                    out.extend(h.get("command", "") for h in inner)
                elif "command" in entry:
                    out.append(entry["command"])
            return out

        commands = _commands(settings["hooks"]["Stop"])
        # Existing hook preserved (legacy bare shape is tolerated)
        assert "echo existing" in commands
        # Our hook added in the matcher-envelope shape
        assert any("opentraces_on_stop" in c for c in commands)

    def test_hooks_install_scripts_are_executable(self, tmp_path, runner):
        import stat as stat_mod
        hooks_dir = tmp_path / "hooks"
        settings_file = tmp_path / "settings.json"
        runner.invoke(main, [
            "setup", "claude-code",
            "--hooks-dir", str(hooks_dir),
            "--settings-file", str(settings_file),
        ])
        stop_script = hooks_dir / "opentraces_on_stop.py"
        mode = stop_script.stat().st_mode
        assert mode & stat_mod.S_IXUSR, "Script should be user-executable"

    def test_hooks_install_emits_json(self, tmp_path, runner):
        hooks_dir = tmp_path / "hooks"
        settings_file = tmp_path / "settings.json"
        result = runner.invoke(main, [
            "--json", "setup", "claude-code",
            "--hooks-dir", str(hooks_dir),
            "--settings-file", str(settings_file),
        ])
        assert result.exit_code == 0
        # Extract JSON after sentinel
        from opentraces.cli import SENTINEL
        if SENTINEL in result.output:
            json_part = result.output.split(SENTINEL, 1)[1].strip()
            data = json.loads(json_part)
            assert data["status"] == "ok"
            assert "installed" in data

    def test_hooks_install_refuses_corrupt_settings(self, tmp_path, runner):
        """Malformed settings.json must abort, not silently overwrite."""
        hooks_dir = tmp_path / "hooks"
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("not valid json {{{")
        result = runner.invoke(main, [
            "setup", "claude-code",
            "--hooks-dir", str(hooks_dir),
            "--settings-file", str(settings_file),
        ])
        assert result.exit_code == 5
        # Original file must be untouched
        assert settings_file.read_text() == "not valid json {{{"

    def test_hooks_install_refuses_non_object_settings(self, tmp_path, runner):
        """settings.json root must be a JSON object, not an array or scalar."""
        hooks_dir = tmp_path / "hooks"
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("[1, 2, 3]")
        result = runner.invoke(main, [
            "setup", "claude-code",
            "--hooks-dir", str(hooks_dir),
            "--settings-file", str(settings_file),
        ])
        assert result.exit_code == 5
        assert settings_file.read_text() == "[1, 2, 3]"

    def test_hooks_install_path_quoting_in_command(self, tmp_path, runner):
        """Hook command registered in settings.json must shell-quote the path."""
        import shlex
        hooks_dir = tmp_path / "hooks with spaces"
        settings_file = tmp_path / "settings.json"
        runner.invoke(main, [
            "setup", "claude-code",
            "--hooks-dir", str(hooks_dir),
            "--settings-file", str(settings_file),
        ])
        settings = json.loads(settings_file.read_text())
        found = False
        for entry in settings["hooks"]["Stop"]:
            inner = entry.get("hooks")
            commands = (
                [h["command"] for h in inner if "command" in h]
                if isinstance(inner, list)
                else ([entry["command"]] if "command" in entry else [])
            )
            for command in commands:
                # shlex.split must parse it back to exactly 2 tokens (python3 + path)
                tokens = shlex.split(command)
                assert len(tokens) == 2, f"Expected 2 tokens, got: {tokens}"
                found = True
        assert found, "no hook commands found under Stop"

    def test_hooks_install_dry_run_emits_json(self, tmp_path, runner):
        """--dry-run should still emit machine-readable JSON."""
        hooks_dir = tmp_path / "hooks"
        settings_file = tmp_path / "settings.json"
        result = runner.invoke(main, [
            "--json", "setup", "claude-code", "--dry-run",
            "--hooks-dir", str(hooks_dir),
            "--settings-file", str(settings_file),
        ])
        assert result.exit_code == 0
        from opentraces.cli import SENTINEL
        assert SENTINEL in result.output
        json_part = result.output.split(SENTINEL, 1)[1].strip()
        data = json.loads(json_part)
        assert data["status"] == "ok"
        assert data["dry_run"] is True
        assert "plan" in data

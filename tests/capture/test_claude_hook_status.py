"""Claude Code hook status and canonical setup remediation (#306).

``status()`` only recognizes a directly executable
``<absolute python> <expected script>`` pair.  A stale or non-canonical
registration is honestly unregistered; ``opentraces setup claude-code`` is
the supported repair path.
"""
from __future__ import annotations

import json
import shlex
import stat
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from opentraces.capture.claude_code import install as cc_install
from opentraces.cli import main


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _status_for_stop_command(tmp_path: Path, command: str) -> dict:
    hooks_dir = tmp_path / "hooks"
    expected_script = hooks_dir / "opentraces_on_stop.py"
    _make_executable(expected_script)
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {"hooks": [{"type": "command", "command": command}]}
                    ]
                }
            }
        )
    )
    return cc_install.status(hooks_dir=hooks_dir, settings_file=settings_file)


def test_status_accepts_canonical_current_interpreter(tmp_path: Path) -> None:
    expected_script = tmp_path / "hooks" / "opentraces_on_stop.py"
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(expected_script))}"

    observed = _status_for_stop_command(tmp_path, command)

    assert observed["registered"]["Stop"] is True


def test_status_accepts_homebrew_opt_stable_interpreter(tmp_path: Path) -> None:
    cellar_python = _make_executable(
        tmp_path
        / "brew"
        / "Cellar"
        / "opentraces"
        / "0.4.10"
        / "libexec"
        / "bin"
        / "python"
    )
    opt_formula = tmp_path / "brew" / "opt" / "opentraces"
    opt_formula.parent.mkdir(parents=True)
    opt_formula.symlink_to(cellar_python.parents[2])
    opt_python = opt_formula / "libexec" / "bin" / "python"
    expected_script = tmp_path / "hooks" / "opentraces_on_stop.py"
    command = f"{shlex.quote(str(opt_python))} {shlex.quote(str(expected_script))}"

    observed = _status_for_stop_command(tmp_path, command)

    assert observed["registered"]["Stop"] is True


def test_status_accepts_pipx_interpreter(tmp_path: Path) -> None:
    pipx_python = _make_executable(
        tmp_path / "pipx" / "venvs" / "opentraces" / "bin" / "python"
    )
    expected_script = tmp_path / "hooks" / "opentraces_on_stop.py"
    command = f"{shlex.quote(str(pipx_python))} {shlex.quote(str(expected_script))}"

    observed = _status_for_stop_command(tmp_path, command)

    assert observed["registered"]["Stop"] is True


def test_status_rejects_deleted_interpreter(tmp_path: Path) -> None:
    """The historical substring check lied after an interpreter was deleted."""

    deleted_python = _make_executable(tmp_path / "deleted" / "bin" / "python")
    expected_script = tmp_path / "hooks" / "opentraces_on_stop.py"
    command = (
        f"{shlex.quote(str(deleted_python))} "
        f"{shlex.quote(str(expected_script))}"
    )
    deleted_python.unlink()

    observed = _status_for_stop_command(tmp_path, command)

    assert observed["registered"]["Stop"] is False


@pytest.mark.parametrize(
    "command_factory",
    [
        pytest.param(
            lambda script: (
                '$(command -v opentraces || echo "$HOME/.local/bin/opentraces") '
                "_capture --hook Stop"
            ),
            id="v0.3.3-wrapper",
        ),
        pytest.param(
            lambda script: f"{shlex.quote(sys.executable)} -u {shlex.quote(str(script))}",
            id="three-token-command",
        ),
        pytest.param(
            lambda script: f"python {shlex.quote(str(script))}",
            id="relative-interpreter",
        ),
    ],
)
def test_status_rejects_noncanonical_command_shapes(
    tmp_path: Path,
    command_factory: Callable[[Path], str],
) -> None:
    expected_script = tmp_path / "hooks" / "opentraces_on_stop.py"

    observed = _status_for_stop_command(tmp_path, command_factory(expected_script))

    assert observed["registered"]["Stop"] is False


def test_setup_claude_code_repairs_stale_registration_without_collateral_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hooks_dir = tmp_path / "hooks"
    settings_file = tmp_path / "settings.json"
    deleted_python = _make_executable(tmp_path / "deleted" / "bin" / "python")
    canonical_python = _make_executable(tmp_path / "pipx" / "bin" / "python")
    stale_hooks: dict[str, list[dict]] = {}
    for event, script_name in cc_install.EVENT_SCRIPTS.items():
        script = hooks_dir / f"opentraces_{script_name}"
        stale_hooks[event] = [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{deleted_python} {script}",
                    }
                ]
            }
        ]
    stale_hooks["Stop"].append(
        {
            "matcher": "user-hook",
            "hooks": [{"type": "command", "command": "echo keep-me"}],
        }
    )
    original_unrelated = {
        "permissions": {"allow": ["Bash(git status:*)"]},
        "env": {"CLAUDE_CODE_ENABLE_TELEMETRY": "1"},
    }
    settings_file.write_text(json.dumps({**original_unrelated, "hooks": stale_hooks}))
    deleted_python.unlink()

    assert cc_install.status(hooks_dir, settings_file)["installed"] is False
    monkeypatch.setattr(cc_install, "stable_interpreter", lambda: str(canonical_python))
    args = [
        "setup",
        "claude-code",
        "--hooks-dir",
        str(hooks_dir),
        "--settings-file",
        str(settings_file),
    ]
    runner = CliRunner()

    first = runner.invoke(main, args)
    second = runner.invoke(main, args)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    observed = cc_install.status(hooks_dir, settings_file)
    assert observed["installed"] is True
    repaired = json.loads(settings_file.read_text())
    assert repaired["permissions"] == original_unrelated["permissions"]
    assert repaired["env"] == original_unrelated["env"]
    for event, script_name in cc_install.EVENT_SCRIPTS.items():
        commands = [
            hook["command"]
            for entry in repaired["hooks"][event]
            for hook in entry.get("hooks", [])
        ]
        expected = f"{canonical_python} {hooks_dir / f'opentraces_{script_name}'}"
        assert commands.count(expected) == 1
        assert all(str(deleted_python) not in command for command in commands)
    assert "echo keep-me" in [
        hook["command"]
        for entry in repaired["hooks"]["Stop"]
        for hook in entry.get("hooks", [])
    ]

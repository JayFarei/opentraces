"""Codex CLI hook installer tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from opentraces.capture._base import HookInstallError
from opentraces.capture.codex_cli import install as codex_install


def _commands(config: dict, event: str) -> list[str]:
    commands: list[str] = []
    for entry in config["hooks"][event]:
        for hook in entry.get("hooks", []):
            commands.append(hook.get("command", ""))
    return commands


def test_codex_installer_writes_hooks_json_and_scripts(tmp_path: Path) -> None:
    hooks_dir = tmp_path / ".codex" / "hooks" / "opentraces"
    hooks_file = tmp_path / ".codex" / "hooks.json"

    result = codex_install.install(hooks_dir=hooks_dir, hooks_file=hooks_file)

    assert set(result.added) == set(codex_install.EVENT_SCRIPTS)
    config = json.loads(hooks_file.read_text())
    assert set(config["hooks"]) == set(codex_install.EVENT_SCRIPTS)
    for event, filename in codex_install.EVENT_SCRIPTS.items():
        script = hooks_dir / filename
        assert script.exists()
        assert script.stat().st_mode & 0o111
        command = _commands(config, event)[0]
        assert " -m " in command
        assert f"opentraces.capture.codex_cli.hooks.{script.stem}" in command
        assert "matcher" not in config["hooks"][event][0]


def test_codex_installer_is_idempotent_and_preserves_other_hooks(tmp_path: Path) -> None:
    hooks_dir = tmp_path / ".codex" / "hooks" / "opentraces"
    hooks_file = tmp_path / ".codex" / "hooks.json"
    hooks_file.parent.mkdir(parents=True)
    hooks_file.write_text(json.dumps({
        "hooks": {
            "Stop": [
                {
                    "matcher": "*",
                    "hooks": [{"type": "command", "command": "echo keep"}],
                }
            ]
        }
    }))

    codex_install.install(hooks_dir=hooks_dir, hooks_file=hooks_file)
    codex_install.install(hooks_dir=hooks_dir, hooks_file=hooks_file)
    config = json.loads(hooks_file.read_text())

    assert _commands(config, "Stop").count("echo keep") == 1
    own = [
        cmd
        for cmd in _commands(config, "Stop")
        if "opentraces.capture.codex_cli.hooks." in cmd
    ]
    assert len(own) == 1


def test_codex_cli_setup_command_dry_run(tmp_path: Path) -> None:
    from click.testing import CliRunner

    from opentraces.cli import main

    hooks_dir = tmp_path / ".codex" / "hooks" / "opentraces"
    hooks_file = tmp_path / ".codex" / "hooks.json"

    result = CliRunner().invoke(
        main,
        [
            "setup",
            "codex-cli",
            "--dry-run",
            "--hooks-dir",
            str(hooks_dir),
            "--hooks-file",
            str(hooks_file),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output
    assert not hooks_dir.exists()
    assert not hooks_file.exists()


def test_init_codex_agent_installs_codex_hooks(tmp_path: Path, monkeypatch) -> None:
    from click.testing import CliRunner

    from opentraces.cli import main

    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: False)
    monkeypatch.setattr("opentraces.cli._install_skill", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        "opentraces.cli._current_project_session_dir",
        lambda _project_dir, cfg=None: None,
    )

    prev_cwd = Path.cwd()
    try:
        os.chdir(project)
        result = CliRunner().invoke(main, ["init", "--agent", "codex", "--start-fresh"])
    finally:
        os.chdir(prev_cwd)

    assert result.exit_code == 0, result.output
    hooks_file = home / ".codex" / "hooks.json"
    config = json.loads(hooks_file.read_text())
    assert "Stop" in config["hooks"]
    assert any(
        "opentraces.capture.codex_cli.hooks.on_stop" in command
        for command in _commands(config, "Stop")
    )
    assert "~/.codex/hooks.json" in result.output


def test_codex_installer_status_and_remove(tmp_path: Path) -> None:
    hooks_dir = tmp_path / ".codex" / "hooks" / "opentraces"
    hooks_file = tmp_path / ".codex" / "hooks.json"

    assert codex_install.status(hooks_dir=hooks_dir, hooks_file=hooks_file)["installed"] is False
    codex_install.install(hooks_dir=hooks_dir, hooks_file=hooks_file)
    assert codex_install.status(hooks_dir=hooks_dir, hooks_file=hooks_file)["installed"] is True

    result = codex_install.remove(hooks_dir=hooks_dir, hooks_file=hooks_file)
    assert set(result.removed) == set(codex_install.EVENT_SCRIPTS)
    assert codex_install.status(hooks_dir=hooks_dir, hooks_file=hooks_file)["installed"] is False


def test_codex_status_is_interpreter_agnostic(tmp_path: Path) -> None:
    """A hook written by a different interpreter still reads as installed.

    Regression: status() previously required a byte-exact command match
    including the full interpreter path, so a hook written by an editable
    ``.venv`` opentraces reported ``installed=False`` when checked by a pipx
    opentraces (different ``sys.executable``).
    """
    hooks_dir = tmp_path / ".codex" / "hooks" / "opentraces"
    hooks_file = tmp_path / ".codex" / "hooks.json"

    codex_install.install(hooks_dir=hooks_dir, hooks_file=hooks_file)

    # Rewrite every registered command to use a foreign interpreter path,
    # keeping the opentraces module form intact (what a different venv writes).
    config = json.loads(hooks_file.read_text())
    foreign = "/some/other/venv/bin/python"
    for event in codex_install.EVENT_SCRIPTS:
        for entry in config["hooks"][event]:
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                if "opentraces.capture.codex_cli.hooks." in cmd:
                    hook["command"] = foreign + cmd[cmd.index(" -m ") :]
    hooks_file.write_text(json.dumps(config))

    status = codex_install.status(hooks_dir=hooks_dir, hooks_file=hooks_file)
    assert status["installed"] is True
    assert all(status["registered"].values())


def test_codex_installer_refuses_corrupt_hooks_json(tmp_path: Path) -> None:
    hooks_file = tmp_path / ".codex" / "hooks.json"
    hooks_file.parent.mkdir(parents=True)
    hooks_file.write_text("{")

    with pytest.raises(HookInstallError, match="Cannot parse"):
        codex_install.install(hooks_file=hooks_file)


def test_codex_hook_installer_registered() -> None:
    from opentraces import capture
    from opentraces.capture.codex_cli.install import CodexCliHookInstaller

    assert capture.get_hook_installers()["codex-cli"] is CodexCliHookInstaller

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from opentraces.capture._base import HookInstallError
from opentraces.capture.pi import install as pi_install
from opentraces.cli import SENTINEL, main


def _json_payload(output: str) -> dict:
    if SENTINEL in output:
        output = output.split(SENTINEL, 1)[1]
    return json.loads(output)


def test_pi_installer_writes_project_package_entry(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()

    result = pi_install.install(project=True, cwd=project, local=False)

    settings = json.loads((project / ".pi" / "settings.json").read_text())
    assert "npm:opentraces-pi" in settings["packages"]
    assert result.added == ["package"]


def test_pi_installer_is_idempotent_and_preserves_other_packages(tmp_path: Path) -> None:
    settings = tmp_path / ".pi" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"packages": ["npm:other"]}))

    pi_install.install(settings_file=settings)
    pi_install.install(settings_file=settings)

    packages = json.loads(settings.read_text())["packages"]
    assert packages.count("npm:other") == 1
    assert len([pkg for pkg in packages if pkg == "npm:opentraces-pi"]) == 1


def test_pi_setup_command_dry_run_does_not_write(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.chdir(project)

    result = CliRunner().invoke(main, ["setup", "pi", "--dry-run", "--json"])

    assert result.exit_code == 0, result.output
    payload = _json_payload(result.output)
    assert payload["status"] == "ok"
    assert payload["dry_run"] is True
    assert payload["raw_provider_bodies_default"] == "off"
    assert not (project / ".pi" / "settings.json").exists()


def test_pi_setup_dry_run_treats_project_package_as_installed(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    pi_install.install(project=True, cwd=project)
    monkeypatch.chdir(project)

    result = CliRunner().invoke(main, ["setup", "pi", "--dry-run", "--json"])

    assert result.exit_code == 0, result.output
    payload = _json_payload(result.output)
    assert payload["project_package"]["installed"] is True
    step = next(step for step in payload["steps"] if step["name"] == "pi_package")
    assert step["state"] == "ok"


def test_pi_setup_command_remove(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    pi_install.install(project=True, cwd=project)
    monkeypatch.chdir(project)

    result = CliRunner().invoke(main, ["setup", "pi", "--project", "--remove", "--json"])

    assert result.exit_code == 0, result.output
    payload = _json_payload(result.output)
    assert payload["status"] == "ok"
    assert payload["removed"] == ["package"]
    assert json.loads((project / ".pi" / "settings.json").read_text())["packages"] == []


def test_pi_installer_refuses_corrupt_settings(tmp_path: Path) -> None:
    settings = tmp_path / ".pi" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{")

    with pytest.raises(HookInstallError, match="Cannot parse"):
        pi_install.install(settings_file=settings)


def test_init_pi_agent_records_agent_and_installs_project_package(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: False)
    monkeypatch.setattr("opentraces.cli._install_skill", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("opentraces.cli._current_project_session_dir", lambda _project_dir, cfg=None: None)

    prev = Path.cwd()
    try:
        os.chdir(project)
        result = CliRunner().invoke(main, ["init", "--agent", "pi", "--start-fresh"])
    finally:
        os.chdir(prev)

    assert result.exit_code == 0, result.output
    payload = _json_payload(result.output)
    assert payload["agents"] == ["pi"]
    settings = json.loads((project / ".pi" / "settings.json").read_text())
    assert any("opentraces-pi" in str(pkg) for pkg in settings["packages"])


def test_init_pi_agent_merges_into_existing_project(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: False)
    monkeypatch.setattr("opentraces.cli._install_skill", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("opentraces.cli._current_project_session_dir", lambda _project_dir, cfg=None: None)

    runner = CliRunner()
    prev = Path.cwd()
    try:
        os.chdir(project)
        first = runner.invoke(main, ["init", "--agent", "claude-code", "--start-fresh"])
        second = runner.invoke(main, ["init", "--agent", "pi", "--start-fresh"])
    finally:
        os.chdir(prev)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    payload = _json_payload(second.output)
    assert payload["agents_added"] == ["pi"]
    assert payload["agents"] == ["claude-code", "pi"]
    assert any("opentraces-pi" in str(pkg) for pkg in json.loads((project / ".pi" / "settings.json").read_text())["packages"])


def test_hidden_pi_bridge_records_via_cli_payload_file(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".opentraces.json").write_text(json.dumps({"project_id": "p", "agents": ["pi"]}))
    payload_file = tmp_path / "payload.json"
    payload_file.write_text(json.dumps({"event": "session_start", "session_id": "s", "cwd": str(project)}))
    monkeypatch.chdir(project)

    result = CliRunner().invoke(main, ["_pi-bridge", "--payload-file", str(payload_file)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert Path(payload["sidecar_path"]).exists()


def test_pi_hook_installer_registered() -> None:
    from opentraces import capture
    from opentraces.capture.pi.install import PiHookInstaller

    assert capture.get_hook_installers()["pi"] is PiHookInstaller

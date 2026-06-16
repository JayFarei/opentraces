"""Issue #78: setup upgrade repairs installed integration glue."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from click.testing import CliRunner

from opentraces import __version__
from opentraces.cli import main


def _parse_last_json(output: str) -> dict:
    marker = "---OPENTRACES_JSON---"
    if marker in output:
        _, _, output = output.rpartition(marker)
    return json.loads(output.strip())


class _Completed:
    returncode = 0
    stdout = ""
    stderr = ""


def _fake_launchctl(*_args, **_kwargs) -> _Completed:
    return _Completed()


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def _install_then_stale_watcher(monkeypatch) -> Path:
    from opentraces.watcher import installer as watcher_installer

    monkeypatch.setattr(watcher_installer, "current_platform", lambda: "macos")
    monkeypatch.setattr(watcher_installer, "_launchctl", _fake_launchctl)
    watcher_installer.install(interval=123)
    shim = Path(os.environ["HOME"]) / ".opentraces" / "bin" / "ot-watcher"
    shim.write_text(
        "#!/bin/sh\n"
        "# opentraces-version: 0.0.1\n"
        'exec "python3" -m opentraces.watcher.daemon run-forever "$@"\n',
        encoding="utf-8",
    )
    return shim


def _install_then_stale_git_hook(project: Path) -> Path:
    from opentraces.capture.git import install as git_install

    assert git_install.install(project)
    hook = project / ".git" / "hooks" / "opentraces-post-commit"
    hook.write_text(
        hook.read_text(encoding="utf-8").replace(
            f"opentraces-version: {__version__}",
            "opentraces-version: 0.0.1",
        ),
        encoding="utf-8",
    )
    return hook


def test_setup_upgrade_integrations_only_regenerates_watcher_and_git(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    _init_git_repo(tmp_path)
    shim = _install_then_stale_watcher(monkeypatch)
    hook = _install_then_stale_git_hook(tmp_path)

    result = CliRunner().invoke(main, ["setup", "upgrade", "--integrations-only"])

    assert result.exit_code == 0, result.output
    shim_text = shim.read_text(encoding="utf-8")
    assert f"opentraces-version: {__version__}" in shim_text
    assert "setup watcher sweep" in shim_text
    assert "run-forever" not in shim_text
    assert f"opentraces-version: {__version__}" in hook.read_text(encoding="utf-8")

    payload = _parse_last_json(result.output)
    repaired = {item["name"] for item in payload["integration_repair"]["repaired"]}
    assert {"watcher", "git"} <= repaired


def test_doctor_reports_unrepaired_version_drift_for_watcher_and_git(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "opentraces.core.integration_versions._fetch_latest_pypi_version",
        lambda: __version__,
    )
    _init_git_repo(tmp_path)
    _install_then_stale_watcher(monkeypatch)
    _install_then_stale_git_hook(tmp_path)

    result = CliRunner().invoke(main, ["--json", "doctor"])

    assert result.exit_code == 3, result.output
    payload = _parse_last_json(result.output)
    doctor = payload["doctor"]
    watcher_drift = doctor["watcher"]["provenance"]["drift"]
    assert "shim-legacy-verb" in watcher_drift
    assert "shim-version-drift" in watcher_drift
    git_hook = next(h for h in doctor["hooks"] if h["installer"] == "git")
    assert "version-drift" in git_hook["drift"]
    drift_names = {item["name"] for item in doctor["integrations"]["drift"]}
    assert {"watcher", "git"} <= drift_names


def test_doctor_surfaces_upgrade_available_for_user_and_agent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "opentraces.core.integration_versions._fetch_latest_pypi_version",
        lambda: "999.0.0",
    )

    # Issue #89: doctor emits human XOR JSON. The human upgrade hint only
    # renders on an interactive TTY (otherwise piped output is JSON-only, with
    # no leaked diagnostics). Click's test runner is non-TTY, so force the human
    # path through the single decision seam.
    monkeypatch.setattr(
        "opentraces.cli.doctor_cli._doctor_json_only", lambda: False
    )
    human = CliRunner().invoke(main, ["doctor"])
    assert human.exit_code == 0, human.output
    assert "v999.0.0 available" in human.output
    assert "opentraces setup upgrade" in human.output

    # The agent/JSON surface comes through the root --json mode (or any piped,
    # non-TTY run).
    result = CliRunner().invoke(main, ["--json", "doctor"])
    assert result.exit_code == 0, result.output
    payload = _parse_last_json(result.output)
    cli = payload["doctor"]["cli"]
    assert cli["installed_version"] == __version__
    assert cli["latest_version"] == "999.0.0"
    assert cli["upgrade_available"] is True


def test_doctor_emits_agent_next_command_when_upgrade_available(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Agent consumers drive off top-level next_command / next_steps, not the
    buried doctor.cli.upgrade_available flag. An available upgrade must surface
    an explicit, machine-actionable directive to run the upgrade."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "opentraces.core.integration_versions._fetch_latest_pypi_version",
        lambda: "999.0.0",
    )

    result = CliRunner().invoke(main, ["--json", "doctor"])

    assert result.exit_code == 0, result.output
    payload = _parse_last_json(result.output)
    assert payload["next_command"] == "opentraces setup upgrade"
    assert payload["next_steps"]
    assert "999.0.0" in payload["next_steps"][0]
    assert "setup upgrade" in payload["next_steps"][0]


def test_doctor_next_command_is_integrations_only_on_drift_without_upgrade(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """With no newer CLI but drifted glue, the agent directive points at the
    integrations-only repair (not a full CLI upgrade)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "opentraces.core.integration_versions._fetch_latest_pypi_version",
        lambda: __version__,
    )
    _init_git_repo(tmp_path)
    _install_then_stale_git_hook(tmp_path)

    result = CliRunner().invoke(main, ["--json", "doctor"])

    assert result.exit_code == 3, result.output
    payload = _parse_last_json(result.output)
    assert payload["next_command"] == "opentraces setup upgrade --integrations-only"
    assert "git" in payload["next_steps"][0]


def test_doctor_directive_prefers_full_upgrade_over_drift_repair(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """When BOTH a newer CLI and drifted glue exist, the agent directive points
    at the full `setup upgrade` (which also re-renders glue), even though the
    exit code stays 3 because of the drift."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "opentraces.core.integration_versions._fetch_latest_pypi_version",
        lambda: "999.0.0",
    )
    _init_git_repo(tmp_path)
    _install_then_stale_git_hook(tmp_path)

    result = CliRunner().invoke(main, ["--json", "doctor"])

    assert result.exit_code == 3, result.output
    payload = _parse_last_json(result.output)
    assert payload["next_command"] == "opentraces setup upgrade"


def test_doctor_emits_no_directive_when_healthy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A healthy, up-to-date install emits no upgrade directive."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "opentraces.core.integration_versions._fetch_latest_pypi_version",
        lambda: __version__,
    )

    result = CliRunner().invoke(main, ["--json", "doctor"])

    assert result.exit_code == 0, result.output
    payload = _parse_last_json(result.output)
    assert "next_command" not in payload
    assert "next_steps" not in payload

"""Tests for the doctor baked-interpreter health check (issue #86).

The check flags opentraces-owned hook commands whose baked interpreter is an
upgrade time-bomb: either the interpreter path is missing on disk, or it is a
version-pinned Homebrew Cellar path that ``brew upgrade`` deletes. Covers
Codex (``~/.codex/hooks.json``), Claude (``~/.claude/settings.json``), and the
Git ``opentraces-post-commit`` shim, plus robustness to missing/corrupt files.
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from opentraces.core import doctor


def _write_codex_hooks(home: Path, command: str) -> None:
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    (codex_dir / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {"hooks": [{"type": "command", "command": command}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


def _write_claude_settings(home: Path, command: str) -> None:
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {"hooks": [{"type": "command", "command": command}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


def _write_git_hook(repo: Path, python: str) -> None:
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "opentraces-post-commit").write_text(
        "#!/usr/bin/env sh\n"
        "set +e\n"
        f'"{python}" -m opentraces _run-post-commit-hook "$(pwd)" >/dev/null 2>&1 || true\n'
        "exit 0\n",
        encoding="utf-8",
    )


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point HOME (and thus codex_home / Path.home) at an isolated dir."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def test_dead_cellar_pin_warns(fake_home: Path, tmp_path: Path) -> None:
    # A version-pinned Cellar path for a version that no longer exists.
    dead = "/opt/homebrew/Cellar/opentraces/0.4.4/libexec/bin/python"
    _write_codex_hooks(fake_home, f"{shlex.quote(dead)} -m opentraces.capture.codex_cli.hooks.on_tool_use")

    report = doctor._interpreter_health(tmp_path / "repo")

    assert report["status"] == "warn"
    findings = report["findings"]
    assert len(findings) == 1
    f = findings[0]
    assert f["integration"] == "codex"
    assert f["event"] == "PostToolUse"
    assert f["interpreter"] == dead
    assert f["remedy"] == "Run: opentraces setup upgrade --integrations-only"
    # Missing-on-disk takes precedence in the reason text; either way it warns.
    assert f["reason"]


def test_live_cellar_pin_warns(
    fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A Cellar pin that currently resolves on disk is STILL a time-bomb.
    pinned = "/opt/homebrew/Cellar/opentraces/9.9.9/libexec/bin/python"
    _write_claude_settings(fake_home, f"{shlex.quote(pinned)} /some/hook.py")

    # Force the path to "exist" so we exercise branch (b) not (a).
    real_exists = doctor.os.path.exists
    monkeypatch.setattr(
        doctor.os.path,
        "exists",
        lambda p: True if p == pinned else real_exists(p),
    )

    report = doctor._interpreter_health(tmp_path / "repo")
    assert report["status"] == "warn"
    findings = report["findings"]
    assert len(findings) == 1
    assert findings[0]["integration"] == "claude"
    assert findings[0]["event"] == "Stop"
    assert "Cellar" in findings[0]["reason"]


def test_stable_pipx_path_ok(
    fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipx = "/Users/dev/.local/pipx/venvs/opentraces/bin/python"
    _write_codex_hooks(fake_home, f"{shlex.quote(pipx)} -m opentraces.capture.codex_cli.hooks.on_tool_use")

    # Make the pipx interpreter "exist" so it is not flagged as missing.
    real_exists = doctor.os.path.exists
    monkeypatch.setattr(
        doctor.os.path,
        "exists",
        lambda p: True if p == pipx else real_exists(p),
    )

    report = doctor._interpreter_health(tmp_path / "repo")
    assert report["status"] == "ok"
    assert report["findings"] == []


def test_missing_interpreter_warns(fake_home: Path, tmp_path: Path) -> None:
    # A non-Cellar interpreter that simply does not exist on disk.
    missing = "/nonexistent/venv/bin/python"
    repo = tmp_path / "repo"
    _write_git_hook(repo, missing)

    report = doctor._interpreter_health(repo)
    assert report["status"] == "warn"
    findings = report["findings"]
    assert len(findings) == 1
    assert findings[0]["integration"] == "git"
    assert findings[0]["event"] == "post-commit"
    assert findings[0]["interpreter"] == missing
    assert "does not exist" in findings[0]["reason"]


def test_missing_config_files_ok(fake_home: Path, tmp_path: Path) -> None:
    # No configs written at all -> ok, empty, never raises.
    report = doctor._interpreter_health(tmp_path / "repo")
    assert report == {"status": "ok", "findings": []}


def test_corrupt_config_files_ok(fake_home: Path, tmp_path: Path) -> None:
    (fake_home / ".codex").mkdir(parents=True)
    (fake_home / ".codex" / "hooks.json").write_text("{not json", encoding="utf-8")
    (fake_home / ".claude").mkdir(parents=True)
    (fake_home / ".claude" / "settings.json").write_text("]]]garbage", encoding="utf-8")
    # A git hook that is unreadable/garbage should not crash either.
    repo = tmp_path / "repo"
    (repo / ".git" / "hooks").mkdir(parents=True)
    (repo / ".git" / "hooks" / "opentraces-post-commit").write_text(
        "\x00\x00not a real shim", encoding="utf-8"
    )

    report = doctor._interpreter_health(repo)
    assert report["status"] == "ok"
    assert report["findings"] == []


def test_non_opentraces_command_ignored(
    fake_home: Path, tmp_path: Path
) -> None:
    # A foreign hook with a dead Cellar interpreter is NOT ours -> ignored.
    dead = "/opt/homebrew/Cellar/somethingelse/1.0.0/bin/python"
    _write_codex_hooks(fake_home, f"{shlex.quote(dead)} -m someother.hook")

    report = doctor._interpreter_health(tmp_path / "repo")
    assert report["status"] == "ok"
    assert report["findings"] == []


def test_report_includes_interpreter_health_key(
    fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Smoke test: the full report dict carries the additive key and existing
    # keys remain present.
    from opentraces.core.config import load_config

    monkeypatch.chdir(tmp_path)
    try:
        cfg = load_config()
    except Exception:
        pytest.skip("global config unavailable in this environment")
    report = doctor.report(cfg, tmp_path)
    assert "interpreter_health" in report
    assert set(report["interpreter_health"]) == {"status", "findings"}
    # Existing JSON consumers must keep working.
    assert "post_commit_hook" in report
    assert "integrations" in report

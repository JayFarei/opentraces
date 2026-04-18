"""Opt-in tmux smoke test for the real TUI entry point."""

from __future__ import annotations

import os
import shutil
import shlex
import subprocess
import time
import uuid

import pytest

from ._smoke_helpers import require_opt_in, seed_opted_in_project


def _require_tui_smoke_env() -> None:
    require_opt_in("OT_TUI_SMOKE", "the tmux-driven TUI smoke test")
    if not shutil.which("tmux"):
        pytest.skip("tmux not on PATH")


def _tmux(*args: str, check: bool = True, timeout: float = 15.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tmux", *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _tmux_has_session(session_name: str) -> bool:
    return _tmux("has-session", "-t", session_name, check=False).returncode == 0


def _capture_pane(session_name: str) -> str:
    return _tmux("capture-pane", "-p", "-t", session_name).stdout


def _wait_for_pane_text(session_name: str, *needles: str, timeout_s: float = 20.0) -> str:
    deadline = time.monotonic() + timeout_s
    last_pane = ""
    while time.monotonic() < deadline:
        if not _tmux_has_session(session_name):
            pytest.fail(f"tmux session {session_name} exited before the TUI rendered")
        last_pane = _capture_pane(session_name)
        if all(needle in last_pane for needle in needles):
            return last_pane
        time.sleep(0.25)
    pytest.fail(
        "timed out waiting for the TUI to render expected text\n"
        f"--- tmux pane ({session_name}) ---\n{last_pane}"
    )


def _wait_for_tmux_exit(session_name: str, *, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _tmux_has_session(session_name):
            return
        time.sleep(0.25)
    pane = _capture_pane(session_name)
    pytest.fail(
        "TUI did not exit after sending q\n"
        f"--- tmux pane ({session_name}) ---\n{pane}"
    )


@pytest.fixture
def tmux_session_name() -> str:
    _require_tui_smoke_env()
    session_name = f"ot-tui-smoke-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    yield session_name
    _tmux("kill-session", "-t", session_name, check=False)


def test_tui_launches_and_quits_in_tmux(tmp_path, tmux_session_name: str) -> None:
    seeded = seed_opted_in_project(
        tmp_path,
        task_description="tmux smoke trace",
        trace_id="trace-tui-smoke-001",
        session_id="sess-tui-smoke-001",
    )

    command = " ".join(
        [
            "env",
            f"HOME={shlex.quote(str(seeded.home))}",
            "HF_HUB_DISABLE_IMPLICIT_TOKEN=1",
            "TERM=xterm-256color",
            shlex.join(seeded.cli_cmd("tui")),
        ]
    )

    _tmux(
        "new-session",
        "-d",
        "-s",
        tmux_session_name,
        "-c",
        str(seeded.project_dir),
        "-x",
        "200",
        "-y",
        "50",
        command,
    )

    pane = _wait_for_pane_text(
        tmux_session_name,
        "[2] Inbox",
        "[5] Trace Preview",
        "tmux smoke",
        "j/k move",
    )
    assert "project" in pane
    assert "smoke/repo" in pane

    _tmux("send-keys", "-t", tmux_session_name, "q")
    _wait_for_tmux_exit(tmux_session_name)

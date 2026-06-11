"""Per-turn git-state verification + fresh-session drive support (issue #49).

These default-CI-safe unit tests pin the verification machinery the drive
core executes INSIDE the box after a turn's ``expect_regex`` matches and
BEFORE the next prompt is sent. They exercise the pure verification
helper against a fake driver/box so no real agent binary, tmux, or
terminal-control is required.

The behavior under test:
  * ``verify_command`` runs in the box project via ``driver.exec`` and its
    combined output must match ``verify_regex`` — a miss yields a FAIL that
    names the turn and the failed assertion (not a silent pass on the TUI
    transcript regex alone).
  * ``expect_revert_commit`` asserts a commit carries the literal
    ``This reverts commit `` body marker.
  * a turn with no verification fields is a no-op (legacy behavior).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from tests.otbox.drivers.base import ExecResult
from tests.otbox.simulated_users import drive
from tests.otbox.simulated_users.scenario import Turn


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------
@dataclass
class _FakeBox:
    project: str = "/box/project"


@dataclass
class _FakeDriver:
    """Records exec calls and replays scripted ExecResults by argv match."""

    responses: list[tuple[list[str], ExecResult]] = field(default_factory=list)
    calls: list[list[str]] = field(default_factory=list)

    def paths(self, box) -> dict:
        return {"project": box.project, "home": "/box/home", "root": "/box"}

    def exec(self, box, argv, *, cwd=None, env_extra=None, timeout=None, live_hf=False):
        self.calls.append(list(argv))
        for pattern, result in self.responses:
            if list(argv) == pattern:
                return result
        return ExecResult(argv=list(argv), returncode=0, stdout="", stderr="", duration_s=0.0)


def _exec(argv, *, rc=0, stdout="", stderr="") -> ExecResult:
    return ExecResult(argv=list(argv), returncode=rc, stdout=stdout, stderr=stderr, duration_s=0.0)


# ---------------------------------------------------------------------------
# verify_command / verify_regex
# ---------------------------------------------------------------------------
def test_verify_passes_when_regex_matches_command_output():
    turn = Turn(
        prompt="commit it",
        expect_regex="(?i)committed",
        verify_command=["git", "log", "-1", "--format=%H"],
        verify_regex="[0-9a-f]{7,}",
    )
    cmd = ["git", "-C", "/box/project", "log", "-1", "--format=%H"]
    driver = _FakeDriver(responses=[(cmd, _exec(cmd, stdout="deadbeef0123\n"))])
    ok, msg = drive._run_turn_verification(driver, _FakeBox(), turn, 0)
    assert ok, msg
    assert msg == ""
    assert cmd in driver.calls


def test_verify_fails_and_names_turn_and_assertion():
    turn = Turn(
        prompt="commit it",
        expect_regex="(?i)committed",
        verify_command=["git", "log", "-1", "--format=%H"],
        verify_regex="[0-9a-f]{7,}",
    )
    cmd = ["git", "-C", "/box/project", "log", "-1", "--format=%H"]
    # No commit landed → git prints nothing → regex misses.
    driver = _FakeDriver(responses=[(cmd, _exec(cmd, stdout="\n"))])
    ok, msg = drive._run_turn_verification(driver, _FakeBox(), turn, 2)
    assert not ok
    assert "turn 2" in msg
    assert "[0-9a-f]{7,}" in msg


def test_verify_noop_when_no_fields():
    turn = Turn(prompt="hi", expect_regex="(?i)ok")
    driver = _FakeDriver()
    ok, msg = drive._run_turn_verification(driver, _FakeBox(), turn, 0)
    assert ok and msg == ""
    assert driver.calls == []


def test_verify_command_failure_surfaces_as_fail():
    turn = Turn(
        prompt="x",
        expect_regex="(?i)ok",
        verify_command=["git", "rev-parse", "HEAD"],
        verify_regex="[0-9a-f]{7,}",
    )
    cmd = ["git", "-C", "/box/project", "rev-parse", "HEAD"]
    driver = _FakeDriver(
        responses=[(cmd, _exec(cmd, rc=128, stderr="fatal: not a git repo"))]
    )
    ok, msg = drive._run_turn_verification(driver, _FakeBox(), turn, 1)
    assert not ok
    assert "turn 1" in msg


# ---------------------------------------------------------------------------
# expect_revert_commit
# ---------------------------------------------------------------------------
def test_revert_assertion_passes_on_marker():
    turn = Turn(prompt="revert", expect_regex="(?i)revert", expect_revert_commit=True)
    # The revert log scan finds the literal body marker.
    cmd = ["git", "-C", "/box/project", "log", "-n", "20", "--format=%B"]
    body = "Revert \"add shout helper\"\n\nThis reverts commit abc1234.\n"
    driver = _FakeDriver(responses=[(cmd, _exec(cmd, stdout=body))])
    ok, msg = drive._run_turn_verification(driver, _FakeBox(), turn, 1)
    assert ok, msg


def test_revert_assertion_fails_without_marker():
    turn = Turn(prompt="revert", expect_regex="(?i)revert", expect_revert_commit=True)
    cmd = ["git", "-C", "/box/project", "log", "-n", "20", "--format=%B"]
    driver = _FakeDriver(
        responses=[(cmd, _exec(cmd, stdout="add shout helper\n\nsome body\n"))]
    )
    ok, msg = drive._run_turn_verification(driver, _FakeBox(), turn, 1)
    assert not ok
    assert "turn 1" in msg
    assert "reverts commit" in msg.lower()


# ---------------------------------------------------------------------------
# enable_security_tools ordering — flip happens during box prep, after the
# hook install (`opentraces init` created the config) and BEFORE the agent
# spawns, so the Stop-hook ingest sanitizes with the tools active.
# ---------------------------------------------------------------------------
def test_security_tools_flip_runs_in_prep_after_hook_install(monkeypatch):
    from pathlib import Path

    from tests.otbox.simulated_users import prep

    calls: list[str] = []

    monkeypatch.setattr(
        prep, "_ensure_box_project_git_repo", lambda box: None
    )
    monkeypatch.setattr(prep, "_prep_agent_home", lambda home, agent: None)
    monkeypatch.setattr(
        prep, "_prep_claude_project_trust", lambda home, project: None
    )
    monkeypatch.setattr(
        prep,
        "_install_opentraces_hooks_in_box",
        lambda box, agent: calls.append("install-hooks") or None,
    )
    monkeypatch.setattr(
        prep,
        "_enable_security_tools_in_box",
        lambda box, tools: calls.append(f"enable-security:{','.join(tools)}")
        or None,
    )

    @dataclass
    class _PrepBox:
        project: Path = Path("/box/project")
        home: Path = Path("/box/home")

    skip_msg, fail_msg, _env = drive._prepare_box_for_agent(
        _PrepBox(),
        "/usr/bin/claude",
        "1.0.0",
        "claude",
        None,
        enable_security_tools=["regex", "entropy"],
    )
    assert skip_msg is None and fail_msg is None
    assert calls == ["install-hooks", "enable-security:regex,entropy"], calls


def test_security_tools_flip_failure_fails_prep(monkeypatch):
    from pathlib import Path

    from tests.otbox.simulated_users import prep

    monkeypatch.setattr(
        prep, "_ensure_box_project_git_repo", lambda box: None
    )
    monkeypatch.setattr(prep, "_prep_agent_home", lambda home, agent: None)
    monkeypatch.setattr(
        prep, "_prep_claude_project_trust", lambda home, project: None
    )
    monkeypatch.setattr(
        prep, "_install_opentraces_hooks_in_box", lambda box, agent: None
    )
    monkeypatch.setattr(
        prep,
        "_enable_security_tools_in_box",
        lambda box, tools: "enable security tools failed (rc=1)",
    )

    @dataclass
    class _PrepBox:
        project: Path = Path("/box/project")
        home: Path = Path("/box/home")

    skip_msg, fail_msg, _env = drive._prepare_box_for_agent(
        _PrepBox(),
        "/usr/bin/claude",
        "1.0.0",
        "claude",
        None,
        enable_security_tools=["regex"],
    )
    assert skip_msg is None
    assert fail_msg is not None and "security tools" in fail_msg

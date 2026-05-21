"""Pytest meta-tests for the PTY runner (plan 071, R1 + R3 + R6).

The runner is intentionally tested against the bundled echo binary
(``_echo_binary.py``) so default CI exercises the send-keys / expect /
capture machinery without needing a real ``claude`` on PATH. Real-
agent scenarios are exercised via the higher-level ``capture-refresh``
flow Agent C owns, gated on the agent binary actually being installed
(M71-5).

The four cases below pin the public contract:
  1. Missing binary -> SKIP (caller-decided severity).
  2. Echo binary + valid turns -> PASS, turn_count == 2,
     binary_version populated.
  3. Unsatisfiable expect on turn 2 -> FAIL with turn_count == 1 and
     ``error_message`` referencing the failing regex.
  4. ``pane.log`` exists post-PASS and contains both prompts (so the
     forensic trail is intact even on the happy path).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.otbox.checkpoints import resolve_checkpoint
from tests.otbox.drivers import get_driver
from tests.otbox.env import isolated_env
from tests.otbox.simulated_users import runner as runner_module
from tests.otbox.simulated_users.runner import (
    ScenarioResult,
    Turn,
    _dismiss_codex_hook_review,
    _ensure_box_project_git_repo,
    _install_opentraces_hooks_in_box,
    _prep_codex_project_trust,
    _prep_agent_home,
    run_simulated_session,
)

ECHO_BINARY = (
    Path(__file__).resolve().parent / "_echo_binary.py"
)


@pytest.fixture(autouse=True)
def _isolate_opentraces_global_state():
    """Same override pattern as tests/otbox/test_agent_session_slice.py
    — the otbox harness isolates HOME via the driver, so the repo-wide
    conftest autouse fixture must be neutralised or it would redirect
    HOME elsewhere and break the box lifecycle."""
    yield


@pytest.fixture
def driver():
    if not shutil.which("tmux"):
        pytest.skip("tmux not installed on PATH")
    return get_driver("local")


@pytest.fixture
def installed_box(driver):
    """A fresh box forked from ``c-installed-source``.

    Teardown deletes the box's working directory so meta-tests leave
    no residue under ``.otbox/boxes/``.
    """
    cp = resolve_checkpoint(driver, "c-installed-source")
    try:
        yield cp.box
    finally:
        if cp.box.root.exists():
            driver.teardown(cp.box)


def _output_dir(tmp_path: Path) -> Path:
    out = tmp_path / "runner_output"
    out.mkdir(parents=True, exist_ok=True)
    return out


# ---------------------------------------------------------------------------
# 1. SKIP on missing binary
# ---------------------------------------------------------------------------
def test_runner_skips_when_binary_missing(driver, installed_box, tmp_path):
    """A missing binary must NOT raise — it returns verdict SKIP so
    the caller (Agent C / CI) can decide whether to fail-soft."""
    missing = "/definitely/not/a/real/binary/path/agent-xyz"
    result = run_simulated_session(
        driver=driver,
        box=installed_box,
        binary=missing,
        turns=[Turn(prompt="hi", expect_regex="hello", timeout_s=2.0)],
        output_dir=_output_dir(tmp_path),
    )
    assert isinstance(result, ScenarioResult)
    assert result.verdict == "SKIP"
    assert result.turn_count == 0
    assert missing in result.error_message, result.error_message
    # pane.log must still exist (caller relies on the path being valid).
    assert Path(result.pane_log_path).exists()


# ---------------------------------------------------------------------------
# 2. Happy-path: echo binary drives 2 turns to PASS
# ---------------------------------------------------------------------------
def test_runner_drives_echo_binary(driver, installed_box, tmp_path):
    """Echo binary + two well-formed turns must reach PASS with the
    correct turn_count and a parsed binary_version."""
    turns = [
        Turn(
            prompt="Add a farewell helper to src/app.py",
            expect_regex=r"(?i)I'?ll add",
            timeout_s=10.0,
        ),
        Turn(
            prompt="yes",
            expect_regex=r"(?i)Done!",
            timeout_s=10.0,
        ),
    ]
    result = run_simulated_session(
        driver=driver,
        box=installed_box,
        binary=str(ECHO_BINARY),
        turns=turns,
        output_dir=_output_dir(tmp_path),
    )
    assert result.verdict == "PASS", (
        f"runner FAIL/SKIP: {result.verdict} — {result.error_message}\n"
        f"pane excerpt:\n{result.pane_excerpt}"
    )
    assert result.turn_count == 2
    assert result.binary_version == "otbox-echo 1.0.0", result.binary_version
    assert result.binary_path.endswith("_echo_binary.py")


# ---------------------------------------------------------------------------
# 3. FAIL: turn 1 PASSes, turn 2 expects an unmatchable regex
# ---------------------------------------------------------------------------
def test_runner_fails_on_unmet_expect(driver, installed_box, tmp_path):
    """When a turn's expect_regex never matches inside its timeout the
    runner must return FAIL with turn_count reflecting how many turns
    DID succeed before the failure."""
    turns = [
        Turn(
            prompt="Add a farewell helper",
            expect_regex=r"(?i)I'?ll add",
            timeout_s=10.0,
        ),
        Turn(
            prompt="yes",
            expect_regex=r"this-string-will-never-appear-zzz-xyzzy",
            timeout_s=2.0,  # bounded so the test stays fast
        ),
    ]
    result = run_simulated_session(
        driver=driver,
        box=installed_box,
        binary=str(ECHO_BINARY),
        turns=turns,
        output_dir=_output_dir(tmp_path),
    )
    assert result.verdict == "FAIL", (
        f"expected FAIL, got {result.verdict} — {result.error_message}"
    )
    # turn 0 succeeded (the "I'll add" regex matched the echo response)
    # but turn 1's regex never appears so turn_count must be exactly 1.
    assert result.turn_count == 1, (
        f"turn_count={result.turn_count}; "
        f"error={result.error_message}; pane={result.pane_excerpt!r}"
    )
    assert "this-string-will-never-appear" in result.error_message
    # Binary version still detectable even on FAIL — proves we did the
    # --version probe before spawning the failing session.
    assert result.binary_version == "otbox-echo 1.0.0"


# ---------------------------------------------------------------------------
# 4. pane.log captures conversation flow on PASS
# ---------------------------------------------------------------------------
def test_runner_writes_pane_log(driver, installed_box, tmp_path):
    """The pane log must exist after a PASS and must record both
    prompts so post-hoc debugging has something to read."""
    turns = [
        Turn(
            prompt="Add a farewell helper to src/app.py",
            expect_regex=r"(?i)I'?ll add",
            timeout_s=10.0,
        ),
        Turn(
            prompt="yes",
            expect_regex=r"(?i)Done!",
            timeout_s=10.0,
        ),
    ]
    result = run_simulated_session(
        driver=driver,
        box=installed_box,
        binary=str(ECHO_BINARY),
        turns=turns,
        output_dir=_output_dir(tmp_path),
    )
    assert result.verdict == "PASS", result.error_message
    log_path = Path(result.pane_log_path)
    assert log_path.exists(), f"pane.log missing at {log_path}"
    body = log_path.read_text(encoding="utf-8", errors="replace")
    # Both turn headers + both prompts should be referenced in the log.
    assert "turn 0" in body and "turn 1" in body, body[:500]
    assert "Add a farewell helper" in body or "farewell" in body.lower(), (
        body[:500]
    )
    assert "yes" in body, body[:500]


# ---------------------------------------------------------------------------
# 5. _prep_agent_home — claude HOME seeding (theme + auth + editor mode)
# ---------------------------------------------------------------------------
def test_prep_agent_home_noop_for_non_claude(tmp_path):
    """Non-real agents (echo, hermes, None) must be no-ops so
    the existing echo-meta fast path stays untouched."""
    box_home = tmp_path / "box_home"
    for agent in (None, "echo", "hermes"):
        assert _prep_agent_home(box_home, agent) is None
    # No files should have been created.
    assert not (box_home / ".claude.json").exists()
    assert not (box_home / ".claude" / ".credentials.json").exists()
    assert not (box_home / ".codex").exists()


def test_prep_agent_home_skips_when_host_unconfigured(tmp_path, monkeypatch):
    """If the host has no claude config the helper returns an error
    string so the runner can SKIP cleanly — never raise."""
    fake_host = tmp_path / "fake_host"
    fake_host.mkdir()
    monkeypatch.setenv("HOME", str(fake_host))
    box_home = tmp_path / "box_home"
    err = _prep_agent_home(box_home, "claude")
    assert err is not None and "not found" in err, err


def test_prep_agent_home_seeds_claude_box(tmp_path, monkeypatch):
    """Helper copies host claude config into the box and forces
    editorMode=emacs so the PTY runner's literal-text send-keys works."""
    import json
    fake_host = tmp_path / "fake_host"
    fake_host.mkdir()
    (fake_host / ".claude").mkdir()
    (fake_host / ".claude" / ".credentials.json").write_text("{\"oauthAccount\":\"x\"}")
    (fake_host / ".claude.json").write_text(json.dumps({
        "hasCompletedOnboarding": True,
        "editorMode": "vim",
        "anonymousId": "test-id",
    }))
    monkeypatch.setenv("HOME", str(fake_host))
    box_home = tmp_path / "box_home"
    err = _prep_agent_home(box_home, "claude")
    assert err is None, err
    settings = json.loads((box_home / ".claude.json").read_text())
    assert settings["editorMode"] == "emacs", "vim must be normalised to emacs"
    assert settings["hasCompletedOnboarding"] is True
    assert settings["anonymousId"] == "test-id"
    creds = (box_home / ".claude" / ".credentials.json").read_text()
    assert "oauthAccount" in creds


# ---------------------------------------------------------------------------
# 6. _prep_agent_home — codex HOME seeding (auth + sanitized config)
# ---------------------------------------------------------------------------
def test_prep_agent_home_skips_codex_when_host_auth_missing(tmp_path, monkeypatch):
    fake_host = tmp_path / "fake_host"
    (fake_host / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_host))

    err = _prep_agent_home(tmp_path / "box_home", "codex")

    assert err is not None
    assert "auth.json" in err


def test_prep_agent_home_seeds_codex_without_project_history(tmp_path, monkeypatch):
    fake_host = tmp_path / "fake_host"
    host_codex = fake_host / ".codex"
    host_codex.mkdir(parents=True)
    (host_codex / "auth.json").write_text('{"OPENAI_API_KEY":"test"}\n')
    (host_codex / "config.toml").write_text(
        '\n'.join([
            'model = "gpt-5.5"',
            'model_reasoning_effort = "xhigh"',
            '',
            '[projects."/Users/example/private-project"]',
            'trust_level = "trusted"',
            '',
            '[features]',
            'goals = true',
        ])
        + "\n"
    )
    monkeypatch.setenv("HOME", str(fake_host))
    box_home = tmp_path / "box_home"

    err = _prep_agent_home(box_home, "codex-cli")

    assert err is None
    assert (
        (box_home / ".codex" / "auth.json").read_text()
        == '{"OPENAI_API_KEY":"test"}\n'
    )
    config = (box_home / ".codex" / "config.toml").read_text()
    assert 'model = "gpt-5.5"' in config
    assert '[features]' in config
    assert "private-project" not in config
    assert "[projects." not in config


def test_prep_codex_project_trust_adds_only_box_project(tmp_path):
    box_home = tmp_path / "box_home"
    project = tmp_path / "box" / "project"
    project.mkdir(parents=True)
    (box_home / ".codex").mkdir(parents=True)
    (box_home / ".codex" / "config.toml").write_text('model = "gpt-5.5"\n')

    _prep_codex_project_trust(box_home, project)
    _prep_codex_project_trust(box_home, project)

    config = (box_home / ".codex" / "config.toml").read_text()
    assert 'model = "gpt-5.5"' in config
    assert f'[projects."{project.resolve()}"]' in config
    assert 'trust_level = "trusted"' in config
    assert config.count(f'[projects."{project.resolve()}"]') == 1


def test_isolated_env_sets_git_ceiling(monkeypatch, tmp_path):
    from tests.otbox import env as otbox_env
    from tests.otbox.env import Box

    monkeypatch.setattr(otbox_env, "BOXES_DIR", tmp_path / "boxes")
    box = Box("unit")

    env = isolated_env(box)

    assert env["GIT_CEILING_DIRECTORIES"] == str(box.root)


def test_ensure_box_project_git_repo_seeds_local_repo(tmp_path, monkeypatch):
    from tests.otbox import env as otbox_env
    from tests.otbox.env import Box

    monkeypatch.setattr(otbox_env, "BOXES_DIR", tmp_path / "boxes")
    box = Box("unit")
    box.project.mkdir(parents=True)
    (box.project / "README.md").write_text("fixture\n", encoding="utf-8")
    (box.project / "src").mkdir()
    (box.project / "src" / "app.py").write_text(
        'def greet(name: str) -> str:\n    return f"hello, {name}"\n',
        encoding="utf-8",
    )
    (box.project / ".testvenv" / "bin").mkdir(parents=True)
    (box.project / ".testvenv" / "bin" / "opentraces").write_text(
        "fixture", encoding="utf-8"
    )

    err = _ensure_box_project_git_repo(box)

    assert err is None
    assert (box.project / ".git").is_dir()
    tracked = subprocess.run(
        ["git", "-C", str(box.project), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert "README.md" in tracked
    assert "src/app.py" in tracked
    assert ".gitignore" in tracked
    assert ".testvenv/bin/opentraces" not in tracked


def test_dismiss_codex_hook_review_uses_trust_shortcut(monkeypatch):
    panes = iter([
        "Hooks\n⚠ 8 hooks need review\nPress t to trust all",
        "ready",
    ])
    keys: list[str] = []

    monkeypatch.setattr(runner_module, "_capture_pane", lambda _session: next(panes))
    monkeypatch.setattr(runner_module, "_send_tmux_key", lambda _session, key: keys.append(key))
    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)

    assert _dismiss_codex_hook_review("session") is True
    assert keys == ["t"]


def test_dismiss_codex_hook_review_handles_numbered_menu(monkeypatch):
    panes = iter([
        "Hooks need review\n› 1. Review hooks\n  2. Trust all and continue",
        "ready",
    ])
    keys: list[str] = []

    monkeypatch.setattr(runner_module, "_capture_pane", lambda _session: next(panes))
    monkeypatch.setattr(runner_module, "_send_tmux_key", lambda _session, key: keys.append(key))
    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)

    assert _dismiss_codex_hook_review("session") is True
    assert keys == ["Down", "Enter"]


# ---------------------------------------------------------------------------
# 7. Hook install dispatch
# ---------------------------------------------------------------------------
def test_install_hooks_dispatches_codex_cli(tmp_path, monkeypatch):
    from subprocess import CompletedProcess

    from tests.otbox import env as otbox_env
    from tests.otbox.env import Box

    monkeypatch.setattr(otbox_env, "BOXES_DIR", tmp_path / "boxes")
    box = Box("unit")
    cli = box.project / ".testvenv" / "bin" / "opentraces"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/bin/sh\n")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(
        "tests.otbox.simulated_users.runner.subprocess.run",
        fake_run,
    )

    err = _install_opentraces_hooks_in_box(box, "codex")

    assert err is None
    assert [call[1:] for call in calls] == [
        ["setup", "codex-cli"],
        ["init", "--start-fresh", "--agent", "codex-cli"],
        ["setup", "git"],
    ]


def test_install_hooks_dispatches_claude_and_noops_echo(tmp_path, monkeypatch):
    from subprocess import CompletedProcess

    from tests.otbox import env as otbox_env
    from tests.otbox.env import Box

    monkeypatch.setattr(otbox_env, "BOXES_DIR", tmp_path / "boxes")
    box = Box("unit")
    assert _install_opentraces_hooks_in_box(box, "echo") is None

    cli = box.project / ".testvenv" / "bin" / "opentraces"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/bin/sh\n")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(
        "tests.otbox.simulated_users.runner.subprocess.run",
        fake_run,
    )

    err = _install_opentraces_hooks_in_box(box, "claude-code")

    assert err is None
    assert [call[1:] for call in calls] == [
        ["setup", "claude-code"],
        ["init", "--start-fresh", "--agent", "claude-code"],
        ["setup", "git"],
    ]


def test_runner_codex_calls_prep_and_hook_install_before_tmux(tmp_path, monkeypatch):
    from tests.otbox import env as otbox_env
    from tests.otbox.env import Box
    from tests.otbox.simulated_users import runner as runner_mod

    monkeypatch.setattr(otbox_env, "BOXES_DIR", tmp_path / "boxes")
    box = Box("unit")
    box.project.mkdir(parents=True)
    calls: list[str] = []

    def fake_which(name: str):
        if name == "tmux":
            return "/usr/bin/tmux"
        return str(ECHO_BINARY) if name == str(ECHO_BINARY) else None

    monkeypatch.setattr(runner_mod.shutil, "which", fake_which)
    monkeypatch.setattr(
        runner_mod,
        "_prep_agent_home",
        lambda home, agent: calls.append(f"prep:{agent}") or None,
    )
    monkeypatch.setattr(
        runner_mod,
        "_prep_codex_project_trust",
        lambda home, project: calls.append("trust"),
    )
    monkeypatch.setattr(
        runner_mod,
        "_install_opentraces_hooks_in_box",
        lambda box_arg, agent: calls.append(f"install:{agent}") or "hook install failed",
    )

    result = run_simulated_session(
        driver=None,
        box=box,
        binary=str(ECHO_BINARY),
        turns=[Turn(prompt="hi", expect_regex="ok", timeout_s=1.0)],
        output_dir=_output_dir(tmp_path),
        agent="codex",
    )

    assert result.verdict == "FAIL"
    assert result.error_message == "hook install failed"
    assert calls == ["prep:codex", "trust", "install:codex"]

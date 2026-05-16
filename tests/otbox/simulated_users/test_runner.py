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
from pathlib import Path

import pytest

from tests.otbox.checkpoints import resolve_checkpoint
from tests.otbox.drivers import get_driver
from tests.otbox.simulated_users.runner import (
    ScenarioResult,
    Turn,
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

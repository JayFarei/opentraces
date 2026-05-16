"""PTY/tmux simulated-user runner (plan 071, R1).

Drives an interactive agent binary inside an otbox box through a
scripted sequence of (prompt, expect_regex, timeout) turns by way of
tmux. The runner is the substrate Agent B's scenario parser and
Agent C's ``capture-refresh`` CLI build on:

  * Agent B (``scenario.py``) parses the TOML scenarios and produces
    :class:`Turn` instances + scenario metadata.
  * Agent C (``cli.py`` / ``capture-refresh``) resolves a base
    checkpoint, applies an optional initial-state template, runs the
    PTY session, and snapshots the resulting box.

The contract surface is small on purpose: one :func:`run_simulated_session`
function returning one :class:`ScenarioResult` dataclass. Everything
else (template materialization, snapshotting, artifact storage) lives
in higher layers so this module stays focused on the tmux-driven
expect loop.

Isolation notes:
  * The tmux session is spawned with ``env <pinned-vars> <binary>``
    so the box's isolated HOME / opentraces_dir / fake_remote
    overrides survive even when a running tmux server inherits the
    developer's ambient environment. The pinning pattern is taken
    from ``journey.py::_run_step`` (the existing ``tmux`` step type).
  * tmux session names are keyed by ``box.box_id`` + a short hash of
    the binary path + turn count so concurrent runs cannot collide on
    a session name.
  * The session is always killed in a ``try/finally`` to prevent
    leaks even if a turn times out or the polling loop throws.

SKIP semantics:
  * Missing or non-executable binary -> ``verdict="SKIP"``.
  * Missing ``tmux`` on PATH -> ``verdict="SKIP"``.

The caller (Agent C) decides whether SKIP should be a hard failure
(``--strict``) or a clean exit.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..drivers.base import Driver
from ..env import Box, isolated_env


# ---------------------------------------------------------------------------
# dataclasses (public API surface — Agents B + C consume these)
# ---------------------------------------------------------------------------
@dataclass
class Turn:
    """One prompt -> expect_regex interaction with the agent binary.

    Field names + types are the cross-agent contract; do NOT rename
    without coordinating with ``scenario.py`` (Agent B) and the CLI
    layer (Agent C). ``timeout_s`` defaults to 60s to match the
    scenario-TOML default.
    """

    prompt: str
    expect_regex: str
    timeout_s: float = 60.0


@dataclass
class ScenarioResult:
    """Outcome of one PTY session.

    ``verdict``:
      * ``"PASS"`` — every turn's ``expect_regex`` matched within its
        ``timeout_s`` window.
      * ``"FAIL"`` — at least one turn failed (timeout, regex not
        matched, or unexpected runtime error). ``turn_count`` records
        how many turns succeeded BEFORE the failure.
      * ``"SKIP"`` — the run was abandoned before any turn fired
        (missing binary, missing tmux, etc.). ``turn_count`` is 0.

    ``binary_version`` is the first non-empty line of
    ``<binary> --version``, stripped, or ``""`` if the call fails /
    exits non-zero / produces no output.

    ``pane_log_path`` always points at a file the caller can read for
    forensic context — even on FAIL or SKIP the runner writes whatever
    pane content it managed to capture (which may be empty on SKIP).
    """

    verdict: str
    binary_path: str
    binary_version: str
    turn_count: int
    pane_log_path: str
    error_message: str = ""
    pane_excerpt: str = field(default="", repr=False)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _detect_binary_version(binary: str) -> str:
    """Return the first non-empty line of ``<binary> --version``.

    Returns ``""`` if the call fails or produces no parseable output.
    Bounded with a 5-second timeout so a broken binary cannot wedge
    the runner before the real session even starts.
    """
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    if result.returncode != 0:
        return ""
    for line in (result.stdout or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _session_name(box: Box, binary: str, turn_count: int) -> str:
    """Derive a unique tmux session name for this run.

    Keyed by ``box_id`` + a short hash of (binary path, turn count) so
    two scenarios against the same box do not collide on a session
    name even if they happen to run concurrently in tests.
    """
    salt = f"{binary}|{turn_count}".encode("utf-8")
    short = hashlib.sha256(salt).hexdigest()[:8]
    return f"otbox-sim-{box.box_id}-{short}"


def _build_env_prefix(box: Box, env_extra: dict[str, str] | None) -> list[str]:
    """Pinned ``env K=V ...`` prefix for the tmux-spawned command.

    Mirrors the journey ``tmux`` step pattern: only the keys that
    DIFFER from the ambient environment need to be pinned, which keeps
    the command line short while still defending against a running
    tmux server inheriting the developer's HOME.
    """
    full_env = isolated_env(box, env_extra)
    overrides = {
        k: v for k, v in full_env.items() if os.environ.get(k) != v
    }
    return ["env"] + [f"{k}={v}" for k, v in sorted(overrides.items())]


def _capture_pane(session: str) -> str:
    """Return the current pane content, or ``""`` if the capture fails."""
    cap = subprocess.run(
        ["tmux", "capture-pane", "-p", "-t", session],
        capture_output=True,
        text=True,
    )
    if cap.returncode != 0:
        return ""
    return cap.stdout or ""


def _send_keys(session: str, text: str) -> None:
    """Send ``text`` + Enter to the tmux session (literal-text mode)."""
    subprocess.run(
        ["tmux", "send-keys", "-t", session, "-l", text],
        capture_output=True,
    )
    subprocess.run(
        ["tmux", "send-keys", "-t", session, "Enter"],
        capture_output=True,
    )


def _kill_session(session: str) -> None:
    """Best-effort tmux session cleanup."""
    subprocess.run(
        ["tmux", "kill-session", "-t", session],
        capture_output=True,
    )


def _copy_initial_state(initial_state_dir: Path, project: Path) -> None:
    """Recursively copy template contents into ``project``.

    Files are overlaid onto the existing project directory (the
    project may already contain a git repo from the base checkpoint);
    existing files with the same name are overwritten so the template
    has the last word.
    """
    project.mkdir(parents=True, exist_ok=True)
    for entry in initial_state_dir.iterdir():
        target = project / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, target)


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------
def run_simulated_session(
    driver: Driver,
    box: Box,
    binary: str,
    turns: list[Turn],
    *,
    initial_state_dir: Path | None = None,
    output_dir: Path,
    env_extra: dict[str, str] | None = None,
) -> ScenarioResult:
    """Drive an interactive session with ``binary`` inside ``box`` via tmux.

    Parameters
    ----------
    driver:
        The substrate driver for this box. Currently used for paths
        only; tmux itself is host-side (same as the journey runner's
        ``tmux`` step type).
    box:
        The otbox box the binary runs against. ``box.project`` is the
        working directory; ``isolated_env(box)`` pins the environment.
    binary:
        Absolute (or PATH-resolvable) path to the agent binary. If the
        binary cannot be resolved / is not executable, the runner
        returns ``verdict="SKIP"``.
    turns:
        Ordered list of prompts to send. Each :class:`Turn` carries an
        ``expect_regex`` and ``timeout_s`` window the runner polls for
        before sending the next prompt.
    initial_state_dir:
        Optional template directory whose contents are recursively
        copied INTO ``box.project`` before the binary spawns. The
        runner does NOT create or own this directory — Agent B's
        scenario parser resolves it from ``[initial_state].template``.
    output_dir:
        Where the pane log lands. Created if missing. The runner
        always writes ``pane.log`` here, even on FAIL / SKIP, so the
        caller has forensic context.
    env_extra:
        Additional environment overrides passed through ``isolated_env``
        to the binary. Useful for scenario-level pins (e.g. forcing a
        particular model id for a real claude run).

    Returns
    -------
    :class:`ScenarioResult` — see the dataclass docstring for verdict
    semantics. The runner never raises on a turn-level failure; it
    encodes the failure as ``verdict="FAIL"`` + ``error_message``.
    Setup failures (no tmux, no binary, no output_dir writable) also
    return cleanly as SKIP / FAIL rather than raising.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pane_log_path = output_dir / "pane.log"
    # Truncate any prior log so a re-run starts clean.
    pane_log_path.write_text("", encoding="utf-8")

    # --- preflight: tmux + binary must be resolvable -----------------------
    if not shutil.which("tmux"):
        return ScenarioResult(
            verdict="SKIP",
            binary_path=binary,
            binary_version="",
            turn_count=0,
            pane_log_path=str(pane_log_path),
            error_message="tmux not installed on PATH",
        )

    resolved = shutil.which(binary) or (binary if Path(binary).is_file() else None)
    if resolved is None or not os.access(resolved, os.X_OK):
        return ScenarioResult(
            verdict="SKIP",
            binary_path=binary,
            binary_version="",
            turn_count=0,
            pane_log_path=str(pane_log_path),
            error_message=f"binary not found or not executable: {binary}",
        )
    binary_abs = str(Path(resolved).resolve())

    # --- optional template overlay before spawn ----------------------------
    if initial_state_dir is not None:
        if not initial_state_dir.exists() or not initial_state_dir.is_dir():
            return ScenarioResult(
                verdict="FAIL",
                binary_path=binary_abs,
                binary_version="",
                turn_count=0,
                pane_log_path=str(pane_log_path),
                error_message=(
                    f"initial_state_dir does not exist or is not a directory: "
                    f"{initial_state_dir}"
                ),
            )
        try:
            _copy_initial_state(initial_state_dir, Path(box.project))
        except OSError as exc:
            return ScenarioResult(
                verdict="FAIL",
                binary_path=binary_abs,
                binary_version="",
                turn_count=0,
                pane_log_path=str(pane_log_path),
                error_message=f"failed to copy initial_state_dir: {exc}",
            )

    binary_version = _detect_binary_version(binary_abs)

    # --- spawn tmux session ------------------------------------------------
    session = _session_name(box, binary_abs, len(turns))
    env_prefix = _build_env_prefix(box, env_extra)
    wrapped = " ".join(shlex.quote(a) for a in (*env_prefix, binary_abs))

    spawn = subprocess.run(
        [
            "tmux", "new-session", "-d", "-s", session,
            "-x", "200", "-y", "50", wrapped,
        ],
        cwd=str(box.project),
        capture_output=True,
        text=True,
    )
    if spawn.returncode != 0:
        return ScenarioResult(
            verdict="FAIL",
            binary_path=binary_abs,
            binary_version=binary_version,
            turn_count=0,
            pane_log_path=str(pane_log_path),
            error_message=(
                f"tmux new-session failed (rc={spawn.returncode}): "
                f"{spawn.stderr.strip() or spawn.stdout.strip()}"
            ),
        )

    # Keep the pane alive after the binary exits so the final turn's
    # output is still capturable. Without this, a binary that prints
    # its last line then exits (like the echo meta-test binary) leaves
    # the pane in a dead state; capture-pane fails with "can't find
    # pane" before the poll loop sees the expect_regex.
    subprocess.run(
        ["tmux", "set-option", "-t", session, "remain-on-exit", "on"],
        capture_output=True,
    )

    poll_interval = 0.25
    settle_after_send = 0.15
    completed_turns = 0
    error_message = ""
    verdict = "PASS"
    final_pane = ""

    try:
        # Give the binary a brief moment to print any preamble / prompt.
        time.sleep(settle_after_send)
        with pane_log_path.open("a", encoding="utf-8") as log:
            log.write("=== preamble ===\n")
            log.write(_capture_pane(session))
            log.write("\n")

            for turn_idx, turn in enumerate(turns):
                _send_keys(session, turn.prompt)
                pattern = re.compile(turn.expect_regex, re.IGNORECASE)
                deadline = time.monotonic() + max(0.1, turn.timeout_s)
                matched = False
                last_pane = ""
                while time.monotonic() < deadline:
                    time.sleep(poll_interval)
                    last_pane = _capture_pane(session)
                    if pattern.search(last_pane):
                        matched = True
                        break

                log.write(
                    f"=== turn {turn_idx} prompt={turn.prompt!r} "
                    f"expect={turn.expect_regex!r} matched={matched} ===\n"
                )
                log.write(last_pane)
                log.write("\n")

                if not matched:
                    verdict = "FAIL"
                    error_message = (
                        f"turn {turn_idx}: expect_regex "
                        f"{turn.expect_regex!r} did not match within "
                        f"{turn.timeout_s}s (last prompt={turn.prompt!r})"
                    )
                    final_pane = last_pane
                    break
                completed_turns += 1
                # Brief settle between turns lets the binary actually
                # consume our keystrokes before the next pane capture.
                time.sleep(settle_after_send)
            else:
                final_pane = _capture_pane(session)
                log.write("=== final ===\n")
                log.write(final_pane)
                log.write("\n")
    finally:
        _kill_session(session)

    return ScenarioResult(
        verdict=verdict,
        binary_path=binary_abs,
        binary_version=binary_version,
        turn_count=completed_turns,
        pane_log_path=str(pane_log_path),
        error_message=error_message,
        pane_excerpt=final_pane[-2000:] if final_pane else "",
    )


__all__ = [
    "Turn",
    "ScenarioResult",
    "run_simulated_session",
]

"""termctrl-backed journey-footage recorder (additive sibling of runner.py).

This module is the *visual* counterpart to ``runner.py``'s tmux-driven
:func:`run_simulated_session`. Where the tmux runner is the validated,
assertion-grade capture path (and MUST NOT be modified), this recorder
reuses the exact same box-preparation sequence but swaps the tmux
spawn + expect loop for `terminal-control <https://github.com/kitlangton/terminal-control>`_
(``termctrl``). The output is an MP4 of the journey playing out inside a
real PTY, plus a per-run ``result.json`` / ``markers.json`` for the gallery.

Design contract:
  * SETUP is byte-for-byte the same intent as ``run_simulated_session``:
    preflight, optional initial-state overlay, box git-repo seeding,
    agent-HOME prep, opentraces hook install, env build. We import the
    runner's PRIVATE helpers verbatim rather than re-implementing them so
    the two paths can never drift.
  * Only the *drive* layer differs: ``termctrl start/send/show/mark/stop``
    + ``termctrl video`` instead of ``tmux new-session`` + ``capture-pane``.
  * Everything graceful-degrades to a ``SKIP`` verdict: no ``termctrl`` on
    PATH, no agent binary, or no ``ffmpeg`` for the video export. A SKIP
    never raises and never blocks default CI.
  * A turn miss is encoded as ``verdict="FAIL"`` + ``error_message`` — but
    the video of *what actually happened* is ALWAYS produced, because the
    whole point of footage is to see the failure.

Determinism note: footage is media, not an assertion artifact. The MP4 is
not byte-stable across runs/machines; ``result.json`` is stable-ish (it
records turn outcomes + marker names, not frame timing).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..drivers.base import Driver
from ..env import Box

# Reuse the tmux runner's private helpers verbatim — never re-implement the
# box-prep sequence. Importing the module (not the symbols) keeps the binding
# live so monkeypatches in tests target the same objects the runner uses.
from . import runner as _runner
from .runner import (
    Turn,
    _agent_name,
    _build_env_prefix,
    _detect_binary_version,
)

__all__ = [
    "FootageResult",
    "record_simulated_session",
]


# ---------------------------------------------------------------------------
# result dataclass — parallels ScenarioResult, adds footage-specific fields
# ---------------------------------------------------------------------------
@dataclass
class FootageTurnResult:
    """Per-turn outcome inside a footage recording."""

    index: int
    prompt: str
    expect_regex: str
    matched: bool
    timeout_s: float
    elapsed_s: float


@dataclass
class FootageResult:
    """Outcome of one termctrl-recorded session.

    Mirrors :class:`tests.otbox.simulated_users.runner.ScenarioResult`
    verdict semantics (``PASS`` / ``FAIL`` / ``SKIP``) and adds the
    footage-specific fields the gallery consumes.

    ``mp4_path`` / ``termctrl_path`` are absolute string paths (or ``""``
    when the recording was skipped before those artifacts existed).
    ``markers`` is the ordered list of marker names added during the run.
    """

    verdict: str
    binary_path: str
    binary_version: str
    agent: str | None
    scenario: str
    turn_count: int
    mp4_path: str
    termctrl_path: str
    markers: list[str] = field(default_factory=list)
    turns: list[FootageTurnResult] = field(default_factory=list)
    duration_s: float = 0.0
    cols: int = 110
    rows: int = 32
    fps: int = 20
    error_message: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# termctrl thin wrappers
# ---------------------------------------------------------------------------
def _tc(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    """Run a ``termctrl`` subcommand, capturing output (never raises)."""
    return subprocess.run(
        ["termctrl", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _tc_show(session: str) -> str:
    """Return the visible screen of ``session`` (or ``""`` on failure)."""
    try:
        proc = _tc("show", session, timeout=15.0)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout or ""


def _tc_mark(session: str, marker: str, markers: list[str]) -> None:
    """Add a navigable marker, recording its name for the result."""
    try:
        _tc("mark", session, marker, timeout=10.0)
    except (subprocess.TimeoutExpired, OSError):
        return
    markers.append(marker)


def _tc_send_prompt(session: str, prompt: str, *, pace_ms: int = 25) -> None:
    """Type ``prompt`` (paced for readability) then press enter."""
    try:
        _tc(
            "send",
            session,
            "--pace-ms",
            str(pace_ms),
            f"text:{prompt}",
            "enter",
            timeout=max(15.0, len(prompt) * (pace_ms / 1000.0) + 10.0),
        )
    except (subprocess.TimeoutExpired, OSError):
        return


def _tc_stop(session: str) -> None:
    """Best-effort session teardown."""
    try:
        _tc("stop", session, timeout=10.0)
    except (subprocess.TimeoutExpired, OSError):
        return


def _await_ready(
    session: str, *, budget_s: float, poll_s: float = 0.5
) -> None:
    """Poll ``termctrl show`` until the screen settles or budget elapses.

    "Settled" means two consecutive non-empty captures with identical
    content — enough signal that the agent's preamble/prompt has painted.
    Never raises; on timeout it simply returns and the turn loop proceeds.
    """
    deadline = time.monotonic() + max(0.1, budget_s)
    previous = ""
    stable_hits = 0
    while time.monotonic() < deadline:
        time.sleep(poll_s)
        screen = _tc_show(session)
        if screen.strip():
            if screen == previous:
                stable_hits += 1
                if stable_hits >= 2:
                    return
            else:
                stable_hits = 0
        previous = screen


def _session_name(box: Box, binary: str, scenario: str) -> str:
    """Derive a unique termctrl session name (mirrors the tmux scheme)."""
    import hashlib

    salt = f"{binary}|{scenario}".encode("utf-8")
    short = hashlib.sha256(salt).hexdigest()[:8]
    return f"otbox-footage-{box.box_id}-{short}"


# ---------------------------------------------------------------------------
# shared SETUP — mirrors run_simulated_session's preflight + box prep
# ---------------------------------------------------------------------------
def _prepare_box_for_agent(
    box: Box,
    binary_abs: str,
    binary_version: str,
    agent: str | None,
    env_extra: dict[str, str] | None,
) -> tuple[str | None, str | None, dict[str, str] | None]:
    """Run the runner's box-prep sequence for ``agent``.

    Returns ``(skip_message, fail_message, updated_env_extra)``:
      * ``skip_message`` set → caller returns a SKIP verdict.
      * ``fail_message`` set → caller returns a FAIL verdict.
      * both ``None`` → prep succeeded; ``updated_env_extra`` carries any
        agent-specific env additions (e.g. Pi's PATH pin).

    This intentionally re-uses the EXACT private helpers the tmux runner
    calls, in the same order, so the recorded box is shaped identically to
    a capture-refresh box. Only the drive layer differs.
    """
    normalized = _agent_name(agent)

    if normalized in {"codex-cli", "claude", "pi"}:
        repo_error = _runner._ensure_box_project_git_repo(box)
        if repo_error is not None:
            return None, repo_error, env_extra

    prep_error = _runner._prep_agent_home(Path(box.home), agent)
    if prep_error is not None:
        # Host operator has no onboarded agent state to copy → SKIP cleanly,
        # exactly as the tmux runner does.
        return prep_error, None, env_extra

    if normalized == "codex-cli":
        _runner._prep_codex_project_trust(Path(box.home), Path(box.project))
        hook_error = _runner._install_opentraces_hooks_in_box(box, agent)
        if hook_error is not None:
            return None, hook_error, env_extra

    elif normalized == "pi":
        hook_error = _runner._install_opentraces_hooks_in_box(box, agent)
        if hook_error is not None:
            return None, hook_error, env_extra
        env_extra = {
            **(env_extra or {}),
            "PATH": f"{Path(box.project) / '.testvenv' / 'bin'}:{os.environ.get('PATH', '')}",
        }

    elif normalized == "claude":
        _runner._prep_claude_project_trust(Path(box.home), Path(box.project))
        hook_error = _runner._install_opentraces_hooks_in_box(box, agent)
        if hook_error is not None:
            return None, hook_error, env_extra

    return None, None, env_extra


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------
def record_simulated_session(
    driver: Driver,
    box: Box,
    binary: str,
    turns: list[Turn],
    *,
    initial_state_dir: Path | None = None,
    output_dir: Path,
    footage_dir: Path,
    env_extra: dict[str, str] | None = None,
    agent: str | None = None,
    scenario: str = "session",
    cols: int = 110,
    rows: int = 32,
    fps: int = 20,
) -> FootageResult:
    """Record a termctrl session of ``binary`` driven through ``turns``.

    The signature is the footage analogue of
    :func:`run_simulated_session`: same ``driver`` / ``box`` / ``binary``
    / ``turns`` / ``initial_state_dir`` / ``output_dir`` / ``env_extra`` /
    ``agent`` contract, plus footage-specific keyword args.

    Parameters
    ----------
    footage_dir:
        Directory the ``.termctrl`` recording, ``<scenario>.mp4``,
        ``markers.json`` and ``result.json`` land in. Created if missing.
    scenario:
        Logical scenario name used for the recording/MP4 filenames and the
        gallery card title. Defaults to ``"session"``.
    cols / rows:
        PTY geometry for the recording.
    fps:
        Sampled frames-per-second for the exported MP4.

    Returns
    -------
    :class:`FootageResult`. The function never raises on a turn-level
    failure — a miss is encoded as ``verdict="FAIL"`` while the video is
    still produced. Setup failures (no termctrl, no binary, no ffmpeg)
    return cleanly as ``SKIP`` / ``FAIL``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    footage_dir.mkdir(parents=True, exist_ok=True)
    termctrl_path = footage_dir / f"{scenario}.termctrl"
    mp4_path = footage_dir / f"{scenario}.mp4"

    def _result(
        verdict: str,
        *,
        binary_path: str = binary,
        binary_version: str = "",
        turn_count: int = 0,
        turn_results: list[FootageTurnResult] | None = None,
        markers: list[str] | None = None,
        duration_s: float = 0.0,
        mp4: str = "",
        termctrl: str = "",
        error_message: str = "",
    ) -> FootageResult:
        return FootageResult(
            verdict=verdict,
            binary_path=binary_path,
            binary_version=binary_version,
            agent=agent,
            scenario=scenario,
            turn_count=turn_count,
            mp4_path=mp4,
            termctrl_path=termctrl,
            markers=markers or [],
            turns=turn_results or [],
            duration_s=round(duration_s, 3),
            cols=cols,
            rows=rows,
            fps=fps,
            error_message=error_message,
        )

    # --- preflight: termctrl + binary must resolve -------------------------
    if shutil.which("termctrl") is None:
        return _result(
            "SKIP",
            error_message=(
                "termctrl not installed; cargo install terminal-control"
            ),
        )

    resolved = shutil.which(binary) or (binary if Path(binary).is_file() else None)
    if resolved is None or not os.access(resolved, os.X_OK):
        return _result(
            "SKIP",
            error_message=f"binary not found or not executable: {binary}",
        )
    binary_abs = str(Path(resolved).resolve())

    # --- optional template overlay (same intent as the tmux runner) --------
    if initial_state_dir is not None:
        if not initial_state_dir.exists() or not initial_state_dir.is_dir():
            return _result(
                "FAIL",
                binary_path=binary_abs,
                error_message=(
                    "initial_state_dir does not exist or is not a directory: "
                    f"{initial_state_dir}"
                ),
            )
        try:
            _runner._copy_initial_state(initial_state_dir, Path(box.project))
        except OSError as exc:
            return _result(
                "FAIL",
                binary_path=binary_abs,
                error_message=f"failed to copy initial_state_dir: {exc}",
            )

    binary_version = _detect_binary_version(binary_abs)

    # --- agent-specific box prep (reuses runner helpers verbatim) ----------
    skip_msg, fail_msg, env_extra = _prepare_box_for_agent(
        box, binary_abs, binary_version, agent, env_extra
    )
    if skip_msg is not None:
        return _result(
            "SKIP", binary_path=binary_abs,
            binary_version=binary_version, error_message=skip_msg,
        )
    if fail_msg is not None:
        return _result(
            "FAIL", binary_path=binary_abs,
            binary_version=binary_version, error_message=fail_msg,
        )

    # --- build the spawn command: env <pinned> <binary> --------------------
    # termctrl has no --env flag, so the COMMAND itself is `env K=V ... bin`,
    # exactly the pattern the tmux runner uses (just handed to termctrl
    # instead of tmux new-session).
    env_prefix = _build_env_prefix(box, env_extra)
    session = _session_name(box, binary_abs, scenario)
    markers: list[str] = []
    turn_results: list[FootageTurnResult] = []
    verdict = "PASS"
    error_message = ""
    completed_turns = 0
    started = time.monotonic()

    # Per-agent readiness budget: real TUIs (claude/codex/pi) take longer to
    # paint their first prompt than the synthetic echo binary.
    normalized = _agent_name(agent)
    ready_budget = 4.0 if normalized in {"claude", "codex-cli", "pi"} else 2.5

    start_argv = [
        "start",
        session,
        "--record",
        str(termctrl_path),
        "--cwd",
        str(box.project),
        "--cols",
        str(cols),
        "--rows",
        str(rows),
        "--",
        *env_prefix,
        binary_abs,
    ]
    try:
        spawn = _tc(*start_argv, timeout=30.0)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return _result(
            "FAIL", binary_path=binary_abs, binary_version=binary_version,
            error_message=f"termctrl start failed: {exc}",
        )
    if spawn.returncode != 0:
        return _result(
            "FAIL", binary_path=binary_abs, binary_version=binary_version,
            error_message=(
                f"termctrl start failed (rc={spawn.returncode}): "
                f"{(spawn.stderr or spawn.stdout).strip()[:300]}"
            ),
        )

    try:
        _await_ready(session, budget_s=ready_budget)
        _tc_mark(session, "ready", markers)

        for turn_idx, turn in enumerate(turns):
            _tc_mark(session, f"turn-{turn_idx}-prompt", markers)
            _tc_send_prompt(session, turn.prompt)

            pattern = re.compile(turn.expect_regex, re.IGNORECASE)
            turn_started = time.monotonic()
            deadline = turn_started + max(0.1, turn.timeout_s)
            matched = False
            while time.monotonic() < deadline:
                time.sleep(0.5)
                screen = _tc_show(session)
                if pattern.search(screen):
                    matched = True
                    break

            elapsed = time.monotonic() - turn_started
            _tc_mark(session, f"turn-{turn_idx}-response", markers)
            turn_results.append(
                FootageTurnResult(
                    index=turn_idx,
                    prompt=turn.prompt,
                    expect_regex=turn.expect_regex,
                    matched=matched,
                    timeout_s=turn.timeout_s,
                    elapsed_s=round(elapsed, 3),
                )
            )

            if not matched:
                verdict = "FAIL"
                error_message = (
                    f"turn {turn_idx}: expect_regex {turn.expect_regex!r} "
                    f"did not match within {turn.timeout_s}s "
                    f"(prompt={turn.prompt!r})"
                )
                # Do NOT break — keep recording so the footage shows the
                # full session even past the failing turn? The tmux runner
                # stops at the first miss; we mirror that for parity but the
                # video already captured the failure leading up to here.
                break
            completed_turns += 1
    finally:
        _tc_mark(session, "stop", markers)
        _tc_stop(session)

    duration_s = time.monotonic() - started

    # --- export the MP4 (graceful-degrade if ffmpeg is absent) -------------
    mp4_out = ""
    if shutil.which("ffmpeg") is None:
        # No ffmpeg → recording exists but no video. Downgrade to SKIP only
        # if the run otherwise passed; a real FAIL stays FAIL.
        if verdict == "PASS":
            verdict = "SKIP"
            error_message = "ffmpeg not installed; cannot export MP4"
    elif termctrl_path.exists() and termctrl_path.stat().st_size > 0:
        video_argv = [
            "video",
            str(termctrl_path),
            "--out",
            str(mp4_path),
            "--footer",
            "--fps",
            str(fps),
            "--hide-cursor",
        ]
        try:
            video = _tc(*video_argv, timeout=180.0)
            if video.returncode == 0 and mp4_path.exists():
                mp4_out = str(mp4_path)
            elif verdict == "PASS":
                verdict = "FAIL"
                error_message = (
                    f"termctrl video export failed (rc={video.returncode}): "
                    f"{(video.stderr or video.stdout).strip()[:300]}"
                )
        except (subprocess.TimeoutExpired, OSError) as exc:
            if verdict == "PASS":
                verdict = "FAIL"
                error_message = f"termctrl video export errored: {exc}"

    result = _result(
        verdict,
        binary_path=binary_abs,
        binary_version=binary_version,
        turn_count=completed_turns,
        turn_results=turn_results,
        markers=markers,
        duration_s=duration_s,
        mp4=mp4_out,
        termctrl=str(termctrl_path) if termctrl_path.exists() else "",
        error_message=error_message,
    )

    # --- persist markers.json + result.json --------------------------------
    try:
        (footage_dir / "markers.json").write_text(
            json.dumps(markers, indent=2) + "\n", encoding="utf-8"
        )
        (footage_dir / "result.json").write_text(
            json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass

    return result

"""Single termctrl drive core for the otbox simulated-user lane.

Stage 2 of the unify-on-terminal-control refactor extracts the termctrl
drive core out of ``footage_runner.py`` into this neutral module so that
BOTH entry points can share ONE drive loop:

  * ``footage_runner.record_simulated_session`` is a thin wrapper today
    (it projects :class:`SessionResult` 1:1 into ``FootageResult``).
  * ``runner.run_simulated_session`` will flip onto this core in Stage 3
    (it imports ``drive`` then; keeping the box-prep helpers in the
    neutral ``prep`` module is what avoids a runner↔drive import cycle).

The drive core ports ``record_simulated_session``'s behavior verbatim:
preflight termctrl/binary, box prep via :func:`_prepare_box_for_agent`,
optional initial-state overlay, ``termctrl start --record`` spawn, await
ready, codex hook dismissal, the instant-paste turn loop with the
accumulate-frames+logs match, stop, then export the MP4 only when
``export_mp4=True``.

This module imports ``prep`` and ``.scenario`` (Turn) and the standard
library only — never ``runner`` or ``footage_runner``.
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
from . import prep
from .prep import (
    _agent_name,
    _build_env_prefix,
    _detect_binary_version,
)
from .scenario import Turn

__all__ = [
    "FootageTurnResult",
    "SessionResult",
    "drive_session",
]


# ---------------------------------------------------------------------------
# per-turn result shape (unchanged from footage_runner)
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


# ---------------------------------------------------------------------------
# unified result — carries BOTH the assertion lane and the footage lane
# ---------------------------------------------------------------------------
@dataclass
class SessionResult:
    """Outcome of one termctrl-driven session.

    Carries both field families so the two thin wrappers can project it
    1:1 into their legacy shapes:

      * assertion lane (``runner.ScenarioResult``):
        ``verdict`` / ``binary_path`` / ``binary_version`` /
        ``turn_count`` / ``error_message`` / ``pane_log_path`` /
        ``pane_excerpt``.
      * footage lane (``footage_runner.FootageResult``):
        ``agent`` / ``scenario`` / ``termctrl_path`` / ``mp4_path`` /
        ``markers`` / ``turns`` / ``duration_s`` / ``cols`` / ``rows`` /
        ``fps``.

    Verdict semantics mirror ``ScenarioResult``/``FootageResult``:
    ``PASS`` (every turn matched), ``FAIL`` (a turn missed or an export
    error), ``SKIP`` (abandoned before any turn — missing termctrl /
    binary / ffmpeg).
    """

    verdict: str
    binary_path: str
    binary_version: str
    agent: str | None
    scenario: str
    turn_count: int
    error_message: str = ""
    # The verdict from the TURN LOOP alone — i.e. did the agent run pass —
    # BEFORE the (footage-only) MP4 export can mutate ``verdict``. The trace
    # capture lane (capture-refresh) keys its PASS/FAIL on this so a footage
    # export hiccup (missing ffmpeg / video timeout) never sinks a real
    # capture. Mirrors ``verdict`` exactly on the non-export path.
    turn_verdict: str = ""
    # assertion lane
    pane_log_path: str = ""
    pane_excerpt: str = field(default="", repr=False)
    # footage lane
    termctrl_path: str = ""
    mp4_path: str = ""
    markers: list[str] = field(default_factory=list)
    turns: list[FootageTurnResult] = field(default_factory=list)
    duration_s: float = 0.0
    cols: int = 110
    rows: int = 32
    fps: int = 20


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


def _tc_logs(session: str) -> str:
    """Return retained scrollback for ``session`` (or ``""`` on failure).

    ``termctrl logs`` keeps readable terminal output that has scrolled past
    the visible viewport, used alongside accumulated ``show`` frames so a
    completion line that scrolled off is still matchable.
    """
    try:
        proc = _tc("logs", session, timeout=15.0)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def _tc_key(session: str, *tokens: str) -> None:
    """Send raw key tokens (e.g. ``enter``, ``down``, ``text:t``) to the session."""
    try:
        _tc("send", session, *tokens, timeout=10.0)
    except (subprocess.TimeoutExpired, OSError):
        return


def _dismiss_codex_hooks(session: str, *, timeout_s: float = 12.0) -> bool:
    """Trust the box-local Codex hooks before the scenario prompt is sent.

    Codex shows a "hooks need review" interstitial the first time a HOME sees
    new lifecycle hooks. The footage box installs opentraces hooks into a fresh
    HOME, so the recorder must clear that modal ("Press t to trust all") or the
    first prompt is swallowed and the journey stalls on the trust dialog for the
    whole timeout. Mirrors the tmux runner's ``_dismiss_codex_hook_review``.
    """
    deadline = time.monotonic() + max(0.1, timeout_s)
    while time.monotonic() < deadline:
        lower = _tc_show(session).lower()
        if "hooks need review" not in lower and "hooks are new or changed" not in lower:
            time.sleep(0.3)
            continue
        if "press t to trust all" in lower:
            _tc_key(session, "text:t")
        elif "trust all and continue" in lower:
            _tc_key(session, "down")
            _tc_key(session, "enter")
        else:
            _tc_key(session, "text:t")
        time.sleep(1.0)
        after = _tc_show(session).lower()
        if "hooks need review" in after or "hooks are new or changed" in after:
            _tc_key(session, "text:t")
            time.sleep(1.0)
        return True
    return False


def _dismiss_claude_startup_dialogs(session: str, *, timeout_s: float = 10.0) -> bool:
    """Clear Claude Code's blocking startup dialogs before turn 0.

    Claude Code >= 2.1.17x interposes trust dialogs at REPL startup when the
    project carries config it has not seen under this HOME — observed live
    with "New MCP server found in this project" (the box project ships a
    codex MCP entry). The dialog swallows anything typed into it, so the
    first prompt vanishes and the turn times out (the claude-linear-edit
    capture FAIL that surfaced this). Confirm the highlighted default —
    what a real user pressing Enter does — until no confirm-dialog
    signature remains. Sibling of ``_dismiss_codex_hooks``.
    """
    dismissed = False
    deadline = time.monotonic() + max(0.1, timeout_s)
    while time.monotonic() < deadline:
        lower = _tc_show(session).lower()
        if "enter to confirm" in lower or "new mcp server found" in lower:
            _tc_key(session, "enter")
            dismissed = True
            time.sleep(1.5)
            continue
        if dismissed:
            return True
        time.sleep(0.3)
    return dismissed


def _tc_mark(session: str, marker: str, markers: list[str]) -> None:
    """Add a navigable marker, recording its name for the result."""
    try:
        _tc("mark", session, marker, timeout=10.0)
    except (subprocess.TimeoutExpired, OSError):
        return
    markers.append(marker)


def _chunk_text(text: str, limit: int = 140) -> list[str]:
    """Split ``text`` into <=limit-char chunks, preferring a space boundary."""
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind(" ", 0, limit + 1)
        cut = cut + 1 if cut > 0 else limit
        chunks.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        chunks.append(rest)
    return chunks


def _tc_send_prompt(session: str, prompt: str) -> None:
    """Paste ``prompt`` fast (un-paced) then press enter.

    Do NOT pace the keystrokes. termctrl ``--pace-ms`` typing is throttled by
    the agent's per-keystroke TUI re-render to ~7 chars/s, so a >170-char prompt
    takes >25s to type and codex's startup idle-timeout kills the session before
    the prompt is ever submitted — the dominant footage-failure mode (the
    recording ends mid-typing, then the poll loop spins on a dead session to the
    timeout). Paste in <=140-char chunks instantly so submit lands within ~1s.
    """
    try:
        for chunk in _chunk_text(prompt, limit=140):
            _tc("send", session, f"text:{chunk}", timeout=20.0)
            time.sleep(0.1)
        _tc("send", session, "enter", timeout=10.0)
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
    """Run the box-prep sequence for ``agent``.

    Returns ``(skip_message, fail_message, updated_env_extra)``:
      * ``skip_message`` set → caller returns a SKIP verdict.
      * ``fail_message`` set → caller returns a FAIL verdict.
      * both ``None`` → prep succeeded; ``updated_env_extra`` carries any
        agent-specific env additions (e.g. Pi's PATH pin).

    This intentionally re-uses the EXACT private helpers the tmux runner
    calls, in the same order, so the recorded box is shaped identically to
    a capture-refresh box. Only the drive layer differs. Helpers are
    referenced through the ``prep`` module so test monkeypatches that
    target ``runner.<name>`` (which re-exports the same objects) stay in
    effect on this path too.
    """
    normalized = _agent_name(agent)

    if normalized in {"codex-cli", "claude", "pi"}:
        repo_error = prep._ensure_box_project_git_repo(box)
        if repo_error is not None:
            return None, repo_error, env_extra

    prep_error = prep._prep_agent_home(Path(box.home), agent)
    if prep_error is not None:
        # Host operator has no onboarded agent state to copy → SKIP cleanly,
        # exactly as the tmux runner does.
        return prep_error, None, env_extra

    if normalized == "codex-cli":
        prep._prep_codex_project_trust(Path(box.home), Path(box.project))
        hook_error = prep._install_opentraces_hooks_in_box(box, agent)
        if hook_error is not None:
            return None, hook_error, env_extra

    elif normalized == "pi":
        hook_error = prep._install_opentraces_hooks_in_box(box, agent)
        if hook_error is not None:
            return None, hook_error, env_extra
        index_error = prep._rebuild_trace_search_snapshot_in_box(box)
        if index_error is not None:
            return None, index_error, env_extra
        env_extra = {
            **(env_extra or {}),
            "PATH": f"{Path(box.project) / '.testvenv' / 'bin'}:{os.environ.get('PATH', '')}",
        }

    elif normalized == "claude":
        prep._prep_claude_project_trust(Path(box.home), Path(box.project))
        hook_error = prep._install_opentraces_hooks_in_box(box, agent)
        if hook_error is not None:
            return None, hook_error, env_extra

    return None, None, env_extra


# ---------------------------------------------------------------------------
# the single drive core
# ---------------------------------------------------------------------------
def drive_session(
    driver: Driver,
    box: Box,
    binary: str,
    turns: list[Turn],
    *,
    output_dir: Path,
    agent: str | None = None,
    initial_state_dir: Path | None = None,
    env_extra: dict[str, str] | None = None,
    scenario: str = "session",
    record_dir: Path | None = None,
    export_mp4: bool = False,
    cols: int = 110,
    rows: int = 32,
    fps: int = 20,
) -> SessionResult:
    """Drive an interactive termctrl session of ``binary`` through ``turns``.

    Behavior is exactly what ``footage_runner.record_simulated_session``
    does today: preflight termctrl/binary, box prep via
    :func:`_prepare_box_for_agent`, optional initial-state overlay, spawn
    ``termctrl start --record``, await ready, codex hook dismissal, the
    instant-paste turn loop with the accumulate-frames+logs match, stop,
    then export the MP4 only when ``export_mp4=True``.

    Parameters
    ----------
    output_dir:
        Where the forensic ``pane.log`` lands (assertion lane). Created
        if missing. When ``record_dir`` is ``None`` it also becomes the
        default home for the ``.termctrl`` recording + ``.mp4``.
    record_dir:
        Directory the ``.termctrl`` recording and ``<scenario>.mp4`` land
        in. Defaults to ``output_dir`` when ``None``.
    export_mp4:
        When ``True``, export the recording to ``<scenario>.mp4`` after the
        drive (the footage path). When ``False`` the drive records the
        ``.termctrl`` byproduct but skips the (expensive) video export.
    scenario:
        Logical scenario name used for the recording/MP4 filenames.
    cols / rows:
        PTY geometry for the recording.
    fps:
        Sampled frames-per-second for the exported MP4.

    Returns
    -------
    :class:`SessionResult`. Never raises on a turn-level failure — a miss
    is encoded as ``verdict="FAIL"`` while the recording is still produced.
    Setup failures (no termctrl, no binary, no ffmpeg) return cleanly as
    ``SKIP`` / ``FAIL``.
    """
    if record_dir is None:
        record_dir = output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    record_dir.mkdir(parents=True, exist_ok=True)
    pane_log_path = output_dir / "pane.log"
    # Truncate any prior log so a re-run starts clean.
    pane_log_path.write_text("", encoding="utf-8")
    termctrl_path = record_dir / f"{scenario}.termctrl"
    mp4_path = record_dir / f"{scenario}.mp4"

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
        pane_excerpt: str = "",
        turn_verdict: str | None = None,
    ) -> SessionResult:
        return SessionResult(
            verdict=verdict,
            binary_path=binary_path,
            binary_version=binary_version,
            agent=agent,
            scenario=scenario,
            turn_count=turn_count,
            error_message=error_message,
            # Defaults to ``verdict`` for the early-return paths (preflight
            # SKIP/FAIL never reached the turn loop, so the run-level and
            # turn-level verdicts coincide). The final result passes the
            # pre-export turn verdict explicitly.
            turn_verdict=turn_verdict if turn_verdict is not None else verdict,
            pane_log_path=str(pane_log_path),
            pane_excerpt=pane_excerpt,
            termctrl_path=termctrl,
            mp4_path=mp4,
            markers=markers or [],
            turns=turn_results or [],
            duration_s=round(duration_s, 3),
            cols=cols,
            rows=rows,
            fps=fps,
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
            prep._copy_initial_state(initial_state_dir, Path(box.project))
        except OSError as exc:
            return _result(
                "FAIL",
                binary_path=binary_abs,
                error_message=f"failed to copy initial_state_dir: {exc}",
            )

    binary_version = _detect_binary_version(binary_abs)

    # --- agent-specific box prep (reuses prep helpers verbatim) ------------
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

    last_frame = ""
    with pane_log_path.open("a", encoding="utf-8") as log:
        try:
            _await_ready(session, budget_s=ready_budget)
            _tc_mark(session, "ready", markers)
            log.write("=== ready ===\n")
            log.write(_tc_show(session))
            log.write("\n")

            # Codex shows a hook-review trust modal the first time a fresh HOME sees
            # the box's opentraces hooks; clear it ("Press t to trust all") or the
            # first prompt is swallowed and the journey stalls on the dialog.
            if normalized == "codex-cli":
                if _dismiss_codex_hooks(session):
                    _tc_mark(session, "codex-hooks-trusted", markers)

            # Claude shows blocking trust dialogs (MCP server discovery) at
            # REPL startup on a fresh HOME; clear them or turn 0 is swallowed.
            if normalized == "claude":
                if _dismiss_claude_startup_dialogs(session):
                    _tc_mark(session, "claude-dialogs-dismissed", markers)

            for turn_idx, turn in enumerate(turns):
                _tc_mark(session, f"turn-{turn_idx}-prompt", markers)
                _tc_send_prompt(session, turn.prompt)

                pattern = re.compile(turn.expect_regex, re.IGNORECASE)
                turn_started = time.monotonic()
                deadline = turn_started + max(0.1, turn.timeout_s)
                matched = False
                # Accumulate distinct visible-screen frames (+ scrollback) and
                # match the expect_regex against the WHOLE transcript. Full-screen
                # TUIs (codex/claude/pi) re-render and scroll the completion line
                # off the visible screen between polls; matching only the latest
                # `show` snapshot misses it and the turn FAILs after the timeout
                # even though the agent finished (the codex-* footage failures).
                # Poll faster (0.3s) and retain every distinct frame so transient
                # completion text is still detected.
                seen_frames: list[str] = []
                last_approval_frame = ""
                while time.monotonic() < deadline:
                    time.sleep(0.3)
                    screen = _tc_show(session)
                    if screen and screen != last_frame:
                        seen_frames.append(screen)
                        last_frame = screen
                    # Mid-turn permission prompts (claude >= 2.1.x asks per
                    # edit/command on a fresh HOME: "Do you want to make this
                    # edit ...?" / "Do you want to run ...?"). The simulated
                    # user approves the highlighted default, as a real
                    # approving user does. Only re-approve when the dialog
                    # frame CHANGED, so a slow repaint doesn't double-Enter.
                    lower = screen.lower()
                    if (
                        "do you want to" in lower
                        and "1. yes" in lower
                        and screen != last_approval_frame
                    ):
                        _tc_key(session, "enter")
                        _tc_mark(session, f"turn-{turn_idx}-approval", markers)
                        last_approval_frame = screen
                        continue
                    haystack = "\n".join(seen_frames)
                    logs = _tc_logs(session)
                    if logs:
                        haystack = f"{haystack}\n{logs}"
                    if pattern.search(haystack):
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
                log.write(
                    f"=== turn {turn_idx} prompt={turn.prompt!r} "
                    f"expect={turn.expect_regex!r} matched={matched} ===\n"
                )
                log.write("\n".join(seen_frames))
                log.write("\n")

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
    pane_excerpt = last_frame[-2000:] if last_frame else ""

    # Freeze the turn-loop verdict BEFORE the footage export can mutate it,
    # so the trace-capture lane (capture-refresh) decides PASS/FAIL on the
    # agent run alone and a footage hiccup never sinks a real capture.
    turn_verdict = verdict

    # --- export the MP4 (graceful-degrade if ffmpeg is absent) -------------
    # Only the footage path requests an export; the capture path records the
    # `.termctrl` byproduct but skips the (expensive) video render.
    mp4_out = ""
    if export_mp4:
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
                video = _tc(*video_argv, timeout=max(360.0, duration_s * 4.0))
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

    session_result = _result(
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
        pane_excerpt=pane_excerpt,
        turn_verdict=turn_verdict,
    )

    # --- write the gallery metadata (single writer) ------------------------
    # When this is a footage drive (export_mp4=True), drive_session is the
    # SINGLE writer of the gallery-ready `result.json` + `markers.json` so
    # BOTH the footage path AND capture-refresh emit identical metadata next
    # to the MP4. `footage.py` reads `result.json` from here and writes
    # `scenario.json` itself; `footage_runner` no longer double-writes these.
    # Never raise — footage media is best-effort, independent of the drive.
    if export_mp4:
        try:
            (record_dir / "result.json").write_text(
                json.dumps(asdict(session_result), indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            (record_dir / "markers.json").write_text(
                json.dumps(session_result.markers, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    return session_result

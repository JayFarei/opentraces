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

# Box-prep helpers live in the neutral `prep` module (Stage 2 of the
# unify-on-terminal-control refactor). They are re-exported here so that
# (a) in-module uses below keep working unchanged, (b) external references
# like `footage_runner._runner._prep_agent_home` resolve, and (c) the test
# monkeypatches that target `tests.otbox.simulated_users.runner.<name>`
# rebind the exact names `run_simulated_session` calls.
from .prep import (  # noqa: F401 - re-exported for back-compat
    _CLAUDE_AGENTS,
    _CODEX_AGENTS,
    _PI_AGENTS,
    _agent_name,
    _build_env_prefix,
    _copy_initial_state,
    _detect_binary_version,
    _ensure_box_project_git_repo,
    _install_opentraces_hooks_in_box,
    _prep_agent_home,
    _prep_claude_project_trust,
    _prep_codex_project_trust,
    _strip_codex_project_tables,
    _toml_quote,
)


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
def _session_name(box: Box, binary: str, turn_count: int) -> str:
    """Derive a unique tmux session name for this run.

    Keyed by ``box_id`` + a short hash of (binary path, turn count) so
    two scenarios against the same box do not collide on a session
    name even if they happen to run concurrently in tests.
    """
    salt = f"{binary}|{turn_count}".encode("utf-8")
    short = hashlib.sha256(salt).hexdigest()[:8]
    return f"otbox-sim-{box.box_id}-{short}"


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
    time.sleep(0.25)
    subprocess.run(
        ["tmux", "send-keys", "-t", session, "Enter"],
        capture_output=True,
    )


def _send_tmux_key(session: str, key: str) -> None:
    """Send one tmux key token without adding Enter."""
    subprocess.run(
        ["tmux", "send-keys", "-t", session, key],
        capture_output=True,
    )


def _dismiss_codex_hook_review(session: str, *, timeout_s: float = 8.0) -> bool:
    """Trust the box-local Codex hooks before scenario prompts are sent.

    Codex v0.132 shows a hook-review interstitial the first time a HOME
    sees new lifecycle hooks. capture-refresh deliberately installs
    opentraces hooks into a fresh box HOME, so the runner must clear that
    screen or the first scenario prompt is swallowed by the review UI.
    """
    deadline = time.monotonic() + max(0.1, timeout_s)
    while time.monotonic() < deadline:
        pane = _capture_pane(session)
        lower = pane.lower()
        if "hooks need review" not in lower and "hooks are new or changed" not in lower:
            time.sleep(0.25)
            continue

        if "press t to trust all" in lower:
            _send_tmux_key(session, "t")
        elif "trust all and continue" in lower:
            _send_tmux_key(session, "Down")
            _send_tmux_key(session, "Enter")
        else:
            _send_tmux_key(session, "t")

        time.sleep(1.0)
        pane_after = _capture_pane(session).lower()
        if "hooks need review" in pane_after or "hooks are new or changed" in pane_after:
            if "press t to trust all" in pane_after:
                _send_tmux_key(session, "t")
            elif "trust all and continue" in pane_after:
                _send_tmux_key(session, "Down")
                _send_tmux_key(session, "Enter")
            else:
                _send_tmux_key(session, "t")
            time.sleep(1.0)
        return True
    return False


def _handle_codex_rate_limit_prompt(session: str, pane: str) -> bool:
    """Accept Codex CLI's lower-model fallback prompt when it appears.

    The live capture lane should keep moving when Codex offers an explicit
    fallback from the operator's default model to a cheaper model. We only
    handle the first-choice fallback prompt; hard usage-limit failures with no
    switch option still time out and preserve the pane log for inspection.
    """
    lower = pane.lower()
    if "usage limit" not in lower and "approaching rate limits" not in lower:
        return False
    if "switch to gpt-5.4-mini" not in lower:
        return False

    _send_tmux_key(session, "Enter")
    time.sleep(1.0)
    return True


def _kill_session(session: str) -> None:
    """Best-effort tmux session cleanup."""
    subprocess.run(
        ["tmux", "kill-session", "-t", session],
        capture_output=True,
    )


def _run_claude_print_turns(
    binary: str,
    project: Path,
    env: dict[str, str],
    turns: list[Turn],
    pane_log_path: Path,
) -> tuple[str, int, str, str]:
    """Drive ``claude --print`` turns in subprocess mode (no tmux).

    Returns ``(verdict, completed_turns, error_message, final_output)``.

    Why not tmux for real claude:
      * ``claude``'s interactive TUI uses tmux's alternate-screen
        buffer during boot, which ``tmux capture-pane -p`` reads as
        empty for the first 30-60s. The PTY runner's poll loop sees
        no content, sends keystrokes blindly, and the agent never
        actually receives the prompt.
      * ``claude``'s interactive mode triggers MCP-trust prompts on
        any ``.mcp.json`` found in cwd ancestry — the runner cannot
        dismiss those.
      * ``claude --print`` (documented in ``claude --help``) is the
        non-interactive contract that skips the workspace-trust
        dialog AND fires the same PreToolUse/PostToolUse/Stop hooks
        as interactive mode (verified empirically — see capture
        pipeline test fixtures).
      * ``claude --continue`` resumes the most recent conversation
        in the cwd, so multi-turn scenarios compose as
        ``--print <turn0>`` then ``--print --continue <turnN>``.
    """
    completed = 0
    last_stdout = ""
    last_stderr = ""
    with pane_log_path.open("a", encoding="utf-8") as log:
        log.write("=== claude --print mode (no tmux) ===\n")
        for turn_idx, turn in enumerate(turns):
            cmd = [
                binary,
                "--print",
                "--strict-mcp-config",
                "--permission-mode", "bypassPermissions",
            ]
            if turn_idx > 0:
                cmd.append("--continue")
            cmd.append(turn.prompt)
            log.write(
                f"=== turn {turn_idx} cmd={cmd!r} expect={turn.expect_regex!r} "
                f"timeout={turn.timeout_s}s ===\n"
            )
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(project),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=max(1.0, turn.timeout_s),
                    stdin=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired as exc:
                last_stdout = (exc.stdout.decode("utf-8", "replace")
                               if isinstance(exc.stdout, (bytes, bytearray))
                               else (exc.stdout or ""))
                last_stderr = (exc.stderr.decode("utf-8", "replace")
                               if isinstance(exc.stderr, (bytes, bytearray))
                               else (exc.stderr or ""))
                log.write(f"--- TIMEOUT after {turn.timeout_s}s ---\n")
                log.write(f"--- partial stdout ---\n{last_stdout}\n")
                log.write(f"--- partial stderr ---\n{last_stderr}\n")
                return (
                    "FAIL",
                    completed,
                    f"turn {turn_idx}: timed out after {turn.timeout_s}s",
                    last_stdout,
                )
            last_stdout = proc.stdout or ""
            last_stderr = proc.stderr or ""
            log.write(f"--- stdout (rc={proc.returncode}) ---\n{last_stdout}\n")
            if last_stderr:
                log.write(f"--- stderr ---\n{last_stderr}\n")
            if proc.returncode != 0:
                return (
                    "FAIL",
                    completed,
                    (
                        f"turn {turn_idx}: claude --print exited rc={proc.returncode}: "
                        f"{last_stderr.strip()[:200]}"
                    ),
                    last_stdout,
                )
            pattern = re.compile(turn.expect_regex, re.IGNORECASE)
            if not pattern.search(last_stdout):
                return (
                    "FAIL",
                    completed,
                    (
                        f"turn {turn_idx}: expect_regex "
                        f"{turn.expect_regex!r} did not match claude --print "
                        f"stdout (prompt={turn.prompt!r})"
                    ),
                    last_stdout,
                )
            completed += 1
    return "PASS", completed, "", last_stdout


def _run_pi_print_turns(
    binary: str,
    project: Path,
    env: dict[str, str],
    turns: list[Turn],
    pane_log_path: Path,
) -> tuple[str, int, str, str]:
    """Drive ``pi --print`` turns in subprocess mode (no tmux).

    Returns ``(verdict, completed_turns, error_message, final_output)``.

    Why not tmux for real pi:
      * pi's interactive TUI boots slowly (skill/extension load + a
        "setup step(s) missing" nag). The tmux poll loop sends the
        prompt after a 0.15s settle, before the TUI is ready to accept
        input, so the typed prompt is never submitted (observed: prompt
        in the input box, ``$0.000`` spent, ``0.0%`` context).
      * ``pi --print`` (documented in ``pi --help``: "Non-interactive
        mode: process prompt and exit") runs the prompt headlessly with
        the same read/bash/edit/write tools and the loaded opentraces-pi
        extension, so the bridge sidecars + session JSONL are produced
        exactly as in interactive mode.
      * ``pi --continue`` resumes the most recent session in the cwd, so
        multi-turn scenarios compose as ``--print <turn0>`` then
        ``--print --continue <turnN>``.
    """
    completed = 0
    last_stdout = ""
    last_stderr = ""
    with pane_log_path.open("a", encoding="utf-8") as log:
        log.write("=== pi --print mode (no tmux) ===\n")
        for turn_idx, turn in enumerate(turns):
            cmd = [binary, "--print"]
            if turn_idx > 0:
                cmd.append("--continue")
            cmd.append(turn.prompt)
            log.write(
                f"=== turn {turn_idx} cmd={cmd!r} expect={turn.expect_regex!r} "
                f"timeout={turn.timeout_s}s ===\n"
            )
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(project),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=max(1.0, turn.timeout_s),
                    stdin=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired as exc:
                last_stdout = (exc.stdout.decode("utf-8", "replace")
                               if isinstance(exc.stdout, (bytes, bytearray))
                               else (exc.stdout or ""))
                last_stderr = (exc.stderr.decode("utf-8", "replace")
                               if isinstance(exc.stderr, (bytes, bytearray))
                               else (exc.stderr or ""))
                log.write(f"--- TIMEOUT after {turn.timeout_s}s ---\n")
                log.write(f"--- partial stdout ---\n{last_stdout}\n")
                log.write(f"--- partial stderr ---\n{last_stderr}\n")
                return (
                    "FAIL",
                    completed,
                    f"turn {turn_idx}: pi --print timed out after {turn.timeout_s}s",
                    last_stdout,
                )
            last_stdout = proc.stdout or ""
            last_stderr = proc.stderr or ""
            log.write(f"--- stdout (rc={proc.returncode}) ---\n{last_stdout}\n")
            if last_stderr:
                log.write(f"--- stderr ---\n{last_stderr}\n")
            if proc.returncode != 0:
                return (
                    "FAIL",
                    completed,
                    (
                        f"turn {turn_idx}: pi --print exited rc={proc.returncode}: "
                        f"{last_stderr.strip()[:200]}"
                    ),
                    last_stdout,
                )
            pattern = re.compile(turn.expect_regex, re.IGNORECASE)
            if not pattern.search(last_stdout):
                return (
                    "FAIL",
                    completed,
                    (
                        f"turn {turn_idx}: expect_regex "
                        f"{turn.expect_regex!r} did not match pi --print "
                        f"stdout (prompt={turn.prompt!r})"
                    ),
                    last_stdout,
                )
            completed += 1
    return "PASS", completed, "", last_stdout


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
    agent: str | None = None,
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
    agent:
        The scenario's declared agent name (``"claude"`` / ``"codex"``
        / ``"hermes"`` / ``"echo"``). Drives per-agent HOME prep before
        the spawn — without this, a real ``claude`` binary launched
        into a virgin HOME shows first-run UX (theme picker, login)
        the PTY runner cannot dismiss. When the host operator has no
        onboarded state to copy, the runner SKIPs cleanly.

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

    normalized_agent = _agent_name(agent)

    if normalized_agent in {"codex-cli", "claude", "pi"}:
        repo_error = _ensure_box_project_git_repo(box)
        if repo_error is not None:
            return ScenarioResult(
                verdict="FAIL",
                binary_path=binary_abs,
                binary_version=binary_version,
                turn_count=0,
                pane_log_path=str(pane_log_path),
                error_message=repo_error,
            )

    # --- seed isolated HOME with agent onboarding state --------------------
    prep_error = _prep_agent_home(Path(box.home), agent)
    if prep_error is not None:
        return ScenarioResult(
            verdict="SKIP",
            binary_path=binary_abs,
            binary_version=binary_version,
            turn_count=0,
            pane_log_path=str(pane_log_path),
            error_message=prep_error,
        )

    if normalized_agent == "codex-cli":
        _prep_codex_project_trust(Path(box.home), Path(box.project))
        hook_install_error = _install_opentraces_hooks_in_box(box, agent)
        if hook_install_error is not None:
            return ScenarioResult(
                verdict="FAIL",
                binary_path=binary_abs,
                binary_version=binary_version,
                turn_count=0,
                pane_log_path=str(pane_log_path),
                error_message=hook_install_error,
            )

    if normalized_agent == "pi":
        hook_install_error = _install_opentraces_hooks_in_box(box, agent)
        if hook_install_error is not None:
            return ScenarioResult(
                verdict="FAIL",
                binary_path=binary_abs,
                binary_version=binary_version,
                turn_count=0,
                pane_log_path=str(pane_log_path),
                error_message=hook_install_error,
            )
        # Pi extension tools shell out to `opentraces`; put the box-local
        # editable CLI first on PATH for TUI slash-command/tool smoke tests.
        env_extra = {
            **(env_extra or {}),
            "PATH": f"{Path(box.project) / '.testvenv' / 'bin'}:{os.environ.get('PATH', '')}",
        }

    # --- claude: --print headless mode (no tmux, no MCP prompts) -----------
    # ``claude``'s interactive TUI uses tmux's alternate-screen buffer and
    # blocks on MCP-server trust prompts; neither is dismissable from the
    # tmux capture-pane poll loop. ``claude --print`` is the documented
    # non-interactive surface, fires the same opentraces hooks as
    # interactive mode, and composes for multi-turn via --continue. So
    # for claude we skip tmux entirely. Other agents (echo, codex,
    # hermes) keep the original PTY/tmux path below.
    if normalized_agent == "claude":
        # Pre-ack MCP trust for the box's project so even if the runner
        # ever needs to drop back to interactive mode the prompt is
        # already silenced. Also short-circuits the cwd-ancestry .mcp.json
        # walk that picks up the host operator's ~/.mcp.json.
        _prep_claude_project_trust(Path(box.home), Path(box.project))
        hook_install_error = _install_opentraces_hooks_in_box(box, agent)
        if hook_install_error is not None:
            return ScenarioResult(
                verdict="FAIL",
                binary_path=binary_abs,
                binary_version=binary_version,
                turn_count=0,
                pane_log_path=str(pane_log_path),
                error_message=hook_install_error,
            )
        env = isolated_env(box, env_extra)
        verdict, completed_turns, error_message, final_out = _run_claude_print_turns(
            binary=binary_abs,
            project=Path(box.project),
            env=env,
            turns=turns,
            pane_log_path=pane_log_path,
        )
        return ScenarioResult(
            verdict=verdict,
            binary_path=binary_abs,
            binary_version=binary_version,
            turn_count=completed_turns,
            pane_log_path=str(pane_log_path),
            error_message=error_message,
            pane_excerpt=final_out[-2000:] if final_out else "",
        )

    # --- pi: --print headless mode (no tmux) -------------------------------
    # pi's interactive TUI boots slowly (skill/extension load + a setup-step
    # nag); the tmux poll loop sends the prompt before the TUI accepts input,
    # so the Enter never submits (observed: prompt typed, $0.000 spent, 0.0%
    # context). ``pi --print`` runs the prompt headlessly with the same
    # read/bash/edit/write tools + loaded opentraces-pi extension, producing
    # the bridge sidecars + session JSONL exactly as interactive mode. Box
    # prep (hooks + box-local opentraces on PATH) already ran above.
    if normalized_agent == "pi":
        env = isolated_env(box, env_extra)
        verdict, completed_turns, error_message, final_out = _run_pi_print_turns(
            binary=binary_abs,
            project=Path(box.project),
            env=env,
            turns=turns,
            pane_log_path=pane_log_path,
        )
        return ScenarioResult(
            verdict=verdict,
            binary_path=binary_abs,
            binary_version=binary_version,
            turn_count=completed_turns,
            pane_log_path=str(pane_log_path),
            error_message=error_message,
            pane_excerpt=final_out[-2000:] if final_out else "",
        )

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
            if normalized_agent == "codex-cli":
                dismissed = _dismiss_codex_hook_review(session)
                log.write(
                    f"=== codex hook review dismissed={dismissed} ===\n"
                )
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
                    if normalized_agent == "codex-cli":
                        if _handle_codex_rate_limit_prompt(session, last_pane):
                            continue
                    if pattern.search(last_pane):
                        # Pi echoes prompts and shows intermediate reasoning/tool text while the
                        # spinner is still active. Do not let a marker in the echoed prompt or
                        # reasoning prematurely end the scenario before the session JSONL is flushed.
                        if normalized_agent == "pi" and "Working" in last_pane:
                            continue
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

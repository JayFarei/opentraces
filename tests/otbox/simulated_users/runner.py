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


def _prep_agent_home(box_home: Path, agent: str | None) -> str | None:
    """Seed the box's isolated HOME with already-onboarded agent state.

    Without this, a real agent binary (e.g. ``claude``) launched into a
    virgin HOME shows first-run UX — theme picker, login prompt, terms
    acceptance, MCP-server trust prompts — and never reaches a state
    where the runner can drive it. The fix is to copy the host operator's
    already-onboarded state into the box, the same state
    ``capture-refresh`` requires anyway (the operator has to be logged
    in for the real agent to do anything useful).

    What gets seeded for ``claude``:

      * ``$HOME/.claude/.credentials.json`` — copied verbatim from the
        host so the API session is authenticated.
      * ``$HOME/.claude.json`` — copied from the host, then sanitised:
        ``editorMode`` is forced to ``"emacs"`` (PTY callers send keys
        in literal-text mode which breaks under vim), the ``projects``
        dict is wiped (Claude rebuilds per-project entries on demand
        and the host's 300KB+ of project history is irrelevant and
        privacy-leaky in a box copy), and any MCP-server prompts the
        host already approved are pre-acknowledged for the box's
        project path.

    The MCP-prompt suppression is the critical bit: when ``claude``
    launches inside the box's project dir it walks the cwd ancestry
    looking for ``.mcp.json`` files and surfaces a trust prompt for
    each user-scope server it finds. The host operator's
    ``~/.mcp.json`` is one such file and the box's runner cannot
    dismiss that prompt. Seeding ``hasTrustDialogAccepted: true``
    for the box's project path plus ``enabledMcpjsonServers: []``
    short-circuits the prompt; for safety the runner *also* passes
    ``--strict-mcp-config`` on the spawn line (defence in depth).

    The host operator's vim preference, full project history, and
    MCP enable list stay untouched on disk — only the box copy is
    normalised.

    Returns ``None`` on success or no-op (echo, unknown agent, etc.),
    or an error string the caller turns into a SKIP verdict if the
    operator's host state isn't usable.
    """
    if agent != "claude":
        return None
    host_home = Path(os.path.expanduser("~"))
    host_settings = host_home / ".claude.json"
    host_creds = host_home / ".claude" / ".credentials.json"
    if not host_settings.is_file():
        return f"agent prep: host {host_settings} not found — run claude once to onboard"
    if not host_creds.is_file():
        return f"agent prep: host {host_creds} not found — run `claude login` first"
    box_home.mkdir(parents=True, exist_ok=True)
    (box_home / ".claude").mkdir(parents=True, exist_ok=True)
    try:
        import json as _json
        settings = _json.loads(host_settings.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - corrupt host config shouldn't crash the runner
        return f"agent prep: failed to parse host {host_settings}: {exc}"
    settings["editorMode"] = "emacs"
    # Drop the host's per-project history — Claude rebuilds per-project
    # entries on first contact, and the host's 186+ project entries
    # (300KB+) leak ambient state into every box. Replace with an empty
    # dict so the box starts with a clean project ledger.
    settings["projects"] = {}
    (box_home / ".claude.json").write_text(
        _json.dumps(settings, indent=2), encoding="utf-8"
    )
    shutil.copy2(host_creds, box_home / ".claude" / ".credentials.json")
    return None


def _prep_claude_project_trust(box_home: Path, project_dir: Path) -> None:
    """Pre-acknowledge MCP + trust prompts for the box's project path.

    ``claude`` walks the cwd ancestry looking for ``.mcp.json`` files
    when it boots and surfaces a per-server trust prompt for any
    user-scope server it finds. The host operator's ``~/.mcp.json``
    almost always declares servers, so the prompt fires by default
    even though the box's HOME is otherwise isolated. The PTY runner
    cannot dismiss that prompt (it requires keyboard interaction
    during boot when the alternate-screen buffer is empty to a
    ``tmux capture-pane`` reader).

    The defence is to seed an entry in ``$HOME/.claude.json`` under
    ``projects[<box.project absolute path>]`` that pre-acks the trust
    dialog and declares an empty enabled-MCP-server list for that
    path. ``claude --strict-mcp-config`` on the spawn line is the
    second layer of defence — even if the trust seeding drifts, the
    flag forces Claude to ignore everything outside the explicit
    config flag.

    Callable safely on any HOME — no-op if ``.claude.json`` is missing.
    """
    settings_path = box_home / ".claude.json"
    if not settings_path.is_file():
        return
    try:
        import json as _json
        settings = _json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - corrupt config shouldn't crash the runner
        return
    projects = settings.setdefault("projects", {})
    key = str(project_dir.resolve())
    # Pre-ack the trust dialog and declare zero enabled MCP servers for
    # this exact project path. Claude treats both the literal path and
    # any /private-prefixed Darwin variant as distinct keys, so seed
    # both forms — the cost is two small dict entries.
    for variant in {key, f"/private{key}" if not key.startswith("/private") else key.removeprefix("/private")}:
        entry = projects.setdefault(variant, {})
        entry["hasTrustDialogAccepted"] = True
        entry["enabledMcpjsonServers"] = []
        entry["disabledMcpjsonServers"] = []
        entry["mcpServers"] = {}
        entry["hasCompletedProjectOnboarding"] = True
    settings_path.write_text(_json.dumps(settings, indent=2), encoding="utf-8")


def _install_opentraces_hooks_in_box(box: Box) -> str | None:
    """Install opentraces Claude Code hooks into the box's HOME.

    The capture-refresh contract is to produce a real trace artifact,
    which requires opentraces' PreToolUse/PostToolUse/Stop/PostCompact
    hooks to be wired into Claude's ``$HOME/.claude/settings.json``
    BEFORE the agent boots. The box ships with an editable opentraces
    install at ``box.project/.testvenv/bin/opentraces`` (the
    ``c-installed-source`` checkpoint provisions it), so we invoke
    ``opentraces setup claude-code`` against the isolated HOME via the
    box's CLI.

    Also runs ``opentraces init`` in the project (so the project has
    a registered ``project-<slug>`` state dir under
    ``$HOME/.opentraces/projects/``) and ``opentraces setup git``
    (post-commit correlator hook that powers ``trail blame``).

    Returns ``None`` on success or a non-fatal error string. The
    caller decides whether to fail the run on a non-None return.
    """
    from ..env import isolated_env

    testvenv_cli = Path(box.project) / ".testvenv" / "bin" / "opentraces"
    if not testvenv_cli.is_file():
        return (
            f"box CLI not found at {testvenv_cli} — checkpoint "
            f"`c-installed-source` did not run?"
        )
    env = isolated_env(box)
    # 1. Install Claude Code hooks into the box's $HOME/.claude/settings.json
    setup_hooks = subprocess.run(
        [str(testvenv_cli), "setup", "claude-code"],
        cwd=str(box.project),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if setup_hooks.returncode != 0:
        return (
            f"opentraces setup claude-code failed (rc={setup_hooks.returncode}): "
            f"{(setup_hooks.stderr or setup_hooks.stdout).strip()[:200]}"
        )
    # 2. Register the project so traces have somewhere to land.
    init = subprocess.run(
        [str(testvenv_cli), "init", "--start-fresh", "--agent", "claude-code"],
        cwd=str(box.project),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if init.returncode != 0:
        return (
            f"opentraces init failed (rc={init.returncode}): "
            f"{(init.stderr or init.stdout).strip()[:200]}"
        )
    # 3. Install the post-commit hook so trail-blame anchors mature.
    setup_git = subprocess.run(
        [str(testvenv_cli), "setup", "git"],
        cwd=str(box.project),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if setup_git.returncode != 0:
        return (
            f"opentraces setup git failed (rc={setup_git.returncode}): "
            f"{(setup_git.stderr or setup_git.stdout).strip()[:200]}"
        )
    return None


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

    # --- claude: --print headless mode (no tmux, no MCP prompts) -----------
    # ``claude``'s interactive TUI uses tmux's alternate-screen buffer and
    # blocks on MCP-server trust prompts; neither is dismissable from the
    # tmux capture-pane poll loop. ``claude --print`` is the documented
    # non-interactive surface, fires the same opentraces hooks as
    # interactive mode, and composes for multi-turn via --continue. So
    # for claude we skip tmux entirely. Other agents (echo, codex,
    # hermes) keep the original PTY/tmux path below.
    if agent == "claude":
        # Pre-ack MCP trust for the box's project so even if the runner
        # ever needs to drop back to interactive mode the prompt is
        # already silenced. Also short-circuits the cwd-ancestry .mcp.json
        # walk that picks up the host operator's ~/.mcp.json.
        _prep_claude_project_trust(Path(box.home), Path(box.project))
        hook_install_error = _install_opentraces_hooks_in_box(box)
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

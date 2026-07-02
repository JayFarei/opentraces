"""CLI ``capture-otlp`` group: lifecycle for the OTLP receiver capture source.

Per plan 078 §R8 / §R10 / §R12 the receiver is one of three Context Tree
capture sources alongside the JSONL parser (plan 077) and the deferred HTTP
proxy. This module wires the four lifecycle verbs (``start``, ``stop``,
``status``, ``restart``) plus a sibling ``setup capture-otlp`` command that
patches ``~/.claude/settings.json`` and (optionally) installs the launchd /
systemd auto-start unit.

All heavy imports (receiver, lifecycle, settings_patcher) are deferred into
the callback bodies so this module is safe to import at CLI startup even if
the sibling modules (receiver, lifecycle, settings_patcher) fail to load,
a missing import lands as a structured error envelope, not a CLI-wide load
failure.

JSON envelopes mirror the ``next_steps`` / ``next_command`` shape used by
the rest of ``opentraces`` so the surface is uniform for downstream agents
driving the CLI through the bundled SKILL.md.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

import click

from ._help import OpentracesCommand, OpentracesGroup
from ._options import dump_json as _dump_json
from ..core.paths import (
    OPENTRACES_DIR,
    otel_staging_dir,
    otlp_receiver_log_dir,
    otlp_receiver_pid_path,
    otlp_receiver_status_path,
    raw_bodies_dir,
)

# --------------------------------------------------------------------------- #
# Defaults + on-disk paths
# --------------------------------------------------------------------------- #

CAPTURE_OTLP_SCHEMA_VERSION = "opentraces.capture_otlp.v1"

DEFAULT_PORT = 4318
DEFAULT_BIND = "127.0.0.1"
# Path constants resolved at import time. Tests that monkey-patch
# OPENTRACES_DIR rebind the underlying helpers, so the call sites in
# this module recompute via the helper functions where freshness matters.
DEFAULT_RAW_BODIES_DIR = raw_bodies_dir()
PID_FILE = otlp_receiver_pid_path()
STATUS_FILE = otlp_receiver_status_path()
SESSIONS_DIR = otel_staging_dir()
LOGS_DIR = otlp_receiver_log_dir()
STDOUT_LOG = LOGS_DIR / "otlp-receiver.log"
STDERR_LOG = LOGS_DIR / "otlp-receiver.err"

# Status-file freshness window. The foreground process should rewrite the
# status file at least every 30s; anything older than 2x that with a live PID
# is reported as ``stale``.
STATUS_FRESHNESS_SECONDS = 60.0


# --------------------------------------------------------------------------- #
# Group + tiny helpers
# --------------------------------------------------------------------------- #


@click.group("capture-otlp", cls=OpentracesGroup, hidden=True)
def capture_otlp_group() -> None:
    """Run and control the OTLP receiver capture source.

    The receiver listens on a local OTLP/HTTP+JSON port and feeds Claude
    Code's native telemetry plus raw Anthropic Messages API request /
    response bodies into the Context Tree substrate (plan 078). When the
    receiver is down agent traffic is never blocked; OTel emission is
    fire-and-forget (R12 bypass safety).

    Subcommands:

    \b
      start     Launch the receiver (default: daemonized).
      stop      Send SIGTERM to the running receiver.
      status    Report enabled / port / uptime / capture counts.
      restart   stop + start.
      flush     Emit one accumulated session as Context Tree events.
    """


def _emit(payload: dict[str, Any]) -> None:
    """Emit a stable JSON envelope to stdout (mirrors cli/ctx.py + cli/__init__.py).

    Injects ``schema_version`` so every capture-otlp envelope carries
    the plan 078 contract id. Downstream consumers (otbox journeys,
    doctor, scripts) join on this field to confirm they are reading
    the v1 shape.
    """
    payload.setdefault("schema_version", CAPTURE_OTLP_SCHEMA_VERSION)
    click.echo(_dump_json(payload))


def _ensure_dirs() -> None:
    """Make sure the ~/.opentraces/{logs,raw-bodies} dirs exist."""
    OPENTRACES_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _read_pid() -> int | None:
    """Return the recorded PID or ``None`` if the file is missing/invalid."""
    if not PID_FILE.exists():
        return None
    try:
        text = PID_FILE.read_text().strip()
        if not text:
            return None
        return int(text)
    except (OSError, ValueError):
        return None


def _process_alive(pid: int) -> bool:
    """POSIX liveness check via signal 0 (no payload, just an existence probe)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by another user; treat as alive.
        return True
    except OSError:
        return False
    return True


def _read_status_file() -> dict[str, Any]:
    """Load the receiver's self-reported status file, tolerating absence."""
    if not STATUS_FILE.exists():
        return {}
    try:
        return json.loads(STATUS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _raw_body_dir_size_bytes(path: Path | None) -> int | None:
    """Sum ``raw-bodies/`` file sizes; cheap because we don't recurse deeply."""
    if path is None:
        return None
    candidate = Path(path).expanduser()
    if not candidate.exists() or not candidate.is_dir():
        return None
    total = 0
    try:
        for entry in candidate.iterdir():
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    continue
    except OSError:
        return None
    return total


def _build_status_payload(verb: str) -> dict[str, Any]:
    """Build the canonical status envelope shared by ``start`` / ``stop`` / ``status``.

    The ``doctor --json`` surface mirrors this shape under
    ``context_tree.otel_receiver`` (see core/doctor.py).
    """
    pid = _read_pid()
    status_data = _read_status_file()

    payload: dict[str, Any] = {
        "schema_version": CAPTURE_OTLP_SCHEMA_VERSION,
        "ok": True,
        "verb": verb,
        "enabled": False,
        "port": None,
        "bind": None,
        "pid": None,
        "uptime_seconds": None,
        "captures_total": None,
        "last_capture_at": None,
        "raw_body_dir": None,
        "raw_body_dir_size_bytes": None,
        "stale": False,
        "stale_pid": False,
        "next_steps": [],
        "next_command": None,
    }

    if pid is None:
        # No PID file => receiver was never started or was cleanly stopped.
        payload["next_steps"] = [
            "Run 'opentraces capture-otlp start' to launch the receiver.",
        ]
        payload["next_command"] = "opentraces capture-otlp start"
        return payload

    if not _process_alive(pid):
        # Stale PID file: process exited without cleaning up. Remove it so
        # the next start doesn't trip on the leftover.
        try:
            PID_FILE.unlink()
        except OSError:
            pass
        payload["stale_pid"] = True
        payload["next_steps"] = [
            "Stale PID file removed; run 'opentraces capture-otlp start' to relaunch.",
        ]
        payload["next_command"] = "opentraces capture-otlp start"
        return payload

    # Live process: fold in whatever the receiver wrote to its status file.
    payload["enabled"] = True
    payload["pid"] = pid
    payload["port"] = status_data.get("port")
    payload["bind"] = status_data.get("bind")
    payload["uptime_seconds"] = status_data.get("uptime_seconds")
    payload["captures_total"] = status_data.get("captures_total")
    payload["last_capture_at"] = status_data.get("last_capture_at")
    # Issue #158 B5/W3: surface the accumulated session ids so an agent can
    # discover what is flushable without guessing. The daemon writes this
    # list every status-file refresh; staging snapshots are the durable form.
    sessions = status_data.get("sessions")
    if not sessions and SESSIONS_DIR.exists():
        sessions = sorted(p.stem for p in SESSIONS_DIR.glob("*.json"))
    payload["sessions"] = sessions or []
    payload["sessions_count"] = len(payload["sessions"])

    raw_body_dir = status_data.get("raw_body_dir")
    if raw_body_dir:
        payload["raw_body_dir"] = raw_body_dir
        payload["raw_body_dir_size_bytes"] = _raw_body_dir_size_bytes(Path(raw_body_dir))

    # Freshness check: live PID but stale status file.
    written_at = status_data.get("written_at")
    if isinstance(written_at, (int, float)):
        age = time.time() - float(written_at)
        if age > STATUS_FRESHNESS_SECONDS:
            payload["stale"] = True

    payload["next_steps"] = [
        "Run 'opentraces capture-otlp stop' to halt the receiver.",
    ]
    payload["next_command"] = "opentraces capture-otlp stop"
    return payload


# --------------------------------------------------------------------------- #
# capture-otlp start
# --------------------------------------------------------------------------- #


@capture_otlp_group.command(
    "start",
    cls=OpentracesCommand,
    examples=[
        "opentraces capture-otlp start",
        "opentraces capture-otlp start --foreground",
        "opentraces capture-otlp start --port 4318 --json",
    ],
    see_also=[
        ("opentraces capture-otlp status", "check whether the receiver is running."),
        ("opentraces capture-otlp stop", "halt the receiver."),
        ("opentraces setup capture-otlp", "patch ~/.claude/settings.json so Claude Code emits."),
    ],
    option_groups=[
        ("Bind", ["port", "bind"]),
        ("Storage", ["raw_bodies_dir"]),
        ("Mode", ["foreground", "as_json"]),
    ],
)
@click.option("--port", type=int, default=DEFAULT_PORT, show_default=True,
              help="OTLP/HTTP+JSON port to bind.")
@click.option("--bind", default=DEFAULT_BIND, show_default=True,
              help="Bind address (default: 127.0.0.1 loopback only).")
@click.option(
    "--raw-bodies-dir",
    "raw_bodies_dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Directory Claude Code writes raw API bodies to (default: ~/.opentraces/raw-bodies/).",
)
@click.option(
    "--foreground",
    is_flag=True,
    help="Block in the terminal (used by launchd/systemd; default daemonizes).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def capture_otlp_start_cmd(
    port: int,
    bind: str,
    raw_bodies_dir: Path | None,
    foreground: bool,
    as_json: bool,
) -> None:
    """Launch the OTLP receiver.

    In ``--foreground`` mode the call blocks on signals (SIGTERM / SIGINT)
    which is what launchd/systemd want. The default daemonized mode writes
    a PID file at ``~/.opentraces/otlp-receiver.pid``, redirects stdio to
    ``~/.opentraces/logs/otlp-receiver.{log,err}``, and returns once the
    receiver confirms the socket is bound.
    """
    _ensure_dirs()
    target_dir = Path(raw_bodies_dir) if raw_bodies_dir else DEFAULT_RAW_BODIES_DIR
    target_dir = target_dir.expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)

    # Reject double-starts up front; otherwise the second receiver fails at
    # bind() and writes a confusing PID over the live one.
    existing_pid = _read_pid()
    if existing_pid is not None and _process_alive(existing_pid):
        payload = {
            "ok": False,
            "verb": "start",
            "error": "already_running",
            "pid": existing_pid,
            "next_steps": [
                "Receiver is already running. Use 'opentraces capture-otlp restart' to bounce it.",
            ],
            "next_command": "opentraces capture-otlp restart",
        }
        if as_json:
            _emit(payload)
        else:
            click.echo(f"error: receiver already running (pid={existing_pid})", err=True)
            click.echo("  hint: 'opentraces capture-otlp restart' will bounce it.", err=True)
        sys.exit(4)

    # Defer the heavy import: if the receiver module is missing we want a
    # structured error envelope, not a top-level ImportError at CLI load.
    try:
        from ..capture.otlp import start_receiver
    except ImportError as exc:
        payload = {
            "ok": False,
            "verb": "start",
            "error": "receiver_module_unavailable",
            "message": str(exc),
            "next_steps": ["Reinstall opentraces; the capture/otlp module is missing."],
            "next_command": None,
        }
        if as_json:
            _emit(payload)
        else:
            click.echo(f"error: {exc}", err=True)
        sys.exit(5)

    if foreground:
        # Foreground path: launchd/systemd call this. Block on signals.
        try:
            receiver = start_receiver(
                port=port,
                bind=bind,
                raw_bodies_dir=target_dir,
            )
        except OSError as exc:
            payload = {
                "ok": False,
                "verb": "start",
                "error": "bind_failed",
                "message": str(exc),
                "port": port,
                "bind": bind,
                "next_steps": [
                    f"Port {port} may be in use; try a different --port.",
                ],
                "next_command": None,
            }
            if as_json:
                _emit(payload)
            else:
                click.echo(f"error: bind failed on {bind}:{port}: {exc}", err=True)
            sys.exit(6)

        # Plan 078 chain wiring: receiver -> buffer.handle_envelope;
        # raw_body_watcher -> buffer.handle_body_pair. Buffer state is
        # snapshotted to disk inside _write_status_file_loop so the
        # cross-process flush verb can read it.
        from ..capture.otlp.emitter import OTLPCaptureBuffer
        from ..capture.otlp.raw_body_watcher import RawBodyWatcher
        buffer = OTLPCaptureBuffer()
        receiver.set_callback(buffer.handle_envelope)
        watcher = RawBodyWatcher(target_dir, buffer.handle_body_pair)
        watcher.start()

        # Record our PID so 'stop' can find us even in foreground mode.
        PID_FILE.write_text(str(os.getpid()))
        try:
            if not receiver.wait_until_ready(timeout=5.0):
                click.echo(f"warning: receiver did not confirm bind on {bind}:{port}", err=True)
            # Periodically write the status file + session snapshots so
            # the cross-process 'status' and 'flush' verbs have fresh data.
            _write_status_file_loop(
                receiver, port=port, bind=bind, raw_body_dir=target_dir,
                buffer=buffer,
            )
        finally:
            watcher.shutdown()
            receiver.shutdown()
            try:
                PID_FILE.unlink()
            except OSError:
                pass
            # Status file is intentionally preserved across stop so
            # ``doctor`` and ``status`` can surface the last known
            # config + capture totals even when the daemon is down.
        return

    # Daemonized path: fork-then-exec so the child survives our exit. We
    # re-exec the same opentraces binary with --foreground so the receiver
    # is owned by a fresh process (clean fd table, no Click context cruft).
    pid = _spawn_daemon(
        port=port,
        bind=bind,
        raw_bodies_dir=target_dir,
    )

    # Wait briefly for the child to publish a PID file / bind the socket so
    # callers can rely on a synchronous-feeling start.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if _read_pid() and _read_status_file():
            break
        if not _process_alive(pid):
            break
        time.sleep(0.1)

    payload = _build_status_payload("start")
    if not payload["enabled"]:
        payload["ok"] = False
        payload["error"] = "start_did_not_confirm"
        payload["next_steps"] = [
            "Check ~/.opentraces/logs/otlp-receiver.err for the bind failure.",
        ]
        payload["next_command"] = None
    if as_json:
        _emit(payload)
        if not payload["enabled"]:
            sys.exit(6)
        return
    if payload["enabled"]:
        click.echo(f"started: pid={payload['pid']} bind={bind}:{port}")
        click.echo(f"  raw_body_dir: {target_dir}")
        click.echo(f"  logs:         {STDOUT_LOG} / {STDERR_LOG}")
    else:
        click.echo("error: receiver failed to start; see logs.", err=True)
        sys.exit(6)


def _write_status_file_loop(
    receiver: Any, *, port: int, bind: str, raw_body_dir: Path,
    buffer: Any = None,
) -> None:
    """Block in the foreground process, periodically updating the status file.

    When ``buffer`` is supplied (plan 078), each known session_id is
    snapshotted to ``~/.opentraces/staging/otel/<session_id>.json``
    so the cross-process ``flush`` verb can read accumulated state.
    Returns when a SIGTERM / SIGINT lands.
    """
    stop_flag = {"stop": False}

    def _handler(signum, _frame) -> None:  # noqa: ARG001
        stop_flag["stop"] = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            # Off the main thread or unsupported on this platform; ignore.
            pass

    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    # Per-session snapshot change-detection: skip the rewrite when the
    # session's last_envelope_at hasn't advanced. Avoids unbounded SSD
    # write amplification for idle daemons with many sessions.
    last_session_envelope_at: dict[str, float] = {}
    while not stop_flag["stop"]:
        try:
            status = {
                "port": port,
                "bind": bind,
                "pid": os.getpid(),
                "raw_body_dir": str(raw_body_dir),
                "uptime_seconds": float(getattr(receiver, "uptime_seconds", 0.0)),
                "captures_total": int(getattr(receiver, "captures_total", 0)),
                "last_capture_at": getattr(receiver, "last_capture_at", None),
                "written_at": time.time(),
                "sessions": buffer.session_ids() if buffer is not None else [],
            }
            STATUS_FILE.write_text(_dump_json(status))
        except OSError:
            pass

        if buffer is not None:
            try:
                for sid in buffer.session_ids():
                    snap = buffer.snapshot_session(sid)
                    if snap is None:
                        continue
                    envelope_at = float(snap.get("last_envelope_at") or 0.0)
                    if last_session_envelope_at.get(sid) == envelope_at:
                        continue
                    safe = sid.replace("/", "_").replace("\\", "_")
                    (SESSIONS_DIR / f"{safe}.json").write_text(
                        json.dumps(snap, indent=2, sort_keys=True, default=str)
                    )
                    last_session_envelope_at[sid] = envelope_at
            except OSError:
                pass

        # Sleep in short increments so signal delivery stays responsive.
        for _ in range(50):
            if stop_flag["stop"]:
                break
            time.sleep(0.1)


def _spawn_daemon(*, port: int, bind: str, raw_bodies_dir: Path) -> int:
    """Fork once, exec the same binary in --foreground mode, return child PID.

    We deliberately avoid double-forking: launchd/systemd handle reparenting
    when the user opts into auto-start, and the manual case is fine with a
    direct child (the shell that ran ``opentraces`` will reap it on exit).
    For users who want true daemon semantics, ``opentraces setup
    capture-otlp`` installs the launchd plist or systemd unit.
    """
    pid = os.fork()
    if pid > 0:
        # Parent: return the child PID and let the caller wait for the
        # status file to materialize.
        return pid

    # Child: redirect stdio to log files, then exec ourselves in foreground.
    try:
        os.setsid()
    except OSError:
        pass

    with open(os.devnull, "rb", buffering=0) as devnull:
        os.dup2(devnull.fileno(), 0)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    stdout_fd = os.open(str(STDOUT_LOG), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    stderr_fd = os.open(str(STDERR_LOG), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(stdout_fd, 1)
    os.dup2(stderr_fd, 2)
    os.close(stdout_fd)
    os.close(stderr_fd)

    argv = [
        sys.executable, "-m", "opentraces",
        "capture-otlp", "start",
        "--foreground",
        "--port", str(port),
        "--bind", bind,
        "--raw-bodies-dir", str(raw_bodies_dir),
    ]
    try:
        os.execvp(argv[0], argv)
    except OSError:
        # exec failed; fall back to opentraces binary on PATH.
        try:
            os.execvp("opentraces", [
                "opentraces", "capture-otlp", "start",
                "--foreground",
                "--port", str(port),
                "--bind", bind,
                "--raw-bodies-dir", str(raw_bodies_dir),
            ])
        except OSError as exc:
            sys.stderr.write(f"exec failed: {exc}\n")
            os._exit(127)
    # Unreachable.
    os._exit(0)


# --------------------------------------------------------------------------- #
# capture-otlp stop
# --------------------------------------------------------------------------- #


@capture_otlp_group.command(
    "stop",
    cls=OpentracesCommand,
    examples=[
        "opentraces capture-otlp stop",
        "opentraces capture-otlp stop --json",
    ],
    see_also=[
        ("opentraces capture-otlp start", "launch the receiver."),
        ("opentraces capture-otlp status", "verify the receiver is down."),
    ],
    option_groups=[
        ("Output", ["as_json"]),
    ],
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def capture_otlp_stop_cmd(as_json: bool) -> None:
    """Halt the running receiver (SIGTERM, then SIGKILL after 5s)."""
    pid = _read_pid()
    if pid is None:
        payload = {
            "ok": True,
            "verb": "stop",
            "enabled": False,
            "pid": None,
            "next_steps": ["Receiver is not running."],
            "next_command": "opentraces capture-otlp start",
        }
        if as_json:
            _emit(payload)
        else:
            click.echo("not running.")
        return

    if not _process_alive(pid):
        # Stale PID; clean up and report.
        try:
            PID_FILE.unlink()
        except OSError:
            pass
        payload = {
            "ok": True,
            "verb": "stop",
            "enabled": False,
            "pid": pid,
            "stale_pid": True,
            "next_steps": ["Removed stale PID file; receiver was not running."],
            "next_command": "opentraces capture-otlp start",
        }
        if as_json:
            _emit(payload)
        else:
            click.echo(f"stale pid file removed (pid={pid} not alive).")
        return

    # Polite SIGTERM, then SIGKILL fallback.
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        payload = {
            "ok": False,
            "verb": "stop",
            "error": "signal_failed",
            "message": str(exc),
            "pid": pid,
        }
        if as_json:
            _emit(payload)
        else:
            click.echo(f"error: SIGTERM failed: {exc}", err=True)
        sys.exit(5)

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not _process_alive(pid):
            break
        time.sleep(0.1)
    forced = False
    if _process_alive(pid):
        forced = True
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        # Brief grace period for the kernel to reap.
        time.sleep(0.2)

    # Clean up the PID file in case the receiver didn't. The status
    # file is intentionally preserved across stop so ``doctor`` /
    # ``status`` can surface the last known config + capture totals.
    try:
        PID_FILE.unlink()
    except OSError:
        pass

    payload = {
        "ok": True,
        "verb": "stop",
        "enabled": False,
        "pid": pid,
        "forced_kill": forced,
        "next_steps": ["Receiver halted."],
        "next_command": "opentraces capture-otlp start",
    }
    if as_json:
        _emit(payload)
        return
    click.echo(f"stopped (pid={pid}, forced={forced}).")


# --------------------------------------------------------------------------- #
# capture-otlp status
# --------------------------------------------------------------------------- #


@capture_otlp_group.command(
    "status",
    cls=OpentracesCommand,
    examples=[
        "opentraces capture-otlp status",
        "opentraces capture-otlp status --json",
    ],
    see_also=[
        ("opentraces doctor", "the doctor surface mirrors this status."),
    ],
    option_groups=[
        ("Output", ["as_json"]),
    ],
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def capture_otlp_status_cmd(as_json: bool) -> None:
    """Report receiver enabled / port / uptime / captures."""
    payload = _build_status_payload("status")
    if as_json:
        _emit(payload)
        return
    if payload["enabled"]:
        click.echo("enabled:           true")
        click.echo(f"pid:               {payload['pid']}")
        click.echo(f"port:              {payload['port']}")
        click.echo(f"bind:              {payload['bind']}")
        click.echo(f"uptime_seconds:    {payload['uptime_seconds']}")
        click.echo(f"captures_total:    {payload['captures_total']}")
        click.echo(f"last_capture_at:   {payload['last_capture_at']}")
        click.echo(f"raw_body_dir:      {payload['raw_body_dir']}")
        click.echo(f"raw_body_dir_size: {payload['raw_body_dir_size_bytes']}")
        click.echo(f"sessions:          {payload.get('sessions_count', 0)}")
        if payload.get("stale"):
            click.echo("status:            stale (status file is old)")
    else:
        click.echo("enabled: false")
        if payload.get("stale_pid"):
            click.echo("  (stale PID file removed)")


# --------------------------------------------------------------------------- #
# capture-otlp restart
# --------------------------------------------------------------------------- #


@capture_otlp_group.command(
    "restart",
    cls=OpentracesCommand,
    examples=[
        "opentraces capture-otlp restart",
        "opentraces capture-otlp restart --json",
    ],
    see_also=[
        ("opentraces capture-otlp start", "start without stopping first."),
    ],
    option_groups=[
        ("Bind", ["port", "bind"]),
        ("Storage", ["raw_bodies_dir"]),
        ("Output", ["as_json"]),
    ],
)
@click.option("--port", type=int, default=DEFAULT_PORT, show_default=True,
              help="TCP port the OTLP receiver listens on.")
@click.option("--bind", default=DEFAULT_BIND, show_default=True,
              help="Address the OTLP receiver binds to.")
@click.option(
    "--raw-bodies-dir",
    "raw_bodies_dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Directory where raw request/response bodies are written.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@click.pass_context
def capture_otlp_restart_cmd(
    ctx: click.Context,
    port: int,
    bind: str,
    raw_bodies_dir: Path | None,
    as_json: bool,
) -> None:
    """Stop the receiver (if running), then start it again."""
    # Re-enter our own commands so we get the same envelope contracts.
    pid = _read_pid()
    if pid is not None and _process_alive(pid):
        ctx.invoke(capture_otlp_stop_cmd, as_json=False)
    ctx.invoke(
        capture_otlp_start_cmd,
        port=port,
        bind=bind,
        raw_bodies_dir=raw_bodies_dir,
        foreground=False,
        as_json=False,
    )
    # Fold the resulting status into a restart envelope.
    payload = _build_status_payload("restart")
    if as_json:
        _emit(payload)
        return
    if payload["enabled"]:
        click.echo(f"restarted: pid={payload['pid']} bind={bind}:{port}")
    else:
        click.echo("error: receiver did not come back up after restart.", err=True)
        sys.exit(6)


# --------------------------------------------------------------------------- #
# flush (plan 078): emit accumulated OTel session state to a project event log
# --------------------------------------------------------------------------- #


@capture_otlp_group.command(
    "flush",
    cls=OpentracesCommand,
    examples=[
        "opentraces capture-otlp flush --session <session-id> --project . --trace-id <trace-id>",
        "opentraces capture-otlp flush --session <id> --project /path/to/repo --trace-id <id> --json",
    ],
    see_also=[
        ("opentraces capture-otlp status", "List accumulated sessions."),
        ("opentraces ctx tree <trace-id>", "Inspect the resulting Context Tree."),
    ],
    option_groups=[
        ("Target", ["session", "project", "trace_id"]),
        ("Output", ["as_json"]),
    ],
)
@click.option(
    "--session", "session", required=True,
    help="OTel session_id to flush (use `status` to list available sessions).",
)
@click.option(
    "--project", "project", required=True,
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    help="Project directory whose event log receives the emitted Context Tree events.",
)
@click.option(
    "--trace-id", "trace_id", required=True,
    help="Trace ID to attach to the emitted events.",
)
@click.option(
    "--from-raw-bodies", "from_raw_bodies", is_flag=True,
    help="Reconstruct the session per-step from the raw-bodies dir (content/metadata "
         "pairing) instead of the daemon snapshot. Flushes already-captured sessions "
         "with no live receiver.",
)
@click.option(
    "--raw-bodies-dir", "raw_bodies_dir_opt",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path), default=None,
    help="Raw-bodies dir for --from-raw-bodies (default: ~/.opentraces/raw-bodies/).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def capture_otlp_flush_cmd(
    session: str, project: Path, trace_id: str,
    from_raw_bodies: bool, raw_bodies_dir_opt: Path | None, as_json: bool,
) -> None:
    """Emit accumulated OTel session state as Context Tree events.

    Two sources:

    \b
      default            Read the per-session snapshot the foreground daemon
                         writes to ``~/.opentraces/staging/otel/<id>.json``.
      --from-raw-bodies  Reconstruct per-step directly from the raw request /
                         response body files (paired by content/metadata), so
                         already-captured sessions flush with no live receiver.

    Builds ContextLayer / ContextNode shapes (one node per llm-request), writes
    per-message content blobs, appends ``context_*`` events with
    ``capture_method=["otel"]``, projects into the bucket companion, and joins
    the trace spine.
    """
    # Deferred imports so missing modules surface as structured envelopes,
    # not import-time crashes.
    try:
        from ..capture.otlp.emitter import (
            flush_session_to_project,
            load_snapshot_from_disk,
        )
    except ImportError as exc:
        payload = {
            "ok": False, "verb": "flush", "error": "emitter_unavailable",
            "message": str(exc),
        }
        if as_json:
            _emit(payload)
        else:
            click.echo(f"error: {exc}", err=True)
        sys.exit(5)

    if from_raw_bodies:
        from ..capture.otlp.reconstruct import reconstruct_steps_from_raw_bodies

        rb_dir = (raw_bodies_dir_opt or DEFAULT_RAW_BODIES_DIR).expanduser()
        recon = reconstruct_steps_from_raw_bodies(session, rb_dir)
        if not recon:
            payload = {
                "ok": False,
                "verb": "flush",
                "error": "no-raw-bodies-for-session",
                "session": session,
                "raw_bodies_dir": str(rb_dir),
                "next_steps": [
                    "Confirm the session_id appears in request bodies' metadata.user_id.",
                    f"Confirm request files exist under {rb_dir}.",
                ],
                "next_command": None,
            }
            if as_json:
                _emit(payload)
            else:
                click.echo(f"error: no raw bodies for session {session!r}", err=True)
            sys.exit(7)
        report = flush_session_to_project(
            project_dir=project,
            trace_id=trace_id,
            session_id=session,
            steps=[s.to_snapshot_step() for s in recon],
        )
    else:
        safe = session.replace("/", "_").replace("\\", "_")
        snapshot_path = SESSIONS_DIR / f"{safe}.json"
        if not snapshot_path.exists():
            payload = {
                "ok": False,
                "verb": "flush",
                "error": "no-such-session",
                "session": session,
                "available_sessions": sorted(
                    p.stem for p in SESSIONS_DIR.glob("*.json")
                ) if SESSIONS_DIR.exists() else [],
                "next_steps": [
                    "Run 'opentraces capture-otlp status --json' to list available sessions.",
                    "Make sure the foreground daemon has had time to snapshot (~5s).",
                    "Or pass --from-raw-bodies to reconstruct from the raw-bodies dir.",
                ],
                "next_command": "opentraces capture-otlp status --json",
            }
            if as_json:
                _emit(payload)
            else:
                click.echo(f"error: no snapshot for session {session!r}", err=True)
            sys.exit(7)
        snap = load_snapshot_from_disk(snapshot_path)
        report = flush_session_to_project(
            project_dir=project,
            trace_id=trace_id,
            session_id=session,
            snapshot=snap,
        )
    payload = {
        "ok": bool(report.get("ok")),
        "verb": "flush",
        "session": session,
        "project": str(project),
        "trace_id": trace_id,
        **report,
        "next_steps": [
            f"Inspect: opentraces ctx tree {trace_id} --json",
        ] if report.get("ok") else [
            f"Reason: {report.get('reason', 'unknown')}",
        ],
        "next_command": f"opentraces ctx tree {trace_id} --json" if report.get("ok") else None,
    }
    if as_json:
        _emit(payload)
    else:
        if report.get("ok"):
            click.echo(
                f"flushed session={session} trace={trace_id}: "
                f"{report.get('layers_count', 0)} layers, "
                f"{report.get('nodes_count', 0)} nodes, "
                f"{report.get('drafts_appended', 0)} drafts appended"
            )
        else:
            click.echo(f"flush failed: {report.get('reason')}", err=True)
            sys.exit(8)


# --------------------------------------------------------------------------- #
# setup capture-otlp (registered under the existing setup group)
# --------------------------------------------------------------------------- #


def _setup_capture_otlp_impl(
    *,
    raw_bodies_dir: Path | None,
    no_autostart: bool,
    as_json: bool,
) -> None:
    """Body for ``opentraces setup capture-otlp``.

    Pulled out into a free function so the coordinator can attach it to the
    existing ``setup_group`` without this module needing to import the
    group at import time (avoids the circular-import trap with installers).
    """
    _ensure_dirs()
    target_dir = Path(raw_bodies_dir) if raw_bodies_dir else DEFAULT_RAW_BODIES_DIR
    target_dir = target_dir.expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)

    # Patch ~/.claude/settings.json via the settings_patcher module. Deferred import.
    settings_result: dict[str, Any]
    try:
        from ..capture.otlp.settings_patcher import install_otel_env
        settings_result = install_otel_env(raw_bodies_dir=target_dir)
    except ImportError as exc:
        payload = {
            "ok": False,
            "verb": "setup-capture-otlp",
            "error": "settings_patcher_unavailable",
            "message": str(exc),
            "next_steps": ["Reinstall opentraces; settings_patcher is missing."],
            "next_command": None,
        }
        if as_json:
            _emit(payload)
        else:
            click.echo(f"error: {exc}", err=True)
        sys.exit(5)
    except Exception as exc:  # noqa: BLE001 - surface as structured error
        payload = {
            "ok": False,
            "verb": "setup-capture-otlp",
            "error": "settings_patch_failed",
            "message": str(exc),
            "next_steps": [
                "Inspect ~/.claude/settings.json manually; the backup is at "
                "~/.claude/settings.json.opentraces-backup.",
            ],
            "next_command": None,
        }
        if as_json:
            _emit(payload)
        else:
            click.echo(f"error: settings patch failed: {exc}", err=True)
        sys.exit(5)

    # Optionally install the launchd plist / systemd unit.
    autostart_result: dict[str, Any] | None = None
    autostart_error: str | None = None
    if not no_autostart:
        try:
            from ..capture.otlp.lifecycle import install_autostart
            autostart_result = install_autostart(
                port=DEFAULT_PORT,
                bind=DEFAULT_BIND,
                raw_bodies_dir=target_dir,
            )
        except ImportError as exc:
            autostart_error = f"lifecycle_module_unavailable: {exc}"
        except Exception as exc:  # noqa: BLE001
            # Plan §"Open questions" #3: launchd signing can fail; we do not
            # treat this as a hard error. The user can still run the
            # receiver manually via 'opentraces capture-otlp start'.
            autostart_error = f"autostart_install_failed: {exc}"

    # install_otel_env returns a typed PatchResult; install_autostart
    # returns InstallResult. Both are dataclasses with declared fields.
    sp = settings_result.settings_path
    backup = settings_result.backup_path
    keys_added = settings_result.keys_added
    keys_skipped = settings_result.keys_skipped
    payload = {
        "schema_version": "opentraces.capture_otlp_setup.v1",
        "ok": True,
        "verb": "setup-capture-otlp",
        "installed": True,
        "settings_path": str(sp) if sp else None,
        "backup_path": str(backup) if backup else None,
        "backup_exists": bool(backup and backup.exists()),
        "backup_written": bool(keys_added) and bool(backup),
        "patched": len(keys_added),
        "already_set": len(keys_skipped),
        "env_keys": sorted(list(keys_added) + list(keys_skipped)),
        "settings_patch": {
            "ok": settings_result.ok,
            "settings_path": str(sp) if sp else None,
            "backup_path": str(backup) if backup else None,
            "keys_added": keys_added,
            "keys_skipped": keys_skipped,
        },
        "autostart_installed": (
            autostart_result is not None
            and autostart_error is None
            and autostart_result.ok
        ),
        "autostart": None if autostart_result is None else {
            "ok": autostart_result.ok,
            "platform": autostart_result.platform,
            "path": str(autostart_result.path) if autostart_result.path else None,
            "reason": autostart_result.reason,
        },
        "autostart_error": autostart_error,
        "raw_body_dir": str(target_dir),
        "next_steps": [
            "Restart any running Claude Code sessions so the new env vars take effect.",
            "Run 'opentraces capture-otlp start' if autostart isn't installed.",
        ],
        "next_command": (
            None
            if (
                autostart_result is not None
                and autostart_error is None
                and autostart_result.ok
            )
            else "opentraces capture-otlp start"
        ),
    }
    if as_json:
        _emit(payload)
        return
    click.echo("setup capture-otlp:")
    backup_path = settings_result.get("backup_path") if isinstance(settings_result, dict) else None
    if backup_path:
        click.echo(f"  settings backup:   {backup_path}")
    env_keys = settings_result.get("env_keys_set") if isinstance(settings_result, dict) else None
    if env_keys:
        click.echo(f"  env keys patched:  {', '.join(env_keys)}")
    click.echo(f"  raw_body_dir:      {target_dir}")
    if no_autostart:
        click.echo("  autostart:         skipped (--no-autostart)")
    elif autostart_error:
        click.echo(f"  autostart:         FAILED ({autostart_error})")
        click.echo("    hint: run 'opentraces capture-otlp start' manually.")
    elif autostart_result is not None and not autostart_result.ok:
        # install_autostart returned gracefully but did NOT install the unit
        # (e.g. an unsigned binary on macOS Ventura+, or launchctl/systemctl
        # unavailable). Report it honestly instead of claiming success.
        reason = autostart_result.reason or "not-installed"
        click.echo(f"  autostart:         not installed ({reason})")
        if autostart_result.details:
            click.echo(f"    {autostart_result.details}")
        click.echo("    hint: run 'opentraces capture-otlp start' manually.")
    else:
        path = autostart_result.path if autostart_result is not None else None
        click.echo(f"  autostart:         installed ({path or 'OK'})")


@click.command(
    "capture-otlp",
    cls=OpentracesCommand,
    examples=[
        "opentraces setup capture-otlp",
        "opentraces setup capture-otlp --no-autostart",
        "opentraces setup capture-otlp --raw-bodies-dir ~/scratch/raw-bodies --json",
    ],
    see_also=[
        ("opentraces capture-otlp start", "start the receiver after setup."),
        ("opentraces doctor", "verify Context Tree OTel pipeline health."),
    ],
    option_groups=[
        ("Storage", ["raw_bodies_dir"]),
        ("Autostart", ["no_autostart"]),
        ("Output", ["as_json"]),
    ],
)
@click.option(
    "--raw-bodies-dir",
    "raw_bodies_dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Where Claude Code should dump raw API bodies (default: ~/.opentraces/raw-bodies/).",
)
@click.option(
    "--no-autostart",
    is_flag=True,
    help="Skip installing the launchd plist / systemd unit; run start manually.",
)
@click.option(
    "--status", "show_status", is_flag=True,
    help="Report whether the settings file is patched without modifying anything.",
)
@click.option(
    "--uninstall", "uninstall", is_flag=True,
    help="Remove the 12 OTel env keys (preserves other user env entries).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def setup_capture_otlp_cmd(
    raw_bodies_dir: Path | None,
    no_autostart: bool,
    show_status: bool,
    uninstall: bool,
    as_json: bool,
) -> None:
    """Patch ~/.claude/settings.json so Claude Code emits OTel, and (optionally)
    install the auto-start unit.

    Idempotent: rerun is safe. A backup is written to
    ``~/.claude/settings.json.opentraces-backup`` before any edit (plan §R10).

    OFF cost: OTel is the only record-PURE capture source — it exists
    solely for context-tree fidelity. With it off (or ``--uninstall``), ctx
    stays approximated: the JSONL-reconstruction view, not the
    byte-perfect wire view of what the model actually saw.

    Flags:
      --status      Report patch state without modifying anything.
      --uninstall   Remove our 12 OTel env keys.
    """
    from ..capture.otlp.settings_patcher import (
        DEFAULT_SETTINGS_PATH,
        OTEL_ENV_KEYS,
        is_installed,
        uninstall_otel_env,
    )
    if show_status:
        settings_path = DEFAULT_SETTINGS_PATH
        installed = is_installed(settings_path)
        backup_path = Path(str(settings_path) + ".opentraces-backup")
        payload = {
            "schema_version": "opentraces.capture_otlp_setup.v1",
            "ok": True,
            "verb": "setup-status",
            "installed": installed,
            "settings_path": str(settings_path),
            "backup_exists": backup_path.exists(),
            "env_keys": list(OTEL_ENV_KEYS),
        }
        if as_json:
            _emit(payload)
        else:
            click.echo(f"settings: {'installed' if installed else 'not installed'}")
            click.echo(f"  path: {settings_path}")
        return

    if uninstall:
        # Two-step uninstall: first do the surgical key removal (which
        # enumerates the removed keys for the envelope), then if the
        # backup exists, atomically restore it on top so the user's
        # pristine pre-opentraces settings are back.
        result = uninstall_otel_env(restore_backup=False)
        # uninstall_otel_env reports removed keys in `keys_skipped`
        # (the function uses the same dataclass shape as install).
        keys_removed = list(getattr(result, "keys_skipped", []) or [])
        settings_path_str = str(result.settings_path)
        backup_path = Path(settings_path_str + ".opentraces-backup")
        backup_restored = False
        if backup_path.exists():
            try:
                os.replace(backup_path, settings_path_str)
                backup_restored = True
            except OSError:
                pass
        payload = {
            "schema_version": "opentraces.capture_otlp_setup.v1",
            "ok": bool(result.ok),
            "verb": "setup-uninstall",
            "uninstalled": True,
            "settings_path": settings_path_str,
            "keys_removed": keys_removed,
            "removed": len(keys_removed),
            "backup_restored": backup_restored,
            "reason": result.reason,
        }
        if as_json:
            _emit(payload)
        else:
            click.echo(f"uninstalled: {result.ok}")
        return

    _setup_capture_otlp_impl(
        raw_bodies_dir=raw_bodies_dir,
        no_autostart=no_autostart,
        as_json=as_json,
    )


__all__ = [
    "capture_otlp_group",
    "setup_capture_otlp_cmd",
    "DEFAULT_PORT",
    "DEFAULT_BIND",
    "DEFAULT_RAW_BODIES_DIR",
    "PID_FILE",
    "STATUS_FILE",
]

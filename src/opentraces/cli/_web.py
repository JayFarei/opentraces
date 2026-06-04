"""Web / TUI launch helpers and port-management utilities.

Extracted from cli/__init__.py (behavior-preserving split).
All symbols are re-exported from opentraces.cli for backward-compat.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time

import click

logger = logging.getLogger(__name__)


def _launch_tui_ui(fullscreen: bool = False, limit: int | None = 500) -> None:
    del fullscreen
    del limit
    click.echo(
        "The legacy TUI review client is decommissioned for now. "
        "Use `opentraces dataset review <name> --json`.",
        err=True,
    )
    sys.exit(2)


def _listener_pid_for_port(port: int) -> int | None:
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        return None
    for line in result.stdout.splitlines():
        if line.startswith("p"):
            try:
                return int(line[1:])
            except ValueError:
                return None
    return None


def _command_for_pid(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _port_is_listening(port: int, *, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0


def _wait_for_port_release(port: int, *, timeout_s: float = 5.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not _port_is_listening(port):
            return True
        time.sleep(0.1)
    return not _port_is_listening(port)


def _is_opentraces_web_process(command: str) -> bool:
    cmd = command.lower()
    return (
        "opentraces" in cmd
        or " ot web" in cmd
        or "opentraces.cli" in cmd
        or "opentraces.clients.web" in cmd
    )


def _reclaim_stale_web_port(port: int) -> bool:
    if not _port_is_listening(port):
        return False
    pid = _listener_pid_for_port(port)
    if pid is None:
        raise click.ClickException(
            f"Port {port} is already in use. Stop that process or run `opentraces web --port <port>`."
        )
    command = _command_for_pid(pid)
    if not _is_opentraces_web_process(command):
        detail = f"PID {pid}" if not command else f"PID {pid} ({command})"
        raise click.ClickException(
            f"Port {port} is already in use by {detail}. Stop that process or run `opentraces web --port <port>`."
        )
    click.echo(f"Port {port} is already in use by an earlier opentraces web server (PID {pid}). Stopping it first.")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError as exc:
        raise click.ClickException(
            f"Could not stop stale opentraces web server PID {pid}: {exc}"
        ) from exc
    if _wait_for_port_release(port):
        return True
    raise click.ClickException(
        f"Port {port} is still busy after signalling stale opentraces server PID {pid}. "
        "Stop it manually or choose another port."
    )


def _serve_web_app(app, *, host: str, port: int) -> str | None:
    from werkzeug.serving import make_server

    if _port_is_listening(port):
        _reclaim_stale_web_port(port)
    try:
        server = make_server(host, port, app, threaded=True)
    except OSError as exc:
        if _port_is_listening(port):
            _reclaim_stale_web_port(port)
            server = make_server(host, port, app, threaded=True)
        else:
            raise click.ClickException(str(exc)) from exc

    stop_event = threading.Event()
    stop_reason: dict[str, str | None] = {"value": None}

    def request_stop(reason: str) -> None:
        if stop_event.is_set():
            return
        stop_reason["value"] = reason
        stop_event.set()

    app.extensions.setdefault("opentraces_web_runtime", {})["request_stop"] = request_stop
    lifecycle = app.extensions.get("opentraces_web_lifecycle") or {}
    snapshot = lifecycle.get("snapshot")

    server_thread = threading.Thread(
        target=server.serve_forever,
        name="opentraces-web-server",
        daemon=True,
    )
    server_thread.start()

    if callable(snapshot):
        def idle_monitor() -> None:
            while not stop_event.wait(5.0):
                state = snapshot(stale_after=45.0)
                if state.get("seen_any_client") and state.get("active_clients", 0) == 0:
                    request_stop("browser disconnected")
                    return

        threading.Thread(
            target=idle_monitor,
            name="opentraces-web-idle-monitor",
            daemon=True,
        ).start()

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def _handle_signal(signum, _frame) -> None:
        try:
            signame = signal.Signals(signum).name
        except ValueError:
            signame = str(signum)
        request_stop(f"signal {signame}")

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        while not stop_event.wait(0.5):
            pass
    except KeyboardInterrupt:
        request_stop("keyboard interrupt")
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5.0)

    return stop_reason["value"]


def _launch_web_ui(port: int = 5050, open_browser: bool = False) -> None:
    del port
    del open_browser
    click.echo(
        "The legacy web review client is decommissioned for now. "
        "Use `opentraces dataset review <name> --json`.",
        err=True,
    )
    sys.exit(2)


def _schedule_browser_open(url: str) -> None:
    try:
        import threading as _threading
        import webbrowser

        timer = _threading.Timer(0.6, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    except Exception as e:
        logger.debug("Could not schedule browser open: %s", e)

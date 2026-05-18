"""OTLP HTTP/JSON receiver (plan 078, R1).

Stdlib ``ThreadingHTTPServer`` exposing ``/v1/{traces,logs,metrics}``
plus ``GET /health``. Accepts ``application/json`` only; protobuf is an
explicit non-goal for v1. Per-request envelopes go to a registered
capture callback (CLI layer wires the real mapper+emitter fan-out);
the default callback only fills a bounded in-memory ring buffer for
``--foreground`` debugging.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional

LOGGER = logging.getLogger("opentraces.otlp.receiver")

_DEFAULT_PORT = 4318
_DEFAULT_BIND = "127.0.0.1"
_RING_BUFFER_SIZE = 2000
_PROTOCOL_HINT = (
    "{\"error\": \"unsupported content-type; "
    "set OTEL_EXPORTER_OTLP_PROTOCOL=http/json\"}"
)
_OTLP_SIGNAL_PATHS = {"/v1/traces", "/v1/logs", "/v1/metrics"}

CaptureCallback = Callable[[dict], None]


@dataclass
class CaptureEnvelope:
    """One accepted OTLP request, parsed into a structured envelope."""
    received_at: float
    signal: str  # "traces" | "logs" | "metrics"
    path: str
    body: dict
    raw_size: int


@dataclass
class _ReceiverState:
    """Shared mutable state held by the receiver thread."""
    lock: threading.Lock = field(default_factory=threading.Lock)
    captures_total: int = 0
    last_capture_at: Optional[float] = None
    ring: deque = field(default_factory=lambda: deque(maxlen=_RING_BUFFER_SIZE))
    callback: Optional[CaptureCallback] = None
    raw_bodies_dir: Optional[Path] = None


class OTLPReceiver:
    """Handle returned by :func:`start_receiver`."""

    def __init__(
        self,
        server: ThreadingHTTPServer,
        thread: threading.Thread,
        state: _ReceiverState,
        bind: str,
        port: int,
        started_at: float,
    ) -> None:
        self._server = server
        self._thread = thread
        self._state = state
        self.bind = bind
        self.port = port
        self._started_at = started_at
        self._shutdown_called = False

    @property
    def uptime_seconds(self) -> float:
        return max(0.0, time.time() - self._started_at)

    @property
    def captures_total(self) -> int:
        with self._state.lock:
            return self._state.captures_total

    @property
    def last_capture_at(self) -> Optional[float]:
        with self._state.lock:
            return self._state.last_capture_at

    @property
    def raw_bodies_dir(self) -> Optional[Path]:
        return self._state.raw_bodies_dir

    def set_callback(self, callback: CaptureCallback) -> None:
        """Replace the default ring-only callback with the real fan-out."""
        with self._state.lock:
            self._state.callback = callback

    def recent_captures(self, limit: int = 50) -> list[CaptureEnvelope]:
        with self._state.lock:
            items = list(self._state.ring)
        return items[-limit:]

    def wait_until_ready(self, timeout: float = 5.0) -> bool:
        """Block until the bound socket is accepting connections."""
        import socket

        target = "127.0.0.1" if self.bind == "0.0.0.0" else self.bind
        deadline = time.time() + timeout
        while time.time() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.2)
                try:
                    probe.connect((target, self.port))
                except OSError:
                    time.sleep(0.05)
                    continue
                return True
        return False

    def shutdown(self) -> None:
        if self._shutdown_called:
            return
        self._shutdown_called = True
        try:
            self._server.shutdown()
        finally:
            self._server.server_close()
            self._thread.join(timeout=5.0)


def _make_handler(state: _ReceiverState) -> type[BaseHTTPRequestHandler]:
    """Build a request handler closed over the shared receiver state."""

    class _OTLPHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return  # silence the stdlib access log; we use our own logger

        def _reply(self, status: int, body: bytes, content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if self.path == "/health":
                self._reply(200, b"{\"status\": \"ok\"}")
                return
            self._reply(405, b"{\"error\": \"method not allowed\"}")

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            path = self.path.split("?", 1)[0]
            if path not in _OTLP_SIGNAL_PATHS:
                self._reply(404, b"{\"error\": \"unknown signal endpoint\"}")
                return
            content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip()
            if content_type != "application/json":
                self._reply(415, _PROTOCOL_HINT.encode("utf-8"))
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                LOGGER.debug("otlp body decode failed signal=%s size=%d err=%s", path, length, exc)
                self._reply(400, b"{\"error\": \"invalid JSON body\"}")
                return
            envelope = CaptureEnvelope(
                received_at=time.time(),
                signal=path.removeprefix("/v1/"),
                path=path,
                body=body if isinstance(body, dict) else {"_root": body},
                raw_size=length,
            )
            self._record(envelope)
            self._reply(200, b"{}")

        def _record(self, envelope: CaptureEnvelope) -> None:
            with state.lock:
                state.captures_total += 1
                state.last_capture_at = envelope.received_at
                state.ring.append(envelope)
                cb = state.callback
            LOGGER.debug(
                "otlp capture signal=%s size=%d total=%d",
                envelope.signal,
                envelope.raw_size,
                state.captures_total,
            )
            if cb is None:
                return
            try:
                cb({
                    "received_at": envelope.received_at,
                    "signal": envelope.signal,
                    "path": envelope.path,
                    "body": envelope.body,
                    "raw_size": envelope.raw_size,
                })
            except Exception:  # noqa: BLE001
                LOGGER.exception("otlp capture callback raised")

    return _OTLPHandler


def start_receiver(
    port: int = _DEFAULT_PORT,
    bind: str = _DEFAULT_BIND,
    raw_bodies_dir: Optional[Path] = None,
) -> OTLPReceiver:
    """Spin up an OTLP receiver in a background thread; use
    :meth:`OTLPReceiver.wait_until_ready` to block on socket readiness."""
    if bind != _DEFAULT_BIND:
        LOGGER.warning("otlp receiver binding non-loopback bind=%s", bind)
    state = _ReceiverState(raw_bodies_dir=Path(raw_bodies_dir) if raw_bodies_dir else None)
    server = ThreadingHTTPServer((bind, port), _make_handler(state))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name=f"otlp-receiver-{port}", daemon=True)
    started_at = time.time()
    thread.start()
    receiver = OTLPReceiver(server=server, thread=thread, state=state, bind=bind, port=port, started_at=started_at)
    # SIGTERM/SIGINT only installable from the main thread; ignore otherwise.
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, lambda _s, _f, _r=receiver: _r.shutdown())
        except (ValueError, OSError):
            LOGGER.debug("otlp receiver could not install handler for signal=%s", sig)
    LOGGER.info("otlp receiver listening on http://%s:%d", bind, port)
    return receiver

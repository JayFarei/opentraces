"""Minimal stdlib HTTP proxy that captures every Anthropic API request.

Listens on ``127.0.0.1:<port>``, accepts POSTs to ``/v1/messages``,
forwards them to a configured upstream (defaults to the local fake
backend), and writes one JSONL line per request/response pair to
``capture_log_path``. Pure stdlib, no async runtime, no MITM TLS.

This is the substrate the prototype uses to observe the bytes Claude
Code would send to ``api.anthropic.com``. Client code points
``ANTHROPIC_BASE_URL`` (or equivalent) at this proxy and gets back the
upstream response untouched while a side-channel log accumulates the
full request/response/header set for later replay into the Context
Tree event log.

Why pure stdlib instead of LiteLLM or a Starlette app:
- Zero install footprint beyond what opentraces already ships.
- One process, one port, one log file, one ~150-line module — trivial
  for the prototype's comparison loop to spin up / tear down.
- LiteLLM's value is in normalising provider differences and adding
  rate-limit/cost tooling. Neither applies to "passively log every
  byte you forward"; for the production version those concerns return
  and LiteLLM's logging hooks (or a dedicated MITM proxy like
  mitmproxy / Helicone's load-balancer image) become attractive again.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ProxyConfig:
    """Runtime configuration for ``run_proxy``."""

    capture_log_path: Path
    upstream_base_url: str = "http://127.0.0.1:9876"  # default: fake backend
    listen_host: str = "127.0.0.1"
    listen_port: int = 4000
    # Extra fields ``OT-Property-*`` request headers should be lifted into
    # under ``request.ot_properties`` in the capture log. Matches the
    # Helicone custom-properties convention (see plan 078 brief).
    property_header_prefix: str = "OT-Property-"
    # Request timeout when forwarding to the upstream backend.
    upstream_timeout_s: float = 30.0
    # Optional async-mode hook: when set, the response is forwarded back
    # to the client without waiting for the log write to succeed. v1
    # uses sync mode for determinism; the field is wired for future
    # experimentation, not used in the prototype journey.
    async_log: bool = False
    # Optional hook for tests: called with the capture-log record after
    # each request. Receives the dict so tests can assert on shape
    # without re-reading the log file.
    on_capture: Callable[[dict[str, Any]], None] | None = None


@dataclass
class ProxyHandle:
    """Returned from ``run_proxy`` so callers can shut the proxy down."""

    server: ThreadingHTTPServer
    thread: threading.Thread
    config: ProxyConfig
    port: int
    capture_log_path: Path

    def stop(self) -> None:
        try:
            self.server.shutdown()
        except Exception:  # noqa: BLE001
            pass
        self.server.server_close()
        if self.thread.is_alive():
            self.thread.join(timeout=2.0)


# --------------------------------------------------------------------------- #
# Handler
# --------------------------------------------------------------------------- #


def _make_handler(config: ProxyConfig) -> type[BaseHTTPRequestHandler]:
    """Build a request handler class bound to a single ProxyConfig.

    Using a closure (rather than subclassing) keeps the handler purely
    stateless aside from the captured config; the ThreadingHTTPServer
    instantiates a fresh handler per request.
    """

    capture_log_path = config.capture_log_path
    capture_log_path.parent.mkdir(parents=True, exist_ok=True)
    capture_lock = threading.Lock()
    sequence = [0]  # mutable counter, shared across threads

    upstream_client = httpx.Client(
        base_url=config.upstream_base_url,
        timeout=config.upstream_timeout_s,
    )

    class ProxyHandler(BaseHTTPRequestHandler):
        # Suppress the noisy default-stderr access log; we keep our own.
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            logger.debug("proxy http: " + format, *args)

        def _read_body(self) -> bytes:
            length_header = self.headers.get("Content-Length", "0")
            try:
                length = int(length_header)
            except ValueError:
                length = 0
            if length <= 0:
                return b""
            return self.rfile.read(length)

        def _collect_ot_properties(self) -> dict[str, str]:
            prefix = config.property_header_prefix
            return {
                key[len(prefix):]: value
                for key, value in self.headers.items()
                if key.startswith(prefix)
            }

        def _forward(self, body: bytes) -> tuple[int, dict[str, str], bytes]:
            # Build the outbound header set: pass through everything
            # except hop-by-hop headers and our own ``OT-Property-*``
            # convention (which the upstream doesn't need to see).
            outbound_headers: dict[str, str] = {}
            prefix = config.property_header_prefix
            hop_by_hop = {
                "host", "content-length", "connection",
                "keep-alive", "transfer-encoding",
            }
            for key, value in self.headers.items():
                lkey = key.lower()
                if lkey in hop_by_hop:
                    continue
                if key.startswith(prefix):
                    continue
                outbound_headers[key] = value
            response = upstream_client.post(
                self.path,
                content=body,
                headers=outbound_headers,
            )
            return (
                response.status_code,
                dict(response.headers),
                response.content,
            )

        def _write_capture(
            self,
            *,
            seq: int,
            method: str,
            path: str,
            request_headers: dict[str, str],
            ot_properties: dict[str, str],
            request_body: bytes,
            status_code: int,
            response_headers: dict[str, str],
            response_body: bytes,
            latency_ms: float,
            error: str | None,
        ) -> None:
            record = {
                "sequence": seq,
                "captured_at": _utc_now(),
                "method": method,
                "path": path,
                "request_headers": _redact_auth(request_headers),
                "request_body": _safe_json_decode(request_body),
                "request_body_bytes": len(request_body),
                "ot_properties": ot_properties,
                "status_code": status_code,
                "response_headers": dict(response_headers),
                "response_body": _safe_json_decode(response_body),
                "response_body_bytes": len(response_body),
                "latency_ms": latency_ms,
                "error": error,
            }
            line = json.dumps(record, sort_keys=True, ensure_ascii=False)
            with capture_lock:
                with capture_log_path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            if config.on_capture is not None:
                try:
                    config.on_capture(record)
                except Exception:  # noqa: BLE001
                    logger.warning("on_capture hook raised", exc_info=True)

        def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
            body = self._read_body()
            with capture_lock:
                sequence[0] += 1
                seq = sequence[0]
            method = self.command
            path = self.path
            request_headers = dict(self.headers.items())
            ot_properties = self._collect_ot_properties()
            start = time.perf_counter()
            status_code = 500
            response_headers: dict[str, str] = {}
            response_body = b""
            error: str | None = None
            try:
                status_code, response_headers, response_body = self._forward(body)
            except Exception as exc:  # noqa: BLE001
                error = repr(exc)
                response_body = json.dumps(
                    {"error": {"type": "proxy_upstream_error", "message": error}}
                ).encode("utf-8")
                response_headers = {"Content-Type": "application/json"}
            latency_ms = (time.perf_counter() - start) * 1000.0

            # Forward response to client.
            self.send_response(status_code)
            for key, value in response_headers.items():
                if key.lower() in {"transfer-encoding", "content-length", "connection"}:
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

            self._write_capture(
                seq=seq,
                method=method,
                path=path,
                request_headers=request_headers,
                ot_properties=ot_properties,
                request_body=body,
                status_code=status_code,
                response_headers=response_headers,
                response_body=response_body,
                latency_ms=latency_ms,
                error=error,
            )

        def do_GET(self) -> None:  # noqa: N802
            # Tiny health endpoint; never logged (no LLM payload).
            if self.path == "/healthz":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"ok")
                return
            self.send_response(404)
            self.end_headers()

    return ProxyHandler


def _redact_auth(headers: dict[str, str]) -> dict[str, str]:
    """Replace bearer/api-key header values with a sha256 fingerprint.

    The prototype must not write real credentials to the capture log,
    even on local-only runs, because the log is later compared against
    the JSONL capture and the diff machinery is not security-aware.
    """
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        lkey = key.lower()
        if lkey in {"authorization", "x-api-key", "anthropic-api-key"}:
            redacted[key] = f"redacted:len={len(value)}"
        else:
            redacted[key] = value
    return redacted


def _safe_json_decode(body: bytes) -> Any:
    """Try to decode a body as JSON; fall back to a stringified preview."""
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"_binary_preview_b64_len": len(body)}


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #


def run_proxy(config: ProxyConfig) -> ProxyHandle:
    """Start the proxy in a background thread and return a handle."""
    handler_cls = _make_handler(config)
    server = ThreadingHTTPServer((config.listen_host, config.listen_port), handler_cls)
    bound_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return ProxyHandle(
        server=server,
        thread=thread,
        config=config,
        port=bound_port,
        capture_log_path=config.capture_log_path,
    )

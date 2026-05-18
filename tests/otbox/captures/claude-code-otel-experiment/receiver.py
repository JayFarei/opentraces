"""Minimal OTLP HTTP/JSON receiver for the Claude Code OTel emission experiment.

Listens on :4318 and dumps every POST body it receives to
``raw-otel-emissions.jsonl`` as one JSON line per request with a thin
envelope describing which signal endpoint it landed on, when, and what
content type was used.

This is intentionally not a real OTel collector. It only needs to accept
the bytes Claude Code sends, write them to disk, and respond 200 so the
client thinks the export succeeded.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock


OUTPUT_PATH = Path(
    os.environ.get(
        "OTEL_RECEIVER_OUTPUT",
        str(Path(__file__).parent / "raw-otel-emissions.jsonl"),
    )
)
PORT = int(os.environ.get("OTEL_RECEIVER_PORT", "4318"))
WRITE_LOCK = Lock()


def _write_envelope(envelope: dict) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(envelope, ensure_ascii=False, sort_keys=True)
    with WRITE_LOCK:
        with OUTPUT_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")


def _decode_body(content_type: str, raw: bytes) -> object:
    """Best-effort body decode. Returns parsed JSON when possible, else hex preview."""
    if content_type.startswith("application/json"):
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:  # pragma: no cover - defensive
            return {"_decode_error": f"json: {exc}", "_raw_preview_hex": raw[:64].hex()}
    if content_type.startswith("application/x-protobuf"):
        return {
            "_format": "protobuf",
            "_note": "receiver does not decode protobuf; rerun with OTEL_EXPORTER_OTLP_PROTOCOL=http/json",
            "_raw_bytes": len(raw),
            "_raw_preview_hex": raw[:128].hex(),
        }
    return {
        "_format": "unknown",
        "_content_type": content_type,
        "_raw_bytes": len(raw),
        "_raw_preview_hex": raw[:64].hex(),
    }


class OTLPHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server API
        signal_kind = self.path.strip("/")  # e.g. "v1/traces"
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length > 0 else b""
        decoded = _decode_body(content_type, body)

        envelope = {
            "received_at": time.time(),
            "path": self.path,
            "signal": signal_kind,
            "content_type": content_type,
            "content_length": length,
            "headers": {k: v for k, v in self.headers.items() if k.lower() not in {"authorization"}},
            "body": decoded,
        }
        _write_envelope(envelope)

        # Respond with whatever the OTLP/HTTP spec considers a success.
        # OTLP/HTTP expects an empty ExportResponse for success.
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def do_GET(self):  # noqa: N802
        # Health check / liveness ping.
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"otlp-receiver-ok\n")

    def log_message(self, format, *args):  # noqa: A002 - silence default access log
        return


def main() -> int:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Truncate at start of run for a clean capture window.
    OUTPUT_PATH.write_text("", encoding="utf-8")

    server = ThreadingHTTPServer(("127.0.0.1", PORT), OTLPHandler)

    def _shutdown(*_: object) -> None:
        server.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    print(f"otlp-receiver listening on http://127.0.0.1:{PORT}", flush=True)
    print(f"  writing to {OUTPUT_PATH}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

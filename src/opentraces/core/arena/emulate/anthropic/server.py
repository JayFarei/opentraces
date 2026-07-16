"""Small deterministic Anthropic Messages API sidecar for bench replay."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


SCRIPT_SCHEMA = "opentraces.anthropic-replay-script.v0"
CONTRACT_VERSION = "anthropic-messages-replay.v0"


def _load_script() -> dict[str, Any]:
    value = json.loads(Path(os.environ["SCRIPT_PATH"]).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != SCRIPT_SCHEMA:
        raise RuntimeError("Anthropic replay script has the wrong schema")
    responses = value.get("responses")
    if not isinstance(responses, list) or not responses:
        raise RuntimeError("Anthropic replay script requires responses")
    if not all(isinstance(response, dict) for response in responses):
        raise RuntimeError("Anthropic replay responses must be objects")
    return value


SCRIPT = _load_script()
LEDGER_PATH = Path(os.environ["LEDGER_PATH"])
LEDGER_LOCK = threading.Lock()
SEQUENCE = 0


def _append_ledger(row: dict[str, Any]) -> None:
    encoded = json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
    with LEDGER_LOCK:
        with LEDGER_PATH.open("a", encoding="utf-8") as ledger:
            ledger.write(encoded)
            ledger.flush()
            os.fsync(ledger.fileno())


def _sse(response: dict[str, Any]) -> bytes:
    message = {
        "id": response["id"],
        "type": "message",
        "role": "assistant",
        "model": response["model"],
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 0},
    }
    events: list[tuple[str, dict[str, Any]]] = [
        ("message_start", {"type": "message_start", "message": message})
    ]
    for index, block in enumerate(response["content"]):
        if block.get("type") == "text":
            start = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": str(block.get("text") or "")}
        elif block.get("type") == "tool_use":
            start = {
                "type": "tool_use",
                "id": block["id"],
                "name": block["name"],
                "input": {},
            }
            delta = {
                "type": "input_json_delta",
                "partial_json": json.dumps(
                    block.get("input") or {}, sort_keys=True, separators=(",", ":")
                ),
            }
        else:
            raise RuntimeError(f"unsupported scripted content block: {block.get('type')!r}")
        events.extend(
            [
                (
                    "content_block_start",
                    {"type": "content_block_start", "index": index, "content_block": start},
                ),
                (
                    "content_block_delta",
                    {"type": "content_block_delta", "index": index, "delta": delta},
                ),
                (
                    "content_block_stop",
                    {"type": "content_block_stop", "index": index},
                ),
            ]
        )
    events.extend(
        [
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {
                        "stop_reason": response["stop_reason"],
                        "stop_sequence": None,
                    },
                    "usage": {"output_tokens": max(1, len(response["content"]))},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
    )
    return "".join(
        f"event: {name}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
        for name, payload in events
    ).encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "opentraces-anthropic-replay/0"

    def log_message(self, _format: str, *args: object) -> None:
        return None

    def _json(self, status: int, value: object) -> None:
        encoded = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/_emulate/manifest":
            self._json(
                200,
                {
                    "id": "anthropic-scripted",
                    "contract_version": CONTRACT_VERSION,
                    "launch": {
                        "nonce": os.environ["OPENTRACES_ANTHROPIC_LAUNCH_NONCE"],
                        "pid": os.getpid(),
                        "source_sha256": os.environ["OPENTRACES_ANTHROPIC_SOURCE_SHA256"],
                    },
                    "script": {
                        "schema_version": SCRIPT_SCHEMA,
                        "sha256": os.environ["OPENTRACES_ANTHROPIC_SCRIPT_SHA256"],
                        "response_count": len(SCRIPT["responses"]),
                    },
                    "runtime": {
                        "name": "python",
                        "version": os.sys.version.split()[0],
                    },
                },
            )
            return
        if path == "/_emulate/ledger":
            raw = LEDGER_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        global SEQUENCE

        path = urlsplit(self.path).path
        if path != "/v1/messages":
            self._json(404, {"error": {"type": "not_found_error"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": {"type": "invalid_request_error"}})
            return
        with LEDGER_LOCK:
            sequence = SEQUENCE + 1
            SEQUENCE = sequence
        responses = SCRIPT["responses"]
        if sequence > len(responses):
            response = {"error": {"type": "replay_exhausted", "sequence": sequence}}
            _append_ledger(
                {
                    "sequence": sequence,
                    "request": {"method": "POST", "path": path, "body": body},
                    "response": {"status": 409, "body": response},
                }
            )
            self._json(409, response)
            return
        response = responses[sequence - 1]
        _append_ledger(
            {
                "sequence": sequence,
                "request": {"method": "POST", "path": path, "body": body},
                "response": {"status": 200, "body": response},
            }
        )
        if body.get("stream") is False:
            self._json(200, response)
            return
        encoded = _sse(response)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.touch(exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()

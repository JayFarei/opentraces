"""Canned-response fake Anthropic backend for the proxy prototype.

Listens on ``127.0.0.1:9876``, accepts POSTs to ``/v1/messages``, and
returns a deterministic ``message`` envelope populated from the
*latest* user message in the request. The proxy forwards client traffic
here so the prototype never burns real API tokens and the comparison
report stays fully reproducible.

The backend can be driven in two modes:

1. ``echo`` (default): the response text echoes the last user message
   prefixed with ``"fake response: "``. Useful for stand-up smoke tests.
2. ``scripted``: callers seed a list of ``(prompt_contains, response_blocks)``
   pairs via ``BackendConfig.scripted_turns``; the backend walks the
   list in order, returning each entry once. Used by the prototype
   fixture so the captured turns mirror a realistic Claude Code session.

Both modes return an Anthropic-shaped ``message`` JSON body, including
``content`` blocks (text + optional ``tool_use``) and ``usage`` counts.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ScriptedTurn:
    """One scripted backend response.

    ``match_text`` is a substring searched in the most-recent user
    message's text. When matched, ``content_blocks`` is returned as
    the assistant message's content. ``stop_reason`` defaults to
    ``"end_turn"``.
    """

    match_text: str
    content_blocks: list[dict[str, Any]]
    stop_reason: str = "end_turn"
    model: str = "claude-sonnet-4-5"


@dataclass
class BackendConfig:
    listen_host: str = "127.0.0.1"
    listen_port: int = 9876
    mode: str = "echo"        # "echo" | "scripted"
    scripted_turns: list[ScriptedTurn] = field(default_factory=list)


@dataclass
class BackendHandle:
    server: ThreadingHTTPServer
    thread: threading.Thread
    config: BackendConfig
    port: int

    def stop(self) -> None:
        try:
            self.server.shutdown()
        except Exception:  # noqa: BLE001
            pass
        self.server.server_close()
        if self.thread.is_alive():
            self.thread.join(timeout=2.0)


def _make_handler(config: BackendConfig) -> type[BaseHTTPRequestHandler]:
    scripted_cursor = [0]  # mutable: index of next scripted turn to consume
    lock = threading.Lock()

    class FakeAnthropicHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            logger.debug("fake-anthropic http: " + format, *args)

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0") or "0")
            return self.rfile.read(length) if length > 0 else b""

        def _last_user_text(self, body_json: dict[str, Any]) -> str:
            """Pull the most recent user message's text out of the request."""
            messages = body_json.get("messages") or []
            for msg in reversed(messages):
                if msg.get("role") != "user":
                    continue
                content = msg.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts: list[str] = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text")
                            if isinstance(text, str):
                                parts.append(text)
                    return "\n".join(parts)
            return ""

        def _build_response(self, body_json: dict[str, Any]) -> dict[str, Any]:
            model = body_json.get("model") or "claude-sonnet-4-5"
            user_text = self._last_user_text(body_json)
            content_blocks: list[dict[str, Any]]
            stop_reason = "end_turn"

            if config.mode == "scripted":
                turn = _pick_scripted_turn(
                    config.scripted_turns, scripted_cursor, lock, user_text
                )
                if turn is not None:
                    content_blocks = list(turn.content_blocks)
                    stop_reason = turn.stop_reason
                    if turn.model:
                        model = turn.model
                else:
                    content_blocks = [
                        {"type": "text", "text": "fake response: (no scripted turn matched)"}
                    ]
            else:
                content_blocks = [
                    {"type": "text", "text": f"fake response: {user_text}"}
                ]

            # Coarse usage estimate so the response shape is realistic.
            input_chars = sum(
                len(_safe_text(m.get("content")))
                for m in body_json.get("messages") or []
            )
            output_chars = sum(
                len(_safe_text(b.get("text") if b.get("type") == "text" else ""))
                for b in content_blocks
            )
            return {
                "id": f"msg_fake_{uuid.uuid4().hex[:16]}",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": content_blocks,
                "stop_reason": stop_reason,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": max(1, input_chars // 4),
                    "output_tokens": max(1, output_chars // 4),
                },
            }

        def do_POST(self) -> None:  # noqa: N802
            body = self._read_body()
            try:
                body_json = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"type":"invalid_request","message":"bad json"}}')
                return
            response_body = self._build_response(body_json)
            payload = json.dumps(response_body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"ok")
                return
            self.send_response(404)
            self.end_headers()

    return FakeAnthropicHandler


def _pick_scripted_turn(
    turns: list[ScriptedTurn],
    cursor: list[int],
    lock: threading.Lock,
    user_text: str,
) -> ScriptedTurn | None:
    """Walk the scripted turns in order, matching on ``match_text``.

    Returns the first turn whose match_text appears in user_text, then
    advances the cursor past it. Skipped turns stay available for later
    matches. When nothing matches and cursor < len(turns), returns the
    cursor turn as a fallback (so a short, off-script user prompt doesn't
    derail the deterministic sequence).
    """
    with lock:
        for idx in range(cursor[0], len(turns)):
            turn = turns[idx]
            if turn.match_text and turn.match_text in user_text:
                cursor[0] = idx + 1
                return turn
        # Fallback: advance one slot if we still have turns left.
        if cursor[0] < len(turns):
            turn = turns[cursor[0]]
            cursor[0] += 1
            return turn
    return None


def _safe_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
                input_payload = block.get("input")
                if isinstance(input_payload, dict):
                    parts.append(json.dumps(input_payload, sort_keys=True))
        return "\n".join(parts)
    return ""


def run_fake_backend(config: BackendConfig) -> BackendHandle:
    handler_cls = _make_handler(config)
    server = ThreadingHTTPServer((config.listen_host, config.listen_port), handler_cls)
    bound_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return BackendHandle(
        server=server,
        thread=thread,
        config=config,
        port=bound_port,
    )

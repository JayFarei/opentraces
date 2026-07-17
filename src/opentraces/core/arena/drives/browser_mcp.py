"""Run-scoped MCP bridge from a real harness to its granted BrowserDrive."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Mapping

from .browser import BrowserDrive


SERVER_NAME = "opentraces_browser"
MAX_REQUEST_BYTES = 512 * 1024
REQUEST_READ_TIMEOUT_SECONDS = 0.25


_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "browser_navigate",
        "description": "Navigate the run-owned browser to a public URL.",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "browser_locate",
        "description": "Locate an element in the run-owned browser.",
        "inputSchema": {
            "type": "object",
            "properties": {"selector": {"type": "string"}},
            "required": ["selector"],
            "additionalProperties": False,
        },
    },
    {
        "name": "browser_click",
        "description": "Click an element in the run-owned browser.",
        "inputSchema": {
            "type": "object",
            "properties": {"selector": {"type": "string"}},
            "required": ["selector"],
            "additionalProperties": False,
        },
    },
    {
        "name": "browser_fill",
        "description": "Fill an element in the run-owned browser.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["selector", "value"],
            "additionalProperties": False,
        },
    },
    {
        "name": "browser_wait",
        "description": "Wait for browser state in the run-owned browser.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "state": {"type": "string"},
                "timeout_ms": {"type": "integer", "minimum": 1},
            },
            "required": ["selector"],
            "additionalProperties": False,
        },
    },
    {
        "name": "browser_inspect",
        "description": "Inspect rendered public state in the run-owned browser.",
        "inputSchema": {
            "type": "object",
            "properties": {"selector": {"type": "string"}},
            "required": ["selector"],
            "additionalProperties": False,
        },
    },
    {
        "name": "browser_screenshot",
        "description": "Retain a screenshot from the run-owned browser.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "full_page": {"type": "boolean"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
)


class BrowserMcpBridge:
    """Serve one exact BrowserDrive on host loopback for one harness attempt."""

    def __init__(self, browser: BrowserDrive) -> None:
        self.browser = browser
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def local_port(self) -> int:
        if self._server is None:
            raise RuntimeError("browser MCP bridge has not started")
        return int(self._server.server_address[1])

    @property
    def remote_port(self) -> int:
        # The SSH reverse forward binds this same ephemeral number in the box.
        return self.local_port

    def config(self) -> dict[str, Any]:
        return {
            "mcpServers": {
                SERVER_NAME: {
                    "type": "http",
                    "url": f"http://127.0.0.1:{self.remote_port}/mcp",
                }
            }
        }

    def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("browser MCP bridge already started")
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
                if self.path != "/mcp":
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > MAX_REQUEST_BYTES:
                        raise ValueError("invalid MCP request size")
                    self.connection.settimeout(REQUEST_READ_TIMEOUT_SECONDS)
                    body = self.rfile.read(length)
                    if len(body) != length:
                        raise ValueError("incomplete MCP request body")
                    request = json.loads(body)
                    response = bridge._respond(request)
                except (OSError, ValueError) as exc:
                    response = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32603, "message": type(exc).__name__},
                    }
                if response is None:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = json.dumps(response, sort_keys=True, separators=(",", ":")).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return None

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._serve,
            name="opentraces-browser-mcp",
            daemon=True,
        )
        self._thread.start()

    def _serve(self) -> None:
        assert self._server is not None
        try:
            self._server.serve_forever(poll_interval=0.05)
        finally:
            # Playwright's sync adapter is thread-affine. The MCP server thread
            # creates the browser session, so it also freezes and closes it.
            self.browser.finalize_recordings()

    def close(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server.server_close()
        self._server = None
        self._thread = None

    def _respond(self, request: Mapping[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method")
        if request_id is None:
            return None
        if method == "initialize":
            protocol = str((request.get("params") or {}).get("protocolVersion") or "2025-06-18")
            result = {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": "bench.v0"},
            }
        elif method == "tools/list":
            result = {"tools": list(_TOOLS)}
        elif method == "tools/call":
            params = request.get("params") or {}
            result = self._call_tool(
                str(params.get("name") or ""),
                params.get("arguments") or {},
            )
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "method not found"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _call_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        try:
            if name == "browser_navigate":
                observed = self.browser.navigate(str(arguments["url"]))
            elif name == "browser_locate":
                observed = self.browser.locate(str(arguments["selector"]))
            elif name == "browser_click":
                observed = self.browser.click(str(arguments["selector"]))
            elif name == "browser_fill":
                observed = self.browser.fill(str(arguments["selector"]), str(arguments["value"]))
            elif name == "browser_wait":
                observed = self.browser.wait(
                    str(arguments["selector"]),
                    state=str(arguments.get("state") or "visible"),
                    timeout_ms=int(arguments.get("timeout_ms") or 30_000),
                )
            elif name == "browser_inspect":
                observed = self.browser.inspect(str(arguments["selector"]))
            elif name == "browser_screenshot":
                observed = self.browser.screenshot(
                    str(arguments["name"]),
                    full_page=bool(arguments.get("full_page", False)),
                )
            else:
                raise ValueError("unknown run-owned browser tool")
            text = json.dumps(observed.state, sort_keys=True, separators=(",", ":"))
            return {"content": [{"type": "text", "text": text}], "isError": False}
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": type(exc).__name__}],
                "isError": True,
            }

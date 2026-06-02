"""convert-api fixture — the CONSUMED SERVICE axis (plan 089, U6).

Same bug as the humanduration library, but now behind an HTTP API the client
*consumes and does not control*: ``GET /convert?d=1h30m`` -> ``{"seconds": N}``.

  * deploy-v1 (buggy): drops the minutes  -> 3600
  * deploy-v2 (fixed): counts h/m/s        -> 5400

The "fix" is a server-side redeploy the client never sees; the client only reads
``CONVERT_API_URL``. Used by the local axis-B proof and the live Vercel deploy.
"""

from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

_UNIT = {"h": 3600, "m": 60, "s": 1}


def convert(spec: str, *, fixed: bool) -> int:
    """The convert logic. v1 (fixed=False) only counts hours; v2 counts h/m/s."""

    pattern = r"(\d+)([hms])" if fixed else r"(\d+)(h)"
    return sum(int(v) * _UNIT[u] for v, u in re.findall(pattern, spec))


def _handler(fixed: bool):
    class _H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            import json
            q = parse_qs(urlparse(self.path).query)
            spec = (q.get("d") or [""])[0]
            body = json.dumps({"seconds": convert(spec, fixed=fixed)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # silence
            pass

    return _H


class LocalConvertAPI:
    """A throwaway local deploy of convert-api at a given fidelity (v1/v2)."""

    def __init__(self, *, fixed: bool):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fixed))
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/convert"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()

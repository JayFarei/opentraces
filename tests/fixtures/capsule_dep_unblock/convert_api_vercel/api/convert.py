"""convert-api Vercel serverless function (plan 089, U6 live deploy).

GET /convert?d=1h30m -> {"seconds": N}. The same code serves both deploys; the
behaviour is switched by the CONVERT_FIXED deploy env var:

  * deploy-v1: CONVERT_FIXED unset/0 -> only hours counted -> 1h30m -> 3600 (bug)
  * deploy-v2: CONVERT_FIXED=1        -> h/m/s counted      -> 1h30m -> 5400 (fix)

The client consumes this via CONVERT_API_URL and never sees the fix — a server-side
redeploy unblocks it.
"""

import json
import os
import re
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

_UNIT = {"h": 3600, "m": 60, "s": 1}


def convert(spec: str) -> int:
    fixed = os.environ.get("CONVERT_FIXED") == "1"
    pattern = r"(\d+)([hms])" if fixed else r"(\d+)(h)"
    return sum(int(v) * _UNIT[u] for v, u in re.findall(pattern, spec))


class handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        spec = (parse_qs(urlparse(self.path).query).get("d") or [""])[0]
        body = json.dumps({
            "seconds": convert(spec),
            "version": "v2" if os.environ.get("CONVERT_FIXED") == "1" else "v1",
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

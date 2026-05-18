"""Watches Claude Code's ``OTEL_LOG_RAW_API_BODIES=file:<dir>`` drop dir.

Claude Code writes full Anthropic Messages API request/response JSON to
``<dir>/<request_id>.{request,response}.json`` (plan 078 evidence,
``tests/otbox/captures/claude-code-otel-experiment/emission-coverage.md``).
This watcher polls that dir, pairs each request file with its response, and
fires a callback once both are present and stable. Polling is intentional
(stdlib-only, cross-platform); default interval is 200 ms.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger("opentraces.otlp.raw_body_watcher")

# Files are "done writing" once mtime hasn't changed in this window. Claude
# Code body files run ~133 KB (gotcha #8), so we wait rather than parse on
# first sight.
_STABILITY_WINDOW_S = 0.2


class RawBodyWatcher:
    """Poll a raw-bodies dir; fire callback on each request/response pair."""

    def __init__(
        self,
        dir_path: Path,
        on_body_pair: Callable[[str, dict, dict], None],
        poll_interval_s: float = 0.2,
    ) -> None:
        self.dir_path = Path(dir_path)
        self._on_body_pair = on_body_pair
        self._poll_interval_s = poll_interval_s
        self._seen: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.pairs_seen = 0  # doctor surface (R11)

    @property
    def dir_size_bytes(self) -> int:
        """Total bytes on disk under the watched dir (R11 doctor surface)."""
        if not self.dir_path.exists():
            return 0
        total = 0
        for p in self.dir_path.glob("*"):
            try:
                total += p.stat().st_size
            except OSError:
                continue
        return total

    def start(self) -> None:
        """Create the dir if missing and start the background polling thread."""
        self.dir_path.mkdir(parents=True, exist_ok=True)
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="opentraces-raw-body-watcher", daemon=True
        )
        self._thread.start()

    def shutdown(self) -> None:
        """Signal the thread to stop and join it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._scan_once()
            except Exception:  # noqa: BLE001 - never let a scan crash the loop
                logger.exception("raw-body watcher scan failed; continuing")
            self._stop.wait(self._poll_interval_s)

    def _scan_once(self) -> None:
        now = time.time()
        for req_path in sorted(self.dir_path.glob("*.request.json")):
            request_id = req_path.name[: -len(".request.json")]
            if request_id in self._seen:
                continue
            resp_path = self.dir_path / f"{request_id}.response.json"
            if not resp_path.exists():
                continue
            if not (
                self._is_stable(req_path, now) and self._is_stable(resp_path, now)
            ):
                continue
            try:
                request_body = json.loads(req_path.read_text(encoding="utf-8"))
                response_body = json.loads(resp_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(
                    "skipping malformed body pair %s: %s", request_id, exc
                )
                self._seen.add(request_id)
                continue
            try:
                self._on_body_pair(request_id, request_body, response_body)
                self.pairs_seen += 1
            except Exception:  # noqa: BLE001 - callback errors must not crash watcher
                logger.exception(
                    "on_body_pair callback failed for request_id=%s", request_id
                )
            self._seen.add(request_id)

    @staticmethod
    def _is_stable(path: Path, now: float) -> bool:
        try:
            return (now - path.stat().st_mtime) >= _STABILITY_WINDOW_S
        except OSError:
            return False


def prune_dir_older_than(dir_path: Path, days: int) -> int:
    """Delete body files older than ``days``. Doctor/CLI invokes explicitly; the
    watcher itself does not auto-rotate (plan 078 gotcha #8)."""
    if not dir_path.exists():
        return 0
    cutoff = time.time() - (days * 86400)
    deleted = 0
    for p in dir_path.glob("*.json"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                deleted += 1
        except OSError:
            continue
    return deleted

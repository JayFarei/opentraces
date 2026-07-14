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

# An unpaired ``*.request.json`` older than this has almost certainly lost its
# pairing chance: on real Claude Code output responses are written under a
# ``req_<id>.response.json`` name that this filename pairer cannot match
# (plan-078 gap (i)), so the pair never completes. Once past the deadline we
# negative-cache the request_id so it permanently leaves the scan set instead
# of being re-stat'd on every 200ms poll forever (issue #251). The
# content-based #158 flush path (``capture-otlp flush --from-raw-bodies``) does
# not depend on this watcher's pairing, so nothing downstream is lost.
_PAIRING_DEADLINE_S = 3600.0


class RawBodyWatcher:
    """Poll a raw-bodies dir; fire callback on each request/response pair.

    Plan 080 §5 raw-body lifecycle (TTL sweep):
      * Default retention is ``delete``: the emitter unlinks the source
        pair on the success path immediately after events are appended.
        Because the filename pairing never completes on real Claude Code
        output (plan-078 gap (i)), the emitter mostly never sees a pair,
        so orphans accumulate. The orphan safety net is config-gated and
        DEFAULT OFF (issue #251): only when
        ``capture.otlp.raw_body_orphan_ttl_days`` is set does the periodic
        sweep delete orphan bodies older than that many days — an unset
        TTL keeps the corpus for retroactive ``flush --from-raw-bodies``.
      * ``keep_N_days``: the periodic sweep deletes pairs older than N
        days. The emitter does NOT delete on success.
      * ``keep_forever``: the sweep is a no-op.

    Per-poll cost (issue #251): a poll whose watched-dir mtime is
    unchanged short-circuits before globbing, and an unpaired request
    older than ``_PAIRING_DEADLINE_S`` is negative-cached so it leaves the
    scan set permanently instead of being re-stat'd on every poll.
    """

    # How often to walk the dir for retention sweep (cheap stat()-only
    # walk). 60s keeps it imperceptible for active dev boxes.
    _RETENTION_SWEEP_INTERVAL_S = 60.0

    def __init__(
        self,
        dir_path: Path,
        on_body_pair: Callable[[str, dict, dict], None],
        poll_interval_s: float = 0.2,
        retention: str | None = None,
        orphan_ttl_days: int | None = None,
    ) -> None:
        self.dir_path = Path(dir_path)
        self._on_body_pair = on_body_pair
        self._poll_interval_s = poll_interval_s
        self._retention = retention  # None => resolve from config at sweep time
        # None => resolve from config at sweep time; a resolved int gates the
        # orphan sweep under ``delete`` retention (issue #251), default OFF.
        self._orphan_ttl_days = orphan_ttl_days
        self._seen: set[str] = set()
        # mtime of the watched dir at the end of the last scan; a poll whose
        # dir mtime is unchanged short-circuits the whole scan (issue #251).
        # ``None`` forces the very first scan to always run.
        self._last_dir_mtime: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_retention_sweep_at: float = 0.0
        self.pairs_seen = 0  # doctor surface (R11)
        self.retention_swept_files = 0  # cumulative file-delete count
        self.stale_skipped = 0  # doctor surface: negative-cached unpaired reqs

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

    def drain(self) -> None:
        """Synchronously consume any body pairs left after ingress stops."""

        self._scan_once()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._scan_once()
            except Exception:  # noqa: BLE001 - never let a scan crash the loop
                logger.exception("raw-body watcher scan failed; continuing")
            now = time.time()
            if (now - self._last_retention_sweep_at) >= self._RETENTION_SWEEP_INTERVAL_S:
                try:
                    self._sweep_retention(now=now)
                except Exception:  # noqa: BLE001
                    logger.exception("retention sweep failed; continuing")
                self._last_retention_sweep_at = now
            self._stop.wait(self._poll_interval_s)

    # ---- retention sweep ------------------------------------------------- #

    def _sweep_retention(self, *, now: float | None = None) -> int:
        """Delete bodies per the configured retention policy.

        Returns the number of files removed in THIS sweep. ``keep_forever``
        is always a no-op. ``keep_N_days`` walks the dir and removes pairs
        older than N days based on mtime.

        ``delete`` is a no-op UNLESS an orphan TTL is configured (issue
        #251). Under ``delete`` the emitter unlinks pairs on the success
        path, but on real Claude Code output the watcher's filename pairing
        never succeeds (plan-078 gap (i)), so orphan bodies accumulate
        unbounded. The orphan TTL is the safety net the class docstring
        already promises: when set, orphans older than N days are swept.
        It is DEFAULT OFF — an unset TTL keeps today's no-op behaviour so a
        user's captured corpus stays available for retroactive
        ``flush --from-raw-bodies``.
        """
        policy = self._resolve_retention()
        if policy == "keep_forever":
            return 0
        if policy == "delete":
            ttl_days = self._resolve_orphan_ttl_days()
            if ttl_days is None:
                return 0
            removed = prune_dir_older_than(self.dir_path, ttl_days, now=now)
            self.retention_swept_files += removed
            if removed:
                logger.info(
                    "raw-body orphan sweep: deleted %d orphan files older than "
                    "%d days under %s",
                    removed, ttl_days, self.dir_path,
                )
            return removed
        days = _parse_keep_n_days(policy)
        if days is None:
            return 0
        removed = prune_dir_older_than(self.dir_path, days, now=now)
        self.retention_swept_files += removed
        if removed:
            logger.info(
                "raw-body retention sweep: deleted %d files older than %d days under %s",
                removed, days, self.dir_path,
            )
        return removed

    def _resolve_retention(self) -> str:
        """Return the retention policy string. Falls back to config or "delete"."""
        if self._retention:
            return self._retention
        try:
            from ...core.config import (
                get_capture_otlp_raw_body_retention,
                load_config,
            )
        except Exception:
            return "delete"
        try:
            return get_capture_otlp_raw_body_retention(load_config())
        except Exception:
            return "delete"

    def _resolve_orphan_ttl_days(self) -> int | None:
        """Return the orphan-TTL day count, or None if unset (sweep OFF).

        An explicit constructor value wins; otherwise resolve from config.
        Any failure degrades to None (no sweep) — the data-safe default:
        the watcher never deletes a user's corpus unless they asked for it.
        """
        if self._orphan_ttl_days is not None:
            return self._orphan_ttl_days
        try:
            from ...core.config import (
                get_capture_otlp_raw_body_orphan_ttl_days,
                load_config,
            )
        except Exception:
            return None
        try:
            return get_capture_otlp_raw_body_orphan_ttl_days(load_config())
        except Exception:
            return None

    def _scan_once(self, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        # Dir-mtime short-circuit: on APFS (and every fs we target) a
        # directory's mtime advances when entries are added or removed, so an
        # unchanged mtime means no new bodies landed since the last scan and
        # the whole glob is wasted work. The first scan (``_last_dir_mtime is
        # None``) always runs; any stat failure falls through to scanning.
        try:
            dir_mtime = self.dir_path.stat().st_mtime
        except OSError:
            dir_mtime = None
        if (
            dir_mtime is not None
            and self._last_dir_mtime is not None
            and dir_mtime == self._last_dir_mtime
        ):
            return
        # A present pair that fails the stability window is deferred to a
        # later poll. If that happens and NO other file lands, the dir mtime
        # never changes, so recording it here would let the short-circuit
        # swallow the pair forever (the last pair of an idle session would
        # never fire). Track the deferral and suppress the mtime record so
        # the next poll rescans, matching the old always-retry behaviour.
        deferred_unstable = False
        for req_path in sorted(self.dir_path.glob("*.request.json")):
            request_id = req_path.name[: -len(".request.json")]
            if request_id in self._seen:
                continue
            resp_path = self.dir_path / f"{request_id}.response.json"
            if not resp_path.exists():
                # No response yet. If the request has sat unpaired past the
                # deadline its pairing will never complete (plan-078 gap (i)),
                # so negative-cache it: it permanently leaves the scan set
                # instead of being re-stat'd on every poll forever (#251).
                if self._is_stale_unpaired(req_path, now):
                    self._seen.add(request_id)
                    self.stale_skipped += 1
                continue
            if not (
                self._is_stable(req_path, now) and self._is_stable(resp_path, now)
            ):
                deferred_unstable = True
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
        # Remember the dir mtime we just scanned so the next unchanged poll
        # short-circuits. Recorded after the walk (not before) so a body that
        # lands mid-scan still bumps the mtime and forces a re-scan next poll.
        # Skipped when a pair was deferred for instability (see above): that
        # pair needs a rescan the dir mtime alone would not trigger.
        if dir_mtime is not None and not deferred_unstable:
            self._last_dir_mtime = dir_mtime

    @staticmethod
    def _is_stale_unpaired(path: Path, now: float) -> bool:
        """True if an unpaired request file is older than the pairing deadline."""
        try:
            return (now - path.stat().st_mtime) >= _PAIRING_DEADLINE_S
        except OSError:
            return False

    @staticmethod
    def _is_stable(path: Path, now: float) -> bool:
        try:
            return (now - path.stat().st_mtime) >= _STABILITY_WINDOW_S
        except OSError:
            return False


def prune_dir_older_than(
    dir_path: Path, days: int, *, now: float | None = None
) -> int:
    """Delete body files older than ``days``. Plan 080 §5 TTL sweep.

    Called by the watcher's periodic retention sweep when the
    configured policy is ``keep_N_days``, and still callable
    explicitly from CLI / doctor surfaces. ``now`` parameter is for
    deterministic tests.
    """
    if not dir_path.exists():
        return 0
    cutoff = (time.time() if now is None else now) - (days * 86400)
    deleted = 0
    for p in dir_path.glob("*.json"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                deleted += 1
        except OSError:
            continue
    return deleted


def _parse_keep_n_days(retention: str) -> int | None:
    """Parse ``keep_N_days`` -> N. Returns None for non-matching literals.

    Mirrors :func:`opentraces.core.config.parse_raw_body_retention_days`
    but does not raise on malformed values — the watcher's sweep
    silently degrades to "no-op" rather than crashing the daemon
    thread. The CLI / config path is where the user is told their
    setting is invalid.
    """
    if not retention.startswith("keep_") or not retention.endswith("_days"):
        return None
    middle = retention[len("keep_"): -len("_days")]
    try:
        n = int(middle)
    except ValueError:
        logger.warning("invalid raw_body_retention literal %r; ignoring", retention)
        return None
    if n < 1:
        logger.warning("raw_body_retention %r has N<1; ignoring", retention)
        return None
    return n

"""Generic, race-safe dirty-marker primitive for read-model freshness.

A *dirty marker* is a tiny JSON file that an out-of-band writer drops to signal
"a derived read-model is now potentially stale; rebuild before trusting it".
A reader compares the current marker token against the token it built from; a
maintainer clears the marker atomically iff it still holds the token it saw
(so a concurrent re-mark is never lost).

This module is the single source of truth for that compare-and-clear dance,
extracted from the original read-only trace-search-snapshot marker (issue #27 E)
so the bucket manifest read-model can reuse the same battle-tested logic instead
of re-deriving it. ``trace_search_state`` and ``bucket_manifest_state`` are both
thin wrappers over a :class:`DirtyMarker` instance.

The token is the marker file's full text content (not just the ``token``
field): ``current_token`` returns it verbatim and ``clear_if_unchanged``
compares it verbatim, so the two always agree on identity.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DirtyMarker:
    """A single race-safe dirty-marker file.

    Parameters
    ----------
    path_fn:
        A zero-arg callable returning the marker :class:`~pathlib.Path`. Passed
        as a callable (not a fixed Path) so the resolved location honours any
        runtime ``OPENTRACES_DIR`` override at call time, matching the rest of
        the codebase's path discipline.
    schema_version:
        The ``schema_version`` string stamped into the marker payload.
    """

    def __init__(self, path_fn: Callable[[], Path], schema_version: str) -> None:
        self._path_fn = path_fn
        self._schema_version = schema_version

    def path(self) -> Path:
        return self._path_fn()

    def current_token(self) -> str | None:
        path = self.path()
        try:
            return path.read_text(encoding="utf-8") if path.exists() else None
        except OSError:
            return "__unreadable__"

    def mark(self, reason: str, *, trace_id: str | None = None) -> None:
        path = self.path()
        payload: dict[str, Any] = {
            "schema_version": self._schema_version,
            "reason": reason,
            "trace_id": trace_id,
            "marked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "token": str(time.time_ns()),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def clear_if_unchanged(self, token: str | None) -> None:
        """Delete the marker iff it still holds ``token`` — atomically.

        The naive read-then-unlink races a concurrent :meth:`mark` (which
        ``os.replace``-es a new marker into place): if the replace lands between
        our read and our unlink, we would delete a *fresh* marker and lose a
        rebuild signal. Instead we ``os.rename`` the marker to a unique temp path
        first — rename is atomic, so a concurrent ``os.replace`` then re-creates
        the original path and that new marker is preserved. We then inspect the
        renamed snapshot we captured: if it matches our token we unlink it (the
        clear is valid). If it does not match, the file we grabbed was already a
        newer marker, so we restore it only when nothing has since reclaimed the
        original path, otherwise we drop our stale copy and let the concurrent
        writer's marker win.
        """

        if token is None:
            return
        path = self.path()
        tmp = path.with_name(f"{path.name}.clearing.{os.getpid()}.{time.time_ns()}")
        try:
            os.rename(path, tmp)
        except OSError:
            # No marker to clear (FileNotFoundError), or rename refused — either
            # way there is nothing for us to safely delete.
            return
        try:
            captured = tmp.read_text(encoding="utf-8")
        except OSError:
            # Unreadable snapshot: do not silently destroy a signal we cannot
            # vet. Restore it if the original slot is free, else discard our copy.
            self._restore_or_discard(tmp, path)
            return
        if captured == token:
            try:
                tmp.unlink()
            except OSError:
                pass
            return
        # The file we grabbed was a newer marker (a concurrent mark wrote it
        # before our rename, or os.replace re-created the path and we lost the
        # race on content). Put it back if the slot is still empty; otherwise the
        # concurrent writer already installed an even-newer marker and wins, so
        # we just drop our stale copy.
        self._restore_or_discard(tmp, path)

    @staticmethod
    def _restore_or_discard(tmp: Path, path: Path) -> None:
        try:
            # link() is atomic and fails (FileExistsError) if a concurrent writer
            # has already reclaimed the original path — in which case that newer
            # marker wins and we only need to drop our temp copy.
            os.link(tmp, path)
        except OSError:
            pass
        try:
            tmp.unlink()
        except OSError:
            pass

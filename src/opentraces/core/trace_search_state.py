"""Small state markers for the read-only trace search snapshot."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import paths


def dirty_marker_path() -> Path:
    return paths.OPENTRACES_DIR / "index" / "search_snapshot.dirty.json"


def current_dirty_token() -> str | None:
    path = dirty_marker_path()
    try:
        return path.read_text(encoding="utf-8") if path.exists() else None
    except OSError:
        return "__unreadable__"


def mark_search_snapshot_dirty(reason: str, *, trace_id: str | None = None) -> None:
    path = dirty_marker_path()
    payload: dict[str, Any] = {
        "schema_version": "opentraces.trace_search_snapshot_dirty.v1",
        "reason": reason,
        "trace_id": trace_id,
        "marked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "token": str(time.time_ns()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def clear_dirty_marker_if_unchanged(token: str | None) -> None:
    """Delete the dirty marker iff it still holds ``token`` — atomically.

    The naive read-then-unlink races a concurrent ``mark_search_snapshot_dirty``
    (which ``os.replace``-es a new marker into place): if the replace lands
    between our read and our unlink, we would delete a *fresh* marker and lose a
    rebuild signal. Instead we ``os.rename`` the marker to a unique temp path
    first — rename is atomic, so a concurrent ``os.replace`` then re-creates the
    original path and that new marker is preserved. We then inspect the renamed
    snapshot we captured: if it matches our token we unlink it (the clear is
    valid). If it does not match, the file we grabbed was already a newer marker,
    so we restore it only when nothing has since reclaimed the original path,
    otherwise we drop our stale copy and let the concurrent writer's marker win.
    """

    if token is None:
        return
    path = dirty_marker_path()
    tmp = path.with_name(f"{path.name}.clearing.{os.getpid()}.{time.time_ns()}")
    try:
        os.rename(path, tmp)
    except OSError:
        # No marker to clear (FileNotFoundError), or rename refused — either way
        # there is nothing for us to safely delete.
        return
    try:
        captured = tmp.read_text(encoding="utf-8")
    except OSError:
        # Unreadable snapshot: do not silently destroy a signal we cannot vet.
        # Restore it if the original slot is free, else discard our copy.
        _restore_or_discard(tmp, path)
        return
    if captured == token:
        try:
            tmp.unlink()
        except OSError:
            pass
        return
    # The file we grabbed was a newer marker (a concurrent mark wrote it before
    # our rename, or os.replace re-created the path and we lost the race on
    # content). Put it back if the slot is still empty; otherwise the concurrent
    # writer already installed an even-newer marker and wins, so we just drop
    # our stale copy.
    _restore_or_discard(tmp, path)


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

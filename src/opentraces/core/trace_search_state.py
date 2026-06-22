"""Small state markers for the read-only trace search snapshot.

The race-safe mark/clear logic lives in :mod:`opentraces.core.dirty_marker`
(shared with the bucket-manifest read-model); this module is the search-specific
binding — the marker location plus the public function names every caller and
test already use.
"""

from __future__ import annotations

from pathlib import Path

from . import paths
from .dirty_marker import DirtyMarker


def dirty_marker_path() -> Path:
    return paths.OPENTRACES_DIR / "index" / "search_snapshot.dirty.json"


_MARKER = DirtyMarker(dirty_marker_path, "opentraces.trace_search_snapshot_dirty.v1")


def current_dirty_token() -> str | None:
    return _MARKER.current_token()


def mark_search_snapshot_dirty(reason: str, *, trace_id: str | None = None) -> None:
    _MARKER.mark(reason, trace_id=trace_id)


def clear_dirty_marker_if_unchanged(token: str | None) -> None:
    _MARKER.clear_if_unchanged(token)

"""Freshness marker for the bucket-manifest read-model (plan 087).

``bucket status`` reads sync/security counts O(1) from the persisted
``manifest.json`` rows instead of an O(N) envelope scan. But per-trace security
state can change OUT OF BAND of the manifest — a ``SECURITY_VERSION`` bump or an
enabled-tool change re-derives every record's ``syncable``/``stale`` via
``sync_trace_records_from_local_stores`` / the bucket security filter, touching
the object envelopes but NOT the manifest rows. A trace removal likewise drops
an object without rewriting the manifest.

Those writers drop this dirty marker so ``bucket status`` can SERVE the cached
row counts stamped ``stale=true`` (with a ``rebuild_recommended`` hint) rather
than silently reporting pre-rescan numbers — and WITHOUT paying the O(N) scan on
every read. A full ``bucket_manifest(write=True)`` reconcile (``bucket manifest
--heal`` / ``bucket repair``) refreshes the rows from the envelopes and clears
the marker.

The marker lives OUTSIDE the bucket content tree (under ``OPENTRACES_DIR/index``,
alongside the search-snapshot marker) so ``bucket remote push`` / ``bucket
verify`` / ``bucket prune`` never enumerate, sync, or flag it, and it never
enters ``bucket_digest``. The race-safe mark/clear logic is the shared
:class:`~opentraces.core.dirty_marker.DirtyMarker` primitive.
"""

from __future__ import annotations

from pathlib import Path

from . import paths
from .dirty_marker import DirtyMarker


def bucket_manifest_dirty_marker_path() -> Path:
    return paths.OPENTRACES_DIR / "index" / "bucket_manifest.dirty.json"


_MARKER = DirtyMarker(
    bucket_manifest_dirty_marker_path, "opentraces.bucket_manifest_dirty.v1"
)


def current_bucket_manifest_dirty_token() -> str | None:
    return _MARKER.current_token()


def mark_bucket_manifest_dirty(reason: str, *, trace_id: str | None = None) -> None:
    _MARKER.mark(reason, trace_id=trace_id)


def clear_bucket_manifest_dirty_if_unchanged(token: str | None) -> None:
    _MARKER.clear_if_unchanged(token)

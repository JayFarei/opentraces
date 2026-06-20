"""Plan 087 — bucket-manifest read-model freshness marker.

The marker lets ``bucket status`` serve cached row counts stamped stale (instead
of an O(N) rescan) after an out-of-band security re-derive, and a full
``bucket_manifest(write=True)`` sweep clears it once the rows are reconciled.
"""

from __future__ import annotations

from opentraces.core.bucket_manifest_state import (
    bucket_manifest_dirty_marker_path,
    clear_bucket_manifest_dirty_if_unchanged,
    current_bucket_manifest_dirty_token,
    mark_bucket_manifest_dirty,
)


def test_marker_roundtrip() -> None:
    assert current_bucket_manifest_dirty_token() is None
    mark_bucket_manifest_dirty("test", trace_id="t1")
    token = current_bucket_manifest_dirty_token()
    assert token is not None
    assert "test" in token
    clear_bucket_manifest_dirty_if_unchanged(token)
    assert current_bucket_manifest_dirty_token() is None
    assert not bucket_manifest_dirty_marker_path().exists()


def test_marker_lives_outside_bucket_content_tree() -> None:
    # Must not sit under the bucket dir, or push/verify/prune would enumerate it.
    from opentraces.core import paths

    marker = bucket_manifest_dirty_marker_path()
    assert paths.bucket_dir() not in marker.parents


def test_full_sweep_clears_marker_but_read_only_does_not() -> None:
    from opentraces.core.bucket_store import bucket_manifest

    # A read-only manifest call must NOT clear an out-of-band staleness signal.
    mark_bucket_manifest_dirty("security_rescan")
    assert current_bucket_manifest_dirty_token() is not None
    bucket_manifest(write=False, heal=False)
    assert current_bucket_manifest_dirty_token() is not None, (
        "read-only bucket_manifest must not clear the dirty marker"
    )

    # A full write sweep reconciles the rows from the envelopes -> clears it.
    bucket_manifest(write=True, heal=True)
    assert current_bucket_manifest_dirty_token() is None, (
        "write=True sweep should clear the marker once rows are reconciled"
    )


def test_concurrent_remark_during_sweep_survives(monkeypatch) -> None:
    # If an out-of-band writer re-marks WHILE the sweep scans, the clear must not
    # delete that fresh signal (token mismatch -> marker preserved).
    from opentraces.core import bucket_store

    mark_bucket_manifest_dirty("first")

    real_manifest = bucket_store._bucket_sync_blockers

    def _remark_then_call(*args, **kwargs):
        # Re-mark exactly once mid-sweep (a deeper-than-token-capture point).
        if not getattr(_remark_then_call, "fired", False):
            _remark_then_call.fired = True
            mark_bucket_manifest_dirty("second")
        return real_manifest(*args, **kwargs)

    monkeypatch.setattr(bucket_store, "_bucket_sync_blockers", _remark_then_call)
    bucket_store.bucket_manifest(write=True, heal=True)

    surviving = current_bucket_manifest_dirty_token()
    assert surviving is not None, "a re-mark during the sweep must not be cleared"
    assert "second" in surviving

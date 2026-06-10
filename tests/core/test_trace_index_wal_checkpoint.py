"""Issue #22 hypothesis 1: keep-warm sync writes must checkpoint the WAL.

The legacy Trace Index runs in WAL mode and is mutated by capture-time
keep-warm; without an explicit ``wal_checkpoint(TRUNCATE)`` the ``-wal``
sidecar grows without bound (1.5GB observed on a real ~1.1k-trace machine).
"""

from __future__ import annotations

from tests.core.test_trace_search_snapshot import _trace, _write_trace


def test_keep_warm_sync_truncates_wal() -> None:
    from opentraces.core.trace_index import (
        default_index_path,
        keep_index_warm,
        refresh_index,
    )

    _write_trace("demo-project", _trace("trace-wal-1", description="WAL probe one"))
    refresh_index()
    keep_index_warm(query_sources=("index",))

    # A second captured trace drives a real sync write through the F3 path.
    _write_trace("demo-project", _trace("trace-wal-2", description="WAL probe two"))
    result = keep_index_warm(trace_id="trace-wal-2", query_sources=("index",))
    assert result.ok and result.synced

    wal = default_index_path().with_name(default_index_path().name + "-wal")
    assert (not wal.exists()) or wal.stat().st_size == 0, (
        f"WAL not truncated after sync: {wal.stat().st_size} bytes"
    )

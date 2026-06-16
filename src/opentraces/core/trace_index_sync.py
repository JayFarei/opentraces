"""Cheap-sync cluster for the Trace Index (Plan 087 U4/U5/F1/F2/F3).

Digest-gated incremental sync and keep-warm primitives extracted from
``trace_index.py`` as part of a structural split. All public symbols are
re-exported from ``opentraces.core.trace_index`` so external callers are
unaffected.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

from . import search_diag
from .trace_index_sqlite import (
    _cheap_sync_lock,
    _checkpoint_wal_truncate,
    _connect,
    _meta_get,
    _meta_set,
)

import logging

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Plan 087 U4 — cheap-sync-then-serve for the ``trace query`` hot path.
# --------------------------------------------------------------------------

_SYNCED_DIGEST_KEY = "synced_bucket_digest"
_SYNCED_TRACE_DIGESTS_KEY = "synced_trace_digests"
# F1 (plan 087 fix) — the cheap O(1) freshness signal persisted at last sync.
# A stat-only fingerprint over the trace-source roots; when it is unchanged the
# steady-state probe short-circuits WITHOUT any corpus scan / manifest recompute.
_SYNCED_CHEAP_SIGNAL_KEY = "synced_cheap_signal"
MAX_INCREMENTAL_TRACE_DELTA = 100


def _synced_digest_key(query_source: str) -> str:
    return f"{_SYNCED_DIGEST_KEY}:{query_source}"


def _synced_trace_digests_key(query_source: str) -> str:
    return f"{_SYNCED_TRACE_DIGESTS_KEY}:{query_source}"


def _synced_cheap_signal_key(query_source: str) -> str:
    # Per-source so an ``index``-source sync does not mask a still-stale
    # ``projection`` (each source advances its own marker independently).
    return f"{_SYNCED_CHEAP_SIGNAL_KEY}:{query_source}"


@dataclass
class CheapSyncResult:
    """Outcome of one :func:`cheap_sync_query_state` call."""

    synced: bool
    query_source: str
    changed_trace_ids: list[str] = dataclass_field(default_factory=list)
    deleted_trace_ids: list[str] = dataclass_field(default_factory=list)
    bucket_digest: str | None = None
    maintenance_required: str | None = None


def _meta_get_safe(db_path: Path, key: str) -> str | None:
    """Best-effort single meta read from the index DB (never raises)."""

    try:
        with _connect(db_path) as conn:
            return _meta_get(conn, key)
    except sqlite3.Error:
        return None


def _compute_trace_delta(
    current: dict[str, str], previous: dict[str, str]
) -> tuple[list[str], list[str]]:
    """Diff two ``{trace_id: digest}`` maps into ``(changed, deleted)``."""

    changed = sorted(
        tid
        for tid, digest in current.items()
        if previous.get(tid) != digest
    )
    deleted = sorted(tid for tid in previous if tid not in current)
    return changed, deleted


def _bootstrap_projection_markers(
    db_path: Path,
    *,
    current_cheap_signal: str,
    index_digest: str | None,
    index_trace_digests: str | None,
) -> str | None:
    """Seed the projection-source cheap-sync markers from the serving build (G2).

    Returns the bucket digest to report (so the caller can short-circuit O(1))
    when the serving search projection is already fresh for ``current_cheap_signal``
    — i.e. the projection build stamped a ``built_from_cheap_signal`` that equals
    the current stat-only bucket signal. In that case the projection needs NO
    delta: we persist the projection's per-source markers exactly the way the
    index source persists its own (so the NEXT steady-state probe short-circuits
    via the cheap-signal equality at the top of ``cheap_sync_query_state``), then
    return.

    Returns ``None`` when no serving build exists, the build never stamped a
    cheap signal (legacy build), or the stamp does not match the current signal
    (the bucket genuinely moved since the build) — the caller then falls through
    to the normal delta path. Best-effort: any failure yields ``None`` so the
    caller never wrongly short-circuits a stale projection.
    """

    try:
        from .search_projection import search_projection_status

        status = search_projection_status()
    except Exception:  # noqa: BLE001 — bootstrap must never raise into queries.
        return None
    if status.get("state") != "ok":
        return None
    built_from_signal = status.get("built_from_cheap_signal")
    if not built_from_signal or built_from_signal != current_cheap_signal:
        # No stamp, or the bucket moved since the build — let the delta path run.
        return None

    # The projection is fresh for the current signal. Seed the projection
    # markers from the index source's snapshot (same bucket, same per-trace
    # record hashes) plus the cheap signal we just confirmed.
    bucket_digest = status.get("built_from_digest") or index_digest
    try:
        with _connect(db_path) as conn:
            _meta_set(
                conn,
                _synced_cheap_signal_key("projection"),
                current_cheap_signal,
            )
            if bucket_digest is not None:
                _meta_set(
                    conn, _synced_digest_key("projection"), str(bucket_digest)
                )
            if index_trace_digests is not None:
                _meta_set(
                    conn,
                    _synced_trace_digests_key("projection"),
                    index_trace_digests,
                )
            conn.commit()
    except sqlite3.Error:
        return None
    return str(bucket_digest) if bucket_digest is not None else None


def cheap_sync_query_state(
    *,
    query_source: str = "index",
    index_path: Path | None = None,
    cheap_signal: str | None = None,
    trace_id: str | None = None,
) -> CheapSyncResult:
    """Digest-gated incremental sync run immediately before a query (U4).

    Closes the Phase-1 stale-query window for the local bucket without
    reintroducing the full-rebuild cost. Steady state (no bucket change since
    the last sync) short-circuits with zero refresh work. A changed bucket
    triggers a BOUNDED ``refresh_index`` (and, for ``query_source ==
    "projection"``, a bounded ``refresh_search_projection``) keyed by the
    per-trace delta, then records the new digest snapshot.

    ``cheap_signal`` (F3): an already-computed :func:`_cheap_bucket_signal`
    snapshot. When the caller drives several query sources in one pass
    (``keep_index_warm``), it computes the stat-only signal ONCE and threads it
    in so each source does not re-scan the trace-source roots. ``None`` means
    "compute it here" (the standalone query-path contract is unchanged).

    ``trace_id`` (F3): the post-INGEST fast path. The capture pipeline already
    knows the single trace it just wrote, so when ``trace_id`` is given and the
    cheap signal moved, we skip the ~46s whole-corpus
    :func:`_current_bucket_trace_digests` scan entirely and instead run the
    bounded ``refresh_index`` (incremental, finds the one changed source by
    mtime) + a projection delta scoped to exactly that trace. The per-trace
    digest snapshot is NOT advanced on this path (only the cheap signal is), so
    a later whole-corpus sync still reconciles correctly.

    Best-effort: any failure leaves the warm cache serving and is swallowed
    (the query still runs against whatever is on disk). It never raises into
    the query path.
    """

    return _cheap_sync_query_state_guarded(
        query_source=query_source,
        index_path=index_path,
        cheap_signal=cheap_signal,
        trace_id=trace_id,
    )


def _cheap_sync_query_state_guarded(
    *,
    query_source: str,
    index_path: Path | None,
    cheap_signal: str | None,
    trace_id: str | None,
) -> CheapSyncResult:
    from .trace_index import default_index_path, _cheap_bucket_signal

    search_diag.incr("cheap_sync_calls")
    db_path = index_path or default_index_path()
    if not db_path.exists():
        return CheapSyncResult(synced=False, query_source=query_source)

    prev_digest, prev_cheap_signal, prev_per_trace_raw = _sync_marker_snapshot(
        db_path, query_source
    )
    current_cheap_signal = cheap_signal if cheap_signal is not None else _cheap_bucket_signal()
    if _cheap_signal_matches(prev_cheap_signal, current_cheap_signal):
        return CheapSyncResult(
            synced=False, query_source=query_source, bucket_digest=prev_digest
        )

    with _cheap_sync_lock(db_path):
        prev_digest, prev_cheap_signal, prev_per_trace_raw = _sync_marker_snapshot(
            db_path, query_source
        )
        current_cheap_signal = (
            cheap_signal if cheap_signal is not None else _cheap_bucket_signal()
        )
        if _cheap_signal_matches(prev_cheap_signal, current_cheap_signal):
            return CheapSyncResult(
                synced=False, query_source=query_source, bucket_digest=prev_digest
            )
        result = _cheap_sync_query_state_locked(
            db_path=db_path,
            query_source=query_source,
            current_cheap_signal=current_cheap_signal,
            prev_digest=prev_digest,
            prev_cheap_signal=prev_cheap_signal,
            prev_per_trace_raw=prev_per_trace_raw,
            trace_id=trace_id,
        )
        # Issue #22 — every keep-warm / refresh sync write path funnels
        # through here exactly once per sync. The index runs in WAL mode
        # and nothing else ever checkpoints it, so without this the
        # ``index.db-wal`` sidecar grows without bound on capture-heavy
        # machines. Only pay the checkpoint when the sync actually wrote
        # (``synced=True``); steady-state no-ops stay free. Best-effort:
        # the helper swallows every SQLite error.
        if result.synced:
            _checkpoint_wal_truncate(db_path)
        return result


def _sync_marker_snapshot(
    db_path: Path, query_source: str
) -> tuple[str | None, str | None, str | None]:
    try:
        with _connect(db_path) as conn:
            return (
                _meta_get(conn, _synced_digest_key(query_source)),
                _meta_get(conn, _synced_cheap_signal_key(query_source)),
                _meta_get(conn, _synced_trace_digests_key(query_source)),
            )
    except sqlite3.Error:
        return None, None, None


def _cheap_signal_matches(prev_cheap_signal: str | None, current_cheap_signal: str) -> bool:
    return (
        prev_cheap_signal is not None
        and current_cheap_signal != ""
        and prev_cheap_signal == current_cheap_signal
    )


def _refresh_index_incremental_only(
    db_path: Path,
    *,
    query_source: str,
    bucket_digest: str | None,
) -> CheapSyncResult | None:
    try:
        from .trace_index import TraceIndexRefreshRequiresRebuild, refresh_index

        refresh_index(db_path, allow_rebuild=False, refresh_trails=False)
    except TraceIndexRefreshRequiresRebuild as exc:
        return CheapSyncResult(
            synced=False,
            query_source=query_source,
            bucket_digest=bucket_digest,
            maintenance_required=f"{query_source}:{exc.reason}",
        )
    except Exception:
        return CheapSyncResult(
            synced=False,
            query_source=query_source,
            bucket_digest=bucket_digest,
        )
    return None


def _refresh_index_incremental_preflight(
    db_path: Path,
    *,
    query_source: str,
    bucket_digest: str | None,
) -> CheapSyncResult | None:
    try:
        from .trace_index import refresh_index_rebuild_reason

        reason = refresh_index_rebuild_reason(db_path)
    except Exception:
        return CheapSyncResult(
            synced=False,
            query_source=query_source,
            bucket_digest=bucket_digest,
        )
    if reason is not None:
        return CheapSyncResult(
            synced=False,
            query_source=query_source,
            bucket_digest=bucket_digest,
            maintenance_required=f"{query_source}:{reason}",
        )
    return None


def _cheap_sync_query_state_locked(
    *,
    db_path: Path,
    query_source: str,
    current_cheap_signal: str,
    prev_digest: str | None,
    prev_cheap_signal: str | None,
    prev_per_trace_raw: str | None,
    trace_id: str | None,
) -> CheapSyncResult:
    from .trace_index import _cheap_bucket_signal, _current_bucket_trace_digests
    if (
        query_source == "projection"
        and prev_cheap_signal is None
        and current_cheap_signal != ""
    ):
        seeded = _bootstrap_projection_markers(
            db_path,
            current_cheap_signal=current_cheap_signal,
            index_digest=_meta_get_safe(db_path, _synced_digest_key("index")),
            index_trace_digests=_meta_get_safe(
                db_path, _synced_trace_digests_key("index")
            ),
        )
        if seeded is not None:
            return CheapSyncResult(
                synced=False,
                query_source=query_source,
                bucket_digest=seeded,
            )

    if trace_id is not None:
        refresh_error = _refresh_index_incremental_only(
            db_path,
            query_source=query_source,
            bucket_digest=prev_digest,
        )
        if refresh_error is not None:
            return refresh_error
        if query_source == "projection":
            try:
                from .search_projection import refresh_search_projection

                refresh_search_projection(
                    changed_trace_ids=[trace_id],
                    deleted_trace_ids=[],
                    index_path=db_path,
                )
            except Exception:
                search_diag.incr("cheap_sync_delta_syncs")
                return CheapSyncResult(
                    synced=True,
                    query_source=query_source,
                    changed_trace_ids=[trace_id],
                    bucket_digest=prev_digest,
                )
        try:
            with _connect(db_path) as conn:
                _meta_set(
                    conn,
                    _synced_cheap_signal_key(query_source),
                    _cheap_bucket_signal(),
                )
                conn.commit()
        except sqlite3.Error:
            pass
        search_diag.incr("cheap_sync_delta_syncs")
        return CheapSyncResult(
            synced=True,
            query_source=query_source,
            changed_trace_ids=[trace_id],
            bucket_digest=prev_digest,
        )

    preflight_error = _refresh_index_incremental_preflight(
        db_path,
        query_source=query_source,
        bucket_digest=prev_digest,
    )
    if preflight_error is not None:
        return preflight_error

    try:
        top_digest, current_per_trace = _current_bucket_trace_digests()
    except Exception:
        return CheapSyncResult(synced=False, query_source=query_source)

    if top_digest is not None and prev_digest == top_digest:
        try:
            with _connect(db_path) as conn:
                _meta_set(
                    conn,
                    _synced_cheap_signal_key(query_source),
                    _cheap_bucket_signal(),
                )
                conn.commit()
        except sqlite3.Error:
            pass
        return CheapSyncResult(
            synced=False, query_source=query_source, bucket_digest=top_digest
        )

    try:
        previous_per_trace = json.loads(prev_per_trace_raw) if prev_per_trace_raw else {}
        if not isinstance(previous_per_trace, dict):
            previous_per_trace = {}
    except (ValueError, json.JSONDecodeError):
        previous_per_trace = {}

    changed, deleted = _compute_trace_delta(current_per_trace, previous_per_trace)
    delta_count = len(changed) + len(deleted)
    if delta_count > MAX_INCREMENTAL_TRACE_DELTA:
        return CheapSyncResult(
            synced=False,
            query_source=query_source,
            bucket_digest=top_digest,
            maintenance_required=(
                f"{query_source}:large_delta:{delta_count}:"
                f"max:{MAX_INCREMENTAL_TRACE_DELTA}"
            ),
        )

    refresh_error = _refresh_index_incremental_only(
        db_path,
        query_source=query_source,
        bucket_digest=top_digest,
    )
    if refresh_error is not None:
        return refresh_error

    if query_source == "projection":
        try:
            from .search_projection import refresh_search_projection

            refresh_search_projection(
                changed_trace_ids=changed,
                deleted_trace_ids=deleted,
                index_path=db_path,
            )
        except Exception:
            search_diag.incr("cheap_sync_delta_syncs")
            return CheapSyncResult(
                synced=True,
                query_source=query_source,
                changed_trace_ids=changed,
                deleted_trace_ids=deleted,
                bucket_digest=top_digest,
            )

    try:
        with _connect(db_path) as conn:
            if top_digest is not None:
                _meta_set(conn, _synced_digest_key(query_source), top_digest)
            _meta_set(
                conn,
                _synced_trace_digests_key(query_source),
                json.dumps(current_per_trace, sort_keys=True),
            )
            _meta_set(
                conn,
                _synced_cheap_signal_key(query_source),
                _cheap_bucket_signal(),
            )
            conn.commit()
    except sqlite3.Error:
        pass

    search_diag.incr("cheap_sync_delta_syncs")
    return CheapSyncResult(
        synced=True,
        query_source=query_source,
        changed_trace_ids=changed,
        deleted_trace_ids=deleted,
        bucket_digest=top_digest,
    )


# --------------------------------------------------------------------------
# Plan 087 U5 — best-effort keep-warm hooks.
# --------------------------------------------------------------------------


@dataclass
class KeepWarmResult:
    """Outcome of one :func:`keep_index_warm` best-effort call."""

    ok: bool
    synced: bool = False
    changed_trace_ids: list[str] = dataclass_field(default_factory=list)
    deleted_trace_ids: list[str] = dataclass_field(default_factory=list)
    snapshot_refreshed: bool = False
    maintenance_required: list[str] = dataclass_field(default_factory=list)
    error: str | None = None


def keep_index_warm(
    *,
    index_path: Path | None = None,
    query_sources: tuple[str, ...] = ("index",),
    trace_id: str | None = None,
) -> KeepWarmResult:
    """Best-effort keep-warm of the Trace Index (+ search projection) (U5/F3).

    Runs the U4 digest-gated cheap sync for each requested query source so a
    freshly captured trace becomes queryable without a manual refresh. Steady
    state is a cheap no-op: the stat-only bucket signal is computed ONCE here
    and threaded into every source (no per-source re-scan), and an unchanged
    signal short-circuits each source with zero corpus scan / manifest recompute
    / refresh.

    F3 default — ``query_sources`` defaults to ``("index",)``. The Trace Index
    refresh is incremental and cheap; the search-projection delta still stamps
    the build with a heavier bucket digest, so the per-capture / per-scan hot
    path warms only the index by default and leaves the projection to be warmed
    on its own (the now-cheap F2 delta refresh) by callers that opt in via
    ``query_sources=("index", "projection")``.

    ``trace_id`` (F3) — when the caller is the post-INGEST hook it passes the
    single trace it just wrote. The cheap sync then refreshes ONLY that trace
    (no ~46s whole-corpus delta derivation).

    NEVER raises: any internal failure is swallowed and surfaced as
    ``ok=False`` so capture / scan can never be broken by an index hiccup.
    """

    try:
        from .trace_index import _cheap_bucket_signal, cheap_sync_query_state
        # F3: one stat-only scan, reused across every query source.
        shared_signal = _cheap_bucket_signal()
        synced = False
        changed: list[str] = []
        deleted: list[str] = []
        maintenance_required: list[str] = []
        for query_source in query_sources:
            result = cheap_sync_query_state(
                query_source=query_source,
                index_path=index_path,
                cheap_signal=shared_signal,
                trace_id=trace_id,
            )
            if (
                result.maintenance_required
                and result.maintenance_required not in maintenance_required
            ):
                maintenance_required.append(result.maintenance_required)
            if result.synced:
                synced = True
                for tid in result.changed_trace_ids:
                    if tid not in changed:
                        changed.append(tid)
                for tid in result.deleted_trace_ids:
                    if tid not in deleted:
                        deleted.append(tid)
        snapshot_refreshed = False
        if changed or deleted:
            # Keep the read-only search snapshot fresh with the same bounded
            # per-trace delta. Best-effort: a missing/older-schema snapshot
            # returns None and the dirty marker stays as the backstop for an
            # explicit ``trace index`` rebuild.
            try:
                from .trace_search_snapshot import refresh_trace_search_snapshot

                snapshot_refreshed = (
                    refresh_trace_search_snapshot(changed, deleted) is not None
                )
            except Exception:
                logger.warning(
                    "snapshot keep-warm refresh failed (best-effort, ignored)",
                    exc_info=True,
                )
        return KeepWarmResult(
            ok=True,
            synced=synced,
            changed_trace_ids=changed,
            deleted_trace_ids=deleted,
            snapshot_refreshed=snapshot_refreshed,
            maintenance_required=maintenance_required,
        )
    except Exception as exc:  # noqa: BLE001 — keep-warm must never raise.
        logger.warning("keep_index_warm failed (best-effort, ignored)", exc_info=True)
        return KeepWarmResult(ok=False, error=f"{type(exc).__name__}: {exc}")

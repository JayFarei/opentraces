"""Local Trace Index cache for Plan 56 query workflows."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar
from urllib.parse import urlparse

from pydantic import ValidationError

from opentraces_schema import (
    CandidatePacket,
    TraceFacet,
    TraceMap,
    TraceMapEdge,
    TraceMapNode,
    TraceRecord,
    TraceSignal,
    TraceUnit,
    load_record_json,
)

from . import paths
from . import search_diag
from ._time import utc_now_str as _utc_now
from .query_helpers import _fts_query, _page_offset, _recency_weighted_sort, _terms
from .boilerplate import (
    headline_from_summary,
    intent_text_for_record,
    strip_injected,
    summary_for_record,
    summary_from_parts,
)
from .bucket_store import (
    bucket_manifest,
    iter_trace_record_objects,
    read_trace_record_object,
    sync_trace_records_from_local_stores,
)
from .provenance import provenance_from_record, provenance_from_unit
from .semantic import semantic_facets_for_trace
from .skill_detection import SkillInvocation, detect_skill_invocations, trace_skill_names
from .text_redaction import redact_index_text
from .trace_map import build_trace_map
from .trace_map import slice_trace_map_for_candidate
from .trails.query import TrailQueryProjection, build_trail_query_projection

logger = logging.getLogger(__name__)

try:  # POSIX advisory locking; available on macOS/Linux.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback.
    _fcntl = None


# Cluster A — A2/A5 schema bump: ``trail_sources.limitations_json`` carries
# structured limitations and ``indexed_at`` records when the trail projection
# was last synced into the query index.
INDEX_VERSION = "plan056-m1-v8"
INDEX_BUSY_TIMEOUT_MS = 5000
INDEX_WRITE_RETRY_LIMIT = 5
INDEX_WRITE_RETRY_BASE_SECONDS = 0.05


class IndexLockedError(RuntimeError):
    """Raised when the index DB stays locked past the retry budget.

    Surfaces a clean message instead of a raw ``sqlite3.OperationalError``
    so the CLI can exit with a typed error code rather than a traceback.
    """


T = TypeVar("T")


def _configure_connection(conn: sqlite3.Connection, *, wal: bool = True) -> None:
    """Apply WAL + busy-timeout pragmas on every fresh connection.

    ``busy_timeout`` is a per-connection setting that needs to be set on each
    new handle. ``journal_mode=wal`` is a one-time DB-level switch but is
    harmless to re-emit. We skip WAL for the tmp file used during
    ``_rebuild_index_locked`` because the tmp gets atomically renamed into
    place: a WAL-mode tmp would leave its ``-wal``/``-shm`` sidecars dangling
    on the rename and the new live DB would surface ``disk I/O error`` on
    the first read.
    """
    try:
        conn.execute(f"PRAGMA busy_timeout={INDEX_BUSY_TIMEOUT_MS}")
        if wal:
            conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        # Some shapes (e.g. fresh empty DB on a read-only filesystem) cannot
        # set journal_mode=WAL; fall back silently. busy_timeout still helps.
        pass


def _connect(db_path: Path, *, wal: bool = True) -> sqlite3.Connection:
    """Open the index DB with the WAL + busy-timeout pragmas applied."""
    conn = sqlite3.connect(db_path, timeout=INDEX_BUSY_TIMEOUT_MS / 1000.0)
    _configure_connection(conn, wal=wal)
    return conn


def _unlink_wal_sidecars(db_path: Path) -> None:
    """Remove any ``-wal``/``-shm`` sidecars belonging to ``db_path``.

    A WAL sidecar is only valid for the exact DB file it was created against.
    After a file-level swap of the DB (an atomic ``replace`` during rebuild, or
    an otbox snapshot restore that copies the ``.db`` separately from its
    sidecars) the leftover ``-wal``/``-shm`` belong to the *old* file; replaying
    them against the new DB on the next ``PRAGMA journal_mode=WAL`` raises
    ``sqlite3.DatabaseError: database disk image is malformed``. Discarding them
    is safe because the index is a fully rebuildable cache.
    """
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        try:
            sidecar.unlink()
        except OSError:
            pass


def _heal_corrupt_index(db_path: Path) -> None:
    """Discard a malformed index DB (a rebuildable cache) and rebuild it once.

    The local trace index is derived entirely from the retained trace stores,
    so a corrupt or unreadable DB is never authoritative. The most common cause
    is stale ``-wal``/``-shm`` sidecars (see ``_unlink_wal_sidecars``). Plan 087
    U1 removed the query-time refresh that used to mask this by rebuilding on
    every read, so the read path now self-heals on demand instead of surfacing
    a sqlite error.
    """
    _unlink_wal_sidecars(db_path)
    rebuild_index(db_path)


def _is_lock_error(exc: sqlite3.OperationalError) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _retry_on_lock(action: Callable[[], T]) -> T:
    """Run ``action`` and retry on transient SQLite lock contention.

    Used to wrap the top-level write transactions (``rebuild_index`` /
    ``refresh_index``). A persistent lock past the retry budget surfaces as
    :class:`IndexLockedError` so the CLI can produce a clean error message.
    """
    delay = INDEX_WRITE_RETRY_BASE_SECONDS
    last_exc: sqlite3.OperationalError | None = None
    for _ in range(INDEX_WRITE_RETRY_LIMIT):
        try:
            return action()
        except sqlite3.OperationalError as exc:
            if not _is_lock_error(exc):
                raise
            last_exc = exc
            time.sleep(delay)
            delay = min(delay * 2, 1.0)
    assert last_exc is not None
    raise IndexLockedError(
        f"trace index DB stayed locked after {INDEX_WRITE_RETRY_LIMIT} retries: {last_exc}"
    )


@contextmanager
def _cheap_sync_lock(db_path: Path) -> Iterator[None]:
    """Serialize expensive cheap-sync delta work across processes.

    Steady-state readers take no lock. The lock is only acquired after the
    stat-only signal indicates the bucket may have changed; once acquired, the
    caller re-checks the marker so only one process pays the delta sync.
    """

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if _fcntl is None:  # pragma: no cover - POSIX-only targets.
        yield
        return
    lock_path = db_path.parent / ".cheap-sync.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        _fcntl.flock(fd, _fcntl.LOCK_EX)
        try:
            yield
        finally:
            _fcntl.flock(fd, _fcntl.LOCK_UN)
    finally:
        os.close(fd)
_M1_UNIT_TYPES = {
    "trace",
    "trace_map_node",
    "trace_slice",
    "trace_intent_candidate",
    "patch",
    "skill_invocation",
    "tool_sequence",
    "test_or_error_signal",
    "git_anchor",
}
_FIELD_WEIGHTS = {
    "title_text": 5.0,
    "intent_text": 4.0,
    "action_text": 3.0,
    "evidence_text": 2.0,
    "artifact_text": 1.0,
}
_TEST_COMMAND_RE = re.compile(
    r"\b(pytest|npm\s+test|pnpm\s+test|yarn\s+test|cargo\s+test|go\s+test|"
    r"vitest|jest|rspec|mvn\s+test|gradle\s+test)\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s'\"<>$`{}|^\\]+")
_DEPENDENCY_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_./-]*$")
_COMMAND_FAMILY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_./-]*$")
_SHELL_KEYWORDS = frozenset(
    {
        "for", "while", "until", "do", "done", "if", "then", "else", "elif",
        "fi", "case", "esac", "in", "select", "function", "{", "}", "(", ")",
        "[", "]", "[[", "]]", "&&", "||", "|", ";", ">", ">>", "<", "<<",
        "2>", "2>>", "&>", "!",
    }
)
CANDIDATE_KIND_CHOICES: tuple[str, ...] = (
    "bug_fix",
    "trace",
    "trace_map_node",
    "trace_slice",
    "trace_intent_candidate",
    "patch",
    "skill_invocation",
    "tool_sequence",
    "test_or_error_signal",
    "git_anchor",
)
FILE_OP_CHOICES: tuple[str, ...] = ("edit", "read")
BASH_ACTION_CHOICES: tuple[str, ...] = ("test", "service_probe")


@dataclass(frozen=True)
class RebuildSummary:
    index_path: Path
    trace_count: int
    unit_count: int
    map_node_count: int


@dataclass(frozen=True)
class QueryPage:
    candidates: list[CandidatePacket]
    next_page_token: str | None
    total: int
    warnings: list[dict[str, Any]] = dataclass_field(default_factory=list)


def default_index_path() -> Path:
    return paths.OPENTRACES_DIR / "index" / "index.db"


def rebuild_index(index_path: Path | None = None) -> RebuildSummary:
    """Rebuild the local cache from retained project trace stores."""

    db_path = index_path or default_index_path()
    return _retry_on_lock(lambda: _rebuild_index_locked(db_path))


def _rebuild_index_locked(db_path: Path) -> RebuildSummary:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_name(f"{db_path.name}.tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    try:
        # Build the tmp DB in rollback-journal mode; we'll atomically rename
        # it over the live DB which would orphan WAL sidecars otherwise.
        with _connect(tmp_path, wal=False) as conn:
            _create_schema(conn)
            project_sources = _project_sources_by_slug()
            trail_projection_cache: dict[str, TrailQueryProjection | None] = {}
            for source in _iter_trace_sources():
                trace_path = source.trace_path
                project_slug = source.project_slug
                layer = source.layer
                records = _iter_trace_file_records(trace_path)
                for record in records:
                    _delete_trace_ids(conn, [record.trace_id])
                    trace_map = build_trace_map(
                        record,
                        trail_projection=_trail_projection_for_project(
                            project_slug,
                            project_sources,
                            trail_projection_cache,
                        ),
                    )
                    units = _build_units(record, trace_map, project_slug, layer=layer)
                    _insert_trace(conn, record, project_slug, trace_path, trace_map)
                    for unit in units:
                        _insert_unit(conn, unit)
                _record_source(conn, trace_path, len(records))
            # Rebuild trail projections per canonical project home, regardless
            # of whether the project has any trace JSONL yet — Trail Patch
            # units originate from refs/opentraces, not from the trace store.
            for project_home in _iter_project_homes():
                project_slug = project_home.name
                source_repo = project_sources.get(project_slug)
                if source_repo and source_repo.exists():
                    _rebuild_trail_projection(conn, project_slug, source_repo)
            conn.execute(
                "insert into meta(key, value) values (?, ?)",
                ("index_version", INDEX_VERSION),
            )
            summary = _index_totals(conn, db_path)
            conn.commit()
        tmp_path.replace(db_path)
        # The replaced DB is a brand-new file; any pre-existing ``-wal``/``-shm``
        # sidecars belong to the old DB and would be replayed as a mismatched
        # WAL ("database disk image is malformed") on the WAL open below.
        _unlink_wal_sidecars(db_path)
        # Cluster A — A3: switch the freshly-renamed live DB into WAL so
        # subsequent concurrent readers and writers can coexist. We could
        # not do this on the tmp file because WAL leaves ``-wal``/``-shm``
        # sidecars that would not survive ``replace``.
        with _connect(db_path) as live_conn:
            live_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return summary
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def refresh_index(
    index_path: Path | None = None,
    *,
    refresh_trails: bool = True,
    trail_project_slugs: set[str] | None = None,
) -> RebuildSummary:
    """Refresh changed trace-store sources without replacing the cache file.

    ``refresh_trails`` (default True) preserves the historical bare-call
    contract: the per-project trail projection is re-synced. Pass
    ``refresh_trails=False`` to skip the entire project-home trail loop (a
    no-op refresh then does zero per-home ref reads). ``trail_project_slugs``
    restricts the trail loop to the named project homes (capture/watcher hooks
    refresh only the changed project); the stale-trail-project sweep is also
    skipped when a scoped set is given so other projects' projections survive.
    """

    db_path = index_path or default_index_path()
    if not db_path.exists():
        return rebuild_index(db_path)

    return _retry_on_lock(
        lambda: _refresh_index_locked(
            db_path,
            refresh_trails=refresh_trails,
            trail_project_slugs=trail_project_slugs,
        )
    )


# --------------------------------------------------------------------------
# Plan 087 U4 — cheap-sync-then-serve for the ``trace query`` hot path.
#
# Phase 1 stopped the query path from auto-rebuilding (killing the 105s tax)
# but opened a stale window: a freshly captured/altered trace was invisible
# until an explicit refresh. U4 closes that window with a digest-gated
# incremental sync that scales with the delta, never the corpus.
#
# Mechanics:
#   1. Cheap probe — read the last-synced bucket digest from the index meta
#      table; compute the current bucket digest. Equal => steady state, return
#      immediately (zero refresh work, sub-2s).
#   2. On mismatch — compute the per-trace delta (changed/added vs deleted) by
#      diffing the current per-trace bucket digests against the snapshot stored
#      at last sync, then run the BOUNDED incremental refreshers:
#        - ``refresh_index`` (already incremental + R3-cheap) for the index,
#        - ``refresh_search_projection(changed, deleted)`` for the projection.
#   3. Persist the new digest + per-trace snapshot so the next call short-
#      circuits. The pointer/marker advance is the last step.
# --------------------------------------------------------------------------

_SYNCED_DIGEST_KEY = "synced_bucket_digest"
_SYNCED_TRACE_DIGESTS_KEY = "synced_trace_digests"
# F1 (plan 087 fix) — the cheap O(1) freshness signal persisted at last sync.
# A stat-only fingerprint over the trace-source roots; when it is unchanged the
# steady-state probe short-circuits WITHOUT any corpus scan / manifest recompute.
_SYNCED_CHEAP_SIGNAL_KEY = "synced_cheap_signal"


def _synced_digest_key(query_source: str) -> str:
    return f"{_SYNCED_DIGEST_KEY}:{query_source}"


def _synced_trace_digests_key(query_source: str) -> str:
    return f"{_SYNCED_TRACE_DIGESTS_KEY}:{query_source}"


def _synced_cheap_signal_key(query_source: str) -> str:
    # Per-source so an ``index``-source sync does not mask a still-stale
    # ``projection`` (each source advances its own marker independently).
    return f"{_SYNCED_CHEAP_SIGNAL_KEY}:{query_source}"


def _cheap_bucket_signal() -> str:
    """Return a cheap, stat-only fingerprint of the trace-source roots (F1).

    This is the O(1) freshness signal for the ``trace query`` hot path. It walks
    the four roots that feed the bucket's trace corpus — the mirrored
    ``objects/traces/v1`` envelope root, the legacy ``trace-records`` mirror, and
    the canonical legacy stores (``projects/*/traces/*.jsonl`` + ``staging/*.jsonl``)
    that ``sync_trace_records_from_local_stores`` reads from — and aggregates
    ``(path, mtime_ns, size)`` over the files it finds. NO file is opened or
    parsed; no manifest is recomputed. A new / changed / removed trace file (in
    either the mirror or the canonical store) flips the fingerprint, which is the
    only signal allowed to engage the expensive delta path.

    Cost is one ``os.scandir``/``stat`` per trace file (~17ms for ~3.7k files on
    the live bucket), versus the ~46s full sync+parse+manifest the digest probe
    used to pay on every steady-state query. Best-effort: any error yields an
    empty signal so the caller treats it as "changed" and falls back to the heavy
    path rather than wrongly short-circuiting.
    """

    from .bucket_layout import legacy_trace_records_root, trace_records_root

    parts: list[str] = []

    def _walk(root: Path) -> None:
        if not root.exists():
            return
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                                continue
                            st = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        parts.append(f"{entry.path}|{st.st_mtime_ns}|{st.st_size}")
            except OSError:
                continue

    try:
        _walk(trace_records_root())
        _walk(legacy_trace_records_root())
        projects_root = getattr(paths, "PROJECTS_DIR", None)
        if projects_root is not None:
            for project_home in (projects_root.iterdir() if projects_root.exists() else []):
                if project_home.is_dir():
                    _walk(project_home / "traces")
        staging_root = getattr(paths, "STAGING_DIR", None)
        if staging_root is not None:
            _walk(staging_root)
    except Exception:  # noqa: BLE001 — cheap signal must never raise.
        return ""

    parts.sort()
    import hashlib

    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


@dataclass
class CheapSyncResult:
    """Outcome of one :func:`cheap_sync_query_state` call."""

    synced: bool
    query_source: str
    changed_trace_ids: list[str] = dataclass_field(default_factory=list)
    deleted_trace_ids: list[str] = dataclass_field(default_factory=list)
    bucket_digest: str | None = None


def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    try:
        row = conn.execute(
            "select value from meta where key = ?", (key,)
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return row[0] if not isinstance(row, sqlite3.Row) else row["value"]


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "insert or replace into meta(key, value) values (?, ?)", (key, value)
    )


def _current_bucket_trace_digests() -> tuple[str | None, dict[str, str]]:
    """Return ``(top_level_digest, {trace_id: per_trace_record_hash})``.

    Mirrors local project/staging stores into the bucket first (the same
    bridge ``_iter_trace_sources`` performs before reindex) so the digest
    reflects freshly-written-but-not-yet-indexed traces and prunes removed
    legacy mirrors. The per-trace map is keyed off the record-object
    ``record_hash`` (the layer the index actually syncs from); the top-level
    digest is the deterministic ``bucket_digest`` used as the projection's
    ``built_from_digest`` so the projection and index share one freshness
    signal. Best-effort: any failure yields ``(None, {})`` and the caller
    falls back to a full sync.
    """

    try:
        sync_trace_records_from_local_stores()
    except Exception:
        pass
    try:
        per_trace = {
            obj.trace_id: str(obj.record_hash)
            for obj in iter_trace_record_objects()
            if obj.trace_id
        }
    except Exception:
        per_trace = {}
    try:
        manifest = bucket_manifest(write=False, include_objects=False)
        top = manifest.get("bucket_digest") or manifest.get("digest")
    except Exception:
        top = None
    return (str(top) if top is not None else None), per_trace


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


def _meta_get_safe(db_path: Path, key: str) -> str | None:
    """Best-effort single meta read from the index DB (never raises)."""

    try:
        with _connect(db_path) as conn:
            return _meta_get(conn, key)
    except sqlite3.Error:
        return None


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
        return _cheap_sync_query_state_locked(
            db_path=db_path,
            query_source=query_source,
            current_cheap_signal=current_cheap_signal,
            prev_digest=prev_digest,
            prev_cheap_signal=prev_cheap_signal,
            prev_per_trace_raw=prev_per_trace_raw,
            trace_id=trace_id,
        )


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
        try:
            refresh_index(db_path)
        except Exception:
            return CheapSyncResult(synced=False, query_source=query_source)
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

    try:
        refresh_index(db_path)
    except Exception:
        return CheapSyncResult(
            synced=False,
            query_source=query_source,
            bucket_digest=top_digest,
        )

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
#
# Capture (ingest) and the watcher sweep call ``keep_index_warm`` after they
# have written a trace to the bucket so the warm index + search projection
# reflect the new trace WITHOUT a manual ``trace index refresh``. This rides
# on the U4 ``cheap_sync_query_state`` primitive: steady-state short-circuits
# (zero refresh work), a changed bucket triggers the bounded incremental
# refreshers.
#
# HARD CONTRACT: keep-warm is best-effort and MUST NOT raise into the capture
# / scan path. Any failure is swallowed, logged at warning level, and reported
# as ``ok=False`` so the warm cache keeps serving and capture still succeeds.
# --------------------------------------------------------------------------


@dataclass
class KeepWarmResult:
    """Outcome of one :func:`keep_index_warm` best-effort call."""

    ok: bool
    synced: bool = False
    changed_trace_ids: list[str] = dataclass_field(default_factory=list)
    deleted_trace_ids: list[str] = dataclass_field(default_factory=list)
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
        # F3: one stat-only scan, reused across every query source.
        shared_signal = _cheap_bucket_signal()
        synced = False
        changed: list[str] = []
        deleted: list[str] = []
        for query_source in query_sources:
            result = cheap_sync_query_state(
                query_source=query_source,
                index_path=index_path,
                cheap_signal=shared_signal,
                trace_id=trace_id,
            )
            if result.synced:
                synced = True
                for tid in result.changed_trace_ids:
                    if tid not in changed:
                        changed.append(tid)
                for tid in result.deleted_trace_ids:
                    if tid not in deleted:
                        deleted.append(tid)
        return KeepWarmResult(
            ok=True,
            synced=synced,
            changed_trace_ids=changed,
            deleted_trace_ids=deleted,
        )
    except Exception as exc:  # noqa: BLE001 — keep-warm must never raise.
        logger.warning("keep_index_warm failed (best-effort, ignored)", exc_info=True)
        return KeepWarmResult(ok=False, error=f"{type(exc).__name__}: {exc}")


def _refresh_index_locked(
    db_path: Path,
    *,
    refresh_trails: bool = True,
    trail_project_slugs: set[str] | None = None,
) -> RebuildSummary:
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if not _schema_supports_refresh(conn):
            return rebuild_index(db_path)

        project_sources = _project_sources_by_slug()
        trail_projection_cache: dict[str, TrailQueryProjection | None] = {}
        seen_sources: set[str] = set()
        # R3: batch the per-source freshness lookup into one scan instead of
        # 1k+ point-SELECTs. The loop below does in-memory dict lookups so a
        # no-op refresh over a large bucket stays cheap.
        source_stats: dict[str, tuple[int, int]] = {
            str(row["path"]): (int(row["mtime_ns"]), int(row["size"]))
            for row in conn.execute("select path, mtime_ns, size from sources")
        }
        for source in _iter_trace_sources():
            trace_path = source.trace_path
            project_slug = source.project_slug
            layer = source.layer
            source_key = str(trace_path)
            seen_sources.add(source_key)
            stat = trace_path.stat()
            existing = source_stats.get(source_key)
            if (
                existing is not None
                and existing[0] == stat.st_mtime_ns
                and existing[1] == stat.st_size
            ):
                continue
            old_trace_ids = [
                str(row["trace_id"])
                for row in conn.execute(
                    "select trace_id from traces where trace_path = ?",
                    (source_key,),
                )
            ]
            records = _iter_trace_file_records(trace_path)
            _delete_trace_ids(
                conn,
                [*old_trace_ids, *[record.trace_id for record in records]],
            )
            for record in records:
                trace_map = build_trace_map(
                    record,
                    trail_projection=_trail_projection_for_project(
                        project_slug,
                        project_sources,
                        trail_projection_cache,
                    ),
                )
                _insert_trace(conn, record, project_slug, trace_path, trace_map)
                for unit in _build_units(record, trace_map, project_slug, layer=layer):
                    _insert_unit(conn, unit)
            _record_source(conn, trace_path, len(records))

        stale_sources = [
            str(row["path"])
            for row in conn.execute("select path from sources")
            if str(row["path"]) not in seen_sources
        ]
        for source_key in stale_sources:
            old_trace_ids = [
                str(row["trace_id"])
                for row in conn.execute(
                    "select trace_id from traces where trace_path = ?",
                    (source_key,),
                )
            ]
            _delete_trace_ids(conn, old_trace_ids)
            conn.execute("delete from sources where path = ?", (source_key,))

        if refresh_trails:
            seen_trail_projects: set[str] = set()
            for project_home in _iter_project_homes():
                project_slug = project_home.name
                if (
                    trail_project_slugs is not None
                    and project_slug not in trail_project_slugs
                ):
                    continue
                seen_trail_projects.add(project_slug)
                source_repo = project_sources.get(project_slug)
                if source_repo and source_repo.exists():
                    _refresh_trail_projection(conn, project_slug, source_repo)
                elif _project_has_trail_rows(conn, project_slug):
                    # R3: only pay for the multi-statement delete sweep when
                    # there is actually a projection to prune. On the live
                    # bucket most homes are unregistered AND have never had a
                    # trail projection, so this collapses N redundant delete
                    # passes into N cheap indexed EXISTS probes.
                    _delete_trail_projection_for_project(conn, project_slug)
                    conn.execute(
                        "delete from trail_sources where project_slug = ?",
                        (project_slug,),
                    )
            # Staging traces have no associated workspace repo; nothing to project.

            # A scoped refresh only touched the named homes, so the
            # stale-trail-project sweep would otherwise delete every OTHER
            # project's projection rows. Skip the sweep when scoped.
            if trail_project_slugs is None:
                stale_trail_projects = [
                    str(row["project_slug"])
                    for row in conn.execute("select project_slug from trail_sources")
                    if str(row["project_slug"]) not in seen_trail_projects
                ]
                for project_slug in stale_trail_projects:
                    _delete_trail_projection_for_project(conn, project_slug)
                    conn.execute(
                        "delete from trail_sources where project_slug = ?",
                        (project_slug,),
                    )

        conn.execute(
            "insert or replace into meta(key, value) values (?, ?)",
            ("index_version", INDEX_VERSION),
        )
        conn.commit()
        return _index_totals(conn, db_path)


def query_index(
    *,
    lex: str | None = None,
    skill: str | None = None,
    tool: str | None = None,
    files: str | None = None,
    file_kind: str | None = None,
    file_op: str | None = None,
    signal: str | None = None,
    facet_filters: tuple[str, ...] = (),
    metadata_filters: tuple[str, ...] = (),
    provider: str | None = None,
    cmd_family: str | None = None,
    bash_action: str | None = None,
    test_framework: str | None = None,
    service: str | None = None,
    service_channel: str | None = None,
    dependency: str | None = None,
    git_tier: str | None = None,
    survival: str | None = None,
    semantic: str | None = None,
    since: str | None = None,
    success: bool | None = None,
    success_unknown: bool = False,
    committed: bool | None = None,
    committed_unknown: bool = False,
    candidate_kind: str | None = None,
    latest_generation: bool = True,
    project: str | None = None,
    limit: int = 20,
    page_token: str | None = None,
    include_slice: str | None = None,
    max_slice_nodes: int = 40,
    index_path: Path | None = None,
) -> list[CandidatePacket]:
    """Return bounded candidate packets from the local index."""
    return query_index_page(
        lex=lex,
        skill=skill,
        tool=tool,
        files=files,
        file_kind=file_kind,
        file_op=file_op,
        signal=signal,
        facet_filters=facet_filters,
        metadata_filters=metadata_filters,
        provider=provider,
        cmd_family=cmd_family,
        bash_action=bash_action,
        test_framework=test_framework,
        service=service,
        service_channel=service_channel,
        dependency=dependency,
        git_tier=git_tier,
        survival=survival,
        semantic=semantic,
        since=since,
        success=success,
        success_unknown=success_unknown,
        committed=committed,
        committed_unknown=committed_unknown,
        candidate_kind=candidate_kind,
        latest_generation=latest_generation,
        project=project,
        limit=limit,
        page_token=page_token,
        include_slice=include_slice,
        max_slice_nodes=max_slice_nodes,
        index_path=index_path,
    ).candidates


def query_index_page(
    *,
    lex: str | None = None,
    skill: str | None = None,
    tool: str | None = None,
    files: str | None = None,
    file_kind: str | None = None,
    file_op: str | None = None,
    signal: str | None = None,
    facet_filters: tuple[str, ...] = (),
    metadata_filters: tuple[str, ...] = (),
    provider: str | None = None,
    cmd_family: str | None = None,
    bash_action: str | None = None,
    test_framework: str | None = None,
    service: str | None = None,
    service_channel: str | None = None,
    dependency: str | None = None,
    git_tier: str | None = None,
    survival: str | None = None,
    semantic: str | None = None,
    since: str | None = None,
    success: bool | None = None,
    success_unknown: bool = False,
    committed: bool | None = None,
    committed_unknown: bool = False,
    candidate_kind: str | None = None,
    latest_generation: bool = True,
    project: str | None = None,
    limit: int = 20,
    page_token: str | None = None,
    include_slice: str | None = None,
    max_slice_nodes: int = 40,
    sort: str = "relevance",
    min_score: float | None = None,
    recency_weight: float = 0.0,
    index_path: Path | None = None,
) -> QueryPage:
    """Return one stable page of bounded candidate packets."""

    db_path = index_path or default_index_path()
    search_diag.reset()
    # Plan 087 U1: the query hot path never auto-refreshes the warm cache.
    # Only the cold-start bootstrap (no DB file yet) builds the index; a forced
    # refresh/rebuild is reachable solely via explicit maintenance
    # (``trace index rebuild``/``refresh``) or ``--force-rebuild``.
    if not db_path.exists():
        rebuild_index(db_path)
    parsed_facets = _parse_key_value_filters(facet_filters)
    parsed_metadata = _parse_key_value_filters(metadata_filters)
    since_dt = _parse_since(since) if since else None
    terms = _terms(lex or "")

    unit_type_filter = _requested_unit_type(parsed_facets, candidate_kind)

    def _read_unit_rows() -> list[sqlite3.Row]:
        with _connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            return _select_unit_rows(conn, terms, unit_type_filter)

    try:
        rows = _read_unit_rows()
    except sqlite3.DatabaseError:
        # The index is a rebuildable cache; a malformed DB (e.g. stale WAL
        # sidecars after a snapshot restore) self-heals via a one-time rebuild
        # rather than surfacing a sqlite error to ``trace query``.
        _heal_corrupt_index(db_path)
        rows = _read_unit_rows()

    if search_diag.enabled():
        search_diag.record(
            path="index_fts" if terms else "index_scan",
            rows_scanned=len(rows),
            docs_selected=len(rows),
            docs_scored=len(rows),
            corpus_docs=_count_index_units(db_path, unit_type_filter or "trace"),
        )
    candidates = [_unit_from_row(row) for row in rows]
    if latest_generation:
        candidates = _latest_units(candidates)
    if project:
        candidates = [unit for unit in candidates if unit.project_slug == project]
    if skill:
        candidates = [unit for unit in candidates if skill in unit.skills]
    if tool:
        candidates = [unit for unit in candidates if tool in _facet_values(unit, "tool.name")]
    if files:
        candidates = [unit for unit in candidates if any(fnmatch(path, files) for path in unit.files)]
    if file_kind:
        expected_kind = file_kind.lstrip(".")
        candidates = [unit for unit in candidates if expected_kind in _facet_values(unit, "file.kind")]
    if file_op:
        candidates = [unit for unit in candidates if file_op in _facet_values(unit, "file.operation")]
    if signal:
        candidates = [
            unit
            for unit in candidates
            if any(sig.name == signal and bool(sig.value) for sig in unit.signals)
        ]
    if parsed_facets:
        candidates = [unit for unit in candidates if _matches_facet_filters(unit, parsed_facets)]
    if parsed_metadata:
        candidates = [
            unit for unit in candidates if _matches_metadata_filters(unit, parsed_metadata)
        ]
    named_filters = _named_facet_filters(
        provider=provider,
        cmd_family=cmd_family,
        bash_action=bash_action,
        test_framework=test_framework,
        service=service,
        service_channel=service_channel,
        dependency=dependency,
        git_tier=git_tier,
        survival=survival,
    )
    if named_filters:
        candidates = [unit for unit in candidates if _matches_facet_filters(unit, named_filters)]
    if since_dt:
        candidates = [unit for unit in candidates if _unit_timestamp(unit) >= since_dt]
    if success_unknown and success is not None:
        raise ValueError("Use --success/--no-success or --unknown-success, not both.")
    if committed_unknown and committed is not None:
        raise ValueError("Use --committed/--uncommitted or --unknown-committed, not both.")
    if success is not None:
        candidates = [
            unit
            for unit in candidates
            if _bool_facet(unit, "outcome.success") is success
        ]
    elif success_unknown:
        candidates = [
            unit for unit in candidates if _bool_facet(unit, "outcome.success") is None
        ]
    if committed is not None:
        candidates = [
            unit
            for unit in candidates
            if _bool_facet(unit, "outcome.committed") is committed
        ]
    elif committed_unknown:
        candidates = [
            unit for unit in candidates if _bool_facet(unit, "outcome.committed") is None
        ]
    if candidate_kind:
        candidates = [
            unit
            for unit in candidates
            if _candidate_kind(unit) == candidate_kind or unit.unit_type == candidate_kind
        ]

    scored: list[tuple[float, dict[str, float], dict[str, list[str]], TraceUnit]] = []
    has_filter = (
        any(
            value is not None
            for value in (
                lex,
                skill,
                tool,
                files,
                file_kind,
                file_op,
                signal,
                success,
                committed,
                candidate_kind,
                project,
                provider,
                cmd_family,
                bash_action,
                test_framework,
                service,
                service_channel,
                dependency,
                git_tier,
                survival,
                since,
            )
        )
        or bool(parsed_facets)
        or bool(parsed_metadata)
        or success_unknown
        or committed_unknown
    )
    for unit in candidates:
        lexical_score, matched_fields = _lexical_score(unit, terms)
        if terms and lexical_score <= 0:
            continue
        signal_score = 5.0 if signal else 0.0
        skill_score = 3.0 if skill else 0.0
        metadata_score = _metadata_score(
            unit,
            tool=tool,
            files=files,
            file_kind=file_kind,
            file_op=file_op,
            success=success,
            committed=committed,
            candidate_kind=candidate_kind,
            project=project,
            facet_filters=parsed_facets,
            metadata_filters=parsed_metadata,
            named_filters=named_filters,
            since=since,
        )
        total = lexical_score + signal_score + skill_score + metadata_score
        if total <= 0 and has_filter:
            total = 1.0
        if not has_filter:
            continue
        scored.append(
            (
                total,
                {
                    "lexical": lexical_score,
                    "signal": signal_score,
                    "facet": skill_score,
                    "metadata": metadata_score,
                },
                matched_fields,
                unit,
            )
        )

    if min_score is not None:
        scored = [item for item in scored if item[0] >= min_score]
    if sort == "time":
        scored.sort(key=lambda item: (_unit_timestamp(item[3]), -item[0], item[3].trace_id))
    elif sort == "recency":
        scored.sort(
            key=lambda item: (-_unit_timestamp(item[3]).timestamp(), -item[0], item[3].trace_id)
        )
    elif recency_weight:  # relevance blended with a recency term (U5)
        scored = _recency_weighted_sort(scored, recency_weight, _unit_timestamp)
    else:  # relevance (default)
        scored.sort(key=lambda item: (-item[0], item[3].trace_id))
    offset = _page_offset(page_token)
    page_size = max(1, limit)
    selected = scored[offset : offset + page_size]
    next_offset = offset + page_size
    next_page_token = f"offset:{next_offset}" if next_offset < len(scored) else None

    # Cluster G — P1: lazy survival lookup at query time. ``_build_trail_units``
    # no longer calls ``sync_patch`` per anchor (that was a 6+ git ops × 315
    # anchors = 10+ minute refresh). Instead we look up the cached survival
    # row for each git_anchor unit going into this page and patch the
    # ``trail.survival_state`` facet in-place. Misses leave the facet as
    # ``"unknown"`` and the caller can run ``trail track --patch <id>`` for
    # fresh data.
    selected_units = [unit for _score, _parts, _matched, unit in selected]
    _apply_lazy_survival(db_path, selected_units)

    candidates_page = [
        _candidate_packet(
            unit,
            score,
            score_parts,
            matched_fields,
            include_slice=include_slice,
            max_slice_nodes=max_slice_nodes,
            index_path=db_path,
        )
        for score, score_parts, matched_fields, unit in selected
    ]
    warnings = _trail_freshness_for_query(
        db_path,
        selected_units,
        project=project,
        survival=survival,
        candidate_kind=candidate_kind,
        facet_filters=facet_filters,
    )
    return QueryPage(
        candidates=candidates_page,
        next_page_token=next_page_token,
        total=len(scored),
        warnings=warnings,
    )


def _trail_freshness_for_query(
    db_path: Path,
    units: list[TraceUnit],
    *,
    project: str | None,
    survival: str | None,
    candidate_kind: str | None,
    facet_filters: tuple[str, ...],
) -> list[dict[str, Any]]:
    project_slugs = {
        unit.project_slug
        for unit in units
        if unit.project_slug and _unit_uses_trail_projection(unit)
    }
    trail_requested = (
        survival is not None
        or candidate_kind in {"patch", "git_anchor"}
        or any(_filter_name(raw).startswith("trail.") for raw in facet_filters)
        or bool(project_slugs)
    )
    if not trail_requested:
        return []
    if project and not project_slugs:
        project_slugs.add(project)
    return trail_freshness_warnings(
        index_path=db_path,
        project_slugs=project_slugs or None,
        include_current=True,
    )


def _unit_uses_trail_projection(unit: TraceUnit) -> bool:
    if unit.unit_type in {"patch", "git_anchor"}:
        return True
    if unit.trail_refs:
        return True
    return any(facet.name.startswith("trail.") for facet in unit.facets)


def _filter_name(raw: str) -> str:
    return str(raw).split("=", 1)[0].strip()


def _apply_lazy_survival(db_path: Path, units: list[TraceUnit]) -> None:
    """Patch ``trail.survival_state`` facets using the per-project cache.

    Reads each project's survival cache once (via ``read_events`` against
    the project's repo) and updates the facet in-place for every
    git_anchor unit that resolves a cached row. Misses are left alone:
    the unit keeps ``"unknown"`` and the user can run ``trail track`` to
    populate the cache.

    All work is wrapped in a top-level try/except so a missing repo or
    broken event log never breaks the query.
    """
    git_anchor_units = [unit for unit in units if unit.unit_type == "git_anchor"]
    if not git_anchor_units:
        return

    # Group by project slug so we only read the cache per-project once.
    by_project: dict[str, list[TraceUnit]] = {}
    for unit in git_anchor_units:
        if unit.project_slug:
            by_project.setdefault(unit.project_slug, []).append(unit)
    if not by_project:
        return

    # Resolve project slug → repo path once.
    repo_paths: dict[str, str] = {}
    try:
        with _connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            for slug in by_project:
                row = conn.execute(
                    "select repo_path from trail_sources where project_slug = ?",
                    (slug,),
                ).fetchone()
                if row and row["repo_path"]:
                    repo_paths[slug] = row["repo_path"]
    except sqlite3.Error:
        return

    # Lazy import to avoid trails ↔ trace_index circularity at module load.
    from .trails import build_survival_cache_index, read_events

    for slug, slug_units in by_project.items():
        repo_str = repo_paths.get(slug)
        if not repo_str:
            continue
        repo = Path(repo_str)
        if not repo.exists():
            continue
        try:
            events = read_events(repo, verify=False)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            continue
        # Resolve current HEAD once.
        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(repo),
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if not head:
            continue
        cache_index = build_survival_cache_index(events)
        for unit in slug_units:
            patch_id = None
            for facet in unit.facets:
                if facet.name == "trace_patch.id":
                    patch_id = facet.value
                    break
            if not patch_id:
                continue
            cached = cache_index.get((patch_id, head))
            if cached is None:
                continue
            survival_state = cached.get("survival_state") or "unknown"
            new_facets: list[TraceFacet] = []
            for facet in unit.facets:
                if facet.name == "trail.survival_state":
                    new_facets.append(
                        TraceFacet(
                            name=facet.name,
                            value=str(survival_state),
                            source=facet.source,
                            confidence=facet.confidence,
                            evidence_ref=facet.evidence_ref,
                        )
                    )
                else:
                    new_facets.append(facet)
            unit.facets = new_facets


def get_trace_map(trace_id: str, *, index_path: Path | None = None) -> TraceMap | None:
    db_path = index_path or default_index_path()
    if not db_path.exists():
        return None
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        nodes = [
            TraceMapNode.model_validate_json(row["payload"])
            for row in conn.execute(
                "select payload from trace_map_nodes where trace_id = ? order by ordinal",
                (trace_id,),
            )
        ]
        edges = [
            row["payload"]
            for row in conn.execute(
                "select payload from trace_map_edges where trace_id = ? order by ordinal",
                (trace_id,),
            )
        ]
    if not nodes:
        return None
    from opentraces_schema import TraceMapEdge

    return TraceMap(
        trace_id=trace_id,
        root_node_ids=[nodes[0].node_id],
        nodes=nodes,
        edges=[TraceMapEdge.model_validate_json(payload) for payload in edges],
    )


def get_unit(unit_id: str, *, index_path: Path | None = None) -> TraceUnit | None:
    db_path = index_path or default_index_path()
    if not db_path.exists():
        return None
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "select * from units where unit_id = ?",
            (unit_id,),
        ).fetchone()
    return _unit_from_row(row) if row else None


def list_units(
    *,
    index_path: Path | None = None,
    project_slug: str | None = None,
    trace_id: str | None = None,
    unit_type: str | None = None,
) -> list[TraceUnit]:
    """Return indexed Trace Units without triggering a rebuild.

    Projection builders use this as a stable read accessor over the SQLite
    cache. If callers need fresh source files folded in, they should invoke
    ``rebuild_index`` or ``refresh_index`` before calling this function.
    """

    db_path = index_path or default_index_path()
    if not db_path.exists():
        return []

    clauses: list[str] = []
    params: list[str] = []
    if project_slug is not None:
        clauses.append("project_slug = ?")
        params.append(project_slug)
    if trace_id is not None:
        clauses.append("trace_id = ?")
        params.append(trace_id)
    if unit_type is not None:
        clauses.append("unit_type = ?")
        params.append(unit_type)

    where_sql = f" where {' and '.join(clauses)}" if clauses else ""
    sql = (
        "select * from units"
        f"{where_sql}"
        " order by project_slug, trace_id, unit_type, unit_id"
    )
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = list(conn.execute(sql, params))
    return [_unit_from_row(row) for row in rows]


def list_skill_invocation_units_from_records(
    *, project_slug: str | None = None
) -> list[TraceUnit]:
    """Project latest ``skill_invocation`` TraceUnits without a full map rebuild."""

    units: list[TraceUnit] = []
    for obj in iter_trace_record_objects(project_slug=project_slug):
        record = obj.record
        slug = obj.project_slug
        facets = _skill_invocation_trace_facets(record, slug)
        title = _index_text(record.task.description or _first_user_text(record) or record.trace_id)
        units.extend(
            _propagate_supersession(
                _skill_invocation_units(record, slug, title, facets),
                record,
            )
        )
    return _latest_units(units)


def latest_units(units: list[TraceUnit]) -> list[TraceUnit]:
    """Return the latest non-superseded Trace Units using index semantics."""

    return _latest_units(units)


def _skill_invocation_trace_facets(record: TraceRecord, project_slug: str) -> list[TraceFacet]:
    """Return only the trace facets retained by ``_skill_invocation_units``."""

    facets = [
        TraceFacet(name="project_slug", value=project_slug, source="exact_schema"),
        TraceFacet(name="agent.name", value=record.agent.name, source="exact_schema"),
    ]
    provider = _provider_kind(record)
    if provider:
        facets.append(TraceFacet(name="provider.kind", value=provider, source="exact_schema"))
    if record.agent.model:
        facets.append(TraceFacet(name="model", value=record.agent.model, source="exact_schema"))
    return facets


def candidate_packet_for_unit(
    unit: TraceUnit,
    score: float,
    score_parts: dict[str, float],
    matched_fields: dict[str, list[str]],
    *,
    include_slice: str | None = None,
    max_slice_nodes: int = 40,
    index_path: Path | None = None,
) -> CandidatePacket:
    """Build a bounded CandidatePacket for a Trace Unit.

    Projection-backed query paths use this to keep packet shape identical
    to the SQLite index query path.
    """

    return _candidate_packet(
        unit,
        score,
        score_parts,
        matched_fields,
        include_slice=include_slice,
        max_slice_nodes=max_slice_nodes,
        index_path=index_path,
    )


def get_map_node(node_id: str, *, index_path: Path | None = None) -> TraceMapNode | None:
    db_path = index_path or default_index_path()
    if not db_path.exists():
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            "select payload from trace_map_nodes where node_id = ?",
            (node_id,),
        ).fetchone()
    return TraceMapNode.model_validate_json(row[0]) if row else None


def get_trace_path(trace_id: str, *, index_path: Path | None = None) -> Path | None:
    db_path = index_path or default_index_path()
    if not db_path.exists():
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            "select trace_path from traces where trace_id = ?",
            (trace_id,),
        ).fetchone()
    return Path(row[0]) if row else None


def trail_freshness_warnings(
    *,
    index_path: Path | None = None,
    project_slugs: set[str] | None = None,
    include_current: bool = False,
) -> list[dict[str, Any]]:
    """Return Trail projection freshness entries for query/workflow callers.

    The trace index auto-refreshes trail projections when it sees the local
    TrailEvent ref move. This helper makes that contract visible: callers can
    show when the projection was last synced and warn if the event ref moved
    after the index was built.
    """

    db_path = index_path or default_index_path()
    if not db_path.exists():
        return []

    try:
        with _connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            table = conn.execute(
                "select name from sqlite_master where type = 'table' and name = 'trail_sources'"
            ).fetchone()
            if table is None:
                return []
            columns = {
                str(row["name"])
                for row in conn.execute("pragma table_info(trail_sources)")
            }
            if "indexed_at" not in columns:
                return []
            rows = list(
                conn.execute(
                    """
                    select project_slug, repo_path, ref_sha, indexed_at, limitations_json
                    from trail_sources
                    order by project_slug
                    """
                )
            )
    except sqlite3.Error:
        return []

    selected = set(project_slugs or [])
    entries: list[dict[str, Any]] = []
    for row in rows:
        project_slug = str(row["project_slug"])
        if selected and project_slug not in selected:
            continue

        repo_path = str(row["repo_path"])
        indexed_ref_sha = str(row["ref_sha"] or "")
        current_ref_sha = _trail_event_ref_sha(Path(repo_path))
        limitations = _json_list(row["limitations_json"])
        if current_ref_sha and current_ref_sha == indexed_ref_sha:
            state = "current"
            severity = "info"
            advice = None
        elif current_ref_sha:
            state = "stale"
            severity = "warning"
            advice = "opentraces trace index rebuild"
        else:
            state = "missing_event_log"
            severity = "warning"
            advice = "opentraces trail track --all"

        if severity == "info" and not include_current:
            continue

        entries.append(
            {
                "kind": "trail_projection_freshness",
                "severity": severity,
                "state": state,
                "project_slug": project_slug,
                "repo_path": repo_path,
                "last_synced_at": str(row["indexed_at"] or ""),
                "indexed_ref_sha": indexed_ref_sha,
                "current_ref_sha": current_ref_sha,
                "limitations": limitations,
                "advice": advice,
            }
        )
    return entries


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table meta (
            key text primary key,
            value text not null
        );
        create table sources (
            path text primary key,
            mtime_ns integer not null,
            size integer not null,
            record_count integer not null
        );
        create table trail_sources (
            project_slug text primary key,
            repo_path text not null,
            ref_sha text not null,
            indexed_at text not null,
            limitations_json text not null default '[]'
        );
        create table traces (
            trace_id text primary key,
            project_slug text not null,
            session_id text not null,
            generation_index integer not null,
            trace_path text not null,
            title text not null
        );
        create table units (
            unit_id text primary key,
            unit_type text not null,
            trace_id text not null,
            project_slug text not null,
            title_text text not null,
            intent_text text not null,
            action_text text not null,
            evidence_text text not null,
            artifact_text text not null,
            files_json text not null,
            skills_json text not null,
            facets_json text not null,
            signals_json text not null,
            metadata_json text not null,
            trail_refs_json text not null
        );
        create virtual table units_fts using fts5(
            title_text,
            intent_text,
            action_text,
            evidence_text,
            artifact_text,
            content='units',
            content_rowid='rowid'
        );
        create table facets (
            unit_id text not null,
            name text not null,
            value text not null
        );
        create table signals (
            unit_id text not null,
            name text not null,
            value text not null
        );
        create table trace_map_nodes (
            node_id text primary key,
            trace_id text not null,
            ordinal integer not null,
            payload text not null
        );
        create table trace_map_edges (
            edge_id text primary key,
            trace_id text not null,
            ordinal integer not null,
            payload text not null
        );
        create index idx_units_project_type on units(project_slug, unit_type);
        """
    )


def _iter_project_homes() -> list[Path]:
    if not paths.PROJECTS_DIR.exists():
        return []
    return sorted(path for path in paths.PROJECTS_DIR.iterdir() if path.is_dir())


STAGING_PROJECT_SLUG = "_staging"


@dataclass(frozen=True)
class TraceSource:
    layer: str            # "canonical" | "staging"
    project_slug: str
    trace_path: Path


def _iter_trace_sources() -> list[TraceSource]:
    """Yield every TraceRecord object the index should ingest, tagged by layer.

    Bundle C / Bug #1: projects/<slug>/traces/*.jsonl is the canonical layer
    (per-project, opted-in) and the new top-level staging/*.jsonl is the
    Plan 58 default-inbox staging layer. Both must be indexed so query callers
    do not silently miss staged-but-unmoved traces.
    """
    sync_trace_records_from_local_stores()
    bucket_sources = [
        TraceSource(obj.source_layer, obj.project_slug, obj.path)
        for obj in iter_trace_record_objects()
    ]
    if bucket_sources:
        return sorted(bucket_sources, key=lambda source: str(source.trace_path))

    sources: list[TraceSource] = []
    for project_home in _iter_project_homes():
        slug = project_home.name
        for trace_path in _iter_trace_paths(project_home):
            sources.append(TraceSource("canonical", slug, trace_path))
    staging_root = getattr(paths, "STAGING_DIR", None)
    if staging_root and staging_root.exists() and staging_root.is_dir():
        for trace_path in sorted(staging_root.glob("*.jsonl")):
            sources.append(TraceSource("staging", STAGING_PROJECT_SLUG, trace_path))
    return sources


def _schema_supports_refresh(conn: sqlite3.Connection) -> bool:
    try:
        version = conn.execute(
            "select value from meta where key = 'index_version'"
        ).fetchone()
        sources = conn.execute(
            "select name from sqlite_master where type = 'table' and name = 'sources'"
        ).fetchone()
        trail_sources = conn.execute(
            "select name from sqlite_master where type = 'table' and name = 'trail_sources'"
        ).fetchone()
    except sqlite3.Error:
        return False
    return bool(version and version[0] == INDEX_VERSION and sources and trail_sources)


def _index_totals(conn: sqlite3.Connection, db_path: Path) -> RebuildSummary:
    trace_count = int(conn.execute("select count(*) from traces").fetchone()[0])
    unit_count = int(conn.execute("select count(*) from units").fetchone()[0])
    map_node_count = int(conn.execute("select count(*) from trace_map_nodes").fetchone()[0])
    return RebuildSummary(
        index_path=db_path,
        trace_count=trace_count,
        unit_count=unit_count,
        map_node_count=map_node_count,
    )


def _record_source(conn: sqlite3.Connection, trace_path: Path, record_count: int) -> None:
    stat = trace_path.stat()
    conn.execute(
        """
        insert or replace into sources(path, mtime_ns, size, record_count)
        values (?, ?, ?, ?)
        """,
        (str(trace_path), stat.st_mtime_ns, stat.st_size, record_count),
    )


def _rebuild_trail_projection(
    conn: sqlite3.Connection,
    project_slug: str,
    source_repo: Path,
) -> None:
    """Rebuild the trail projection rows for ``project_slug``.

    Cluster A — A1: previously ``_build_trail_units`` swallowed every
    OSError/RuntimeError/ValueError/ValidationError/SubprocessError and
    returned ``[]``, after which ``_record_trail_source`` was *still*
    called. The cache then claimed it was fresh while holding zero units;
    the next refresh saw a matching ref_sha and skipped the rebuild,
    persisting the bad state forever. Now ``_build_trail_units`` raises
    those errors; we catch here, leave ``trail_sources`` untouched
    (intentional: next refresh retries), and propagate nothing further.

    Cluster A — A2: capture the post-rebuild ref. If the trail event log
    advanced *during* the rebuild (because the watcher / post-commit hook
    appended new events), record the post-rebuild head and tag the row
    with the structured ``trail_event_ref_advanced_during_rebuild``
    limitation so consumers can detect the case.
    """
    pre_ref_sha = _trail_event_ref_sha(source_repo)
    if not pre_ref_sha:
        conn.execute("delete from trail_sources where project_slug = ?", (project_slug,))
        return

    try:
        trail_units = _build_trail_units(source_repo, project_slug)
    except (OSError, RuntimeError, ValueError, ValidationError, subprocess.SubprocessError):
        # Build failed — leave trail_sources untouched so the next refresh
        # retries instead of marking a stale-but-empty cache as fresh.
        return
    trail_units = _disambiguate_trail_unit_collisions(conn, project_slug, trail_units)

    for trail_map in _trail_maps_from_units(trail_units):
        _insert_trail_map(conn, trail_map)
    for unit in trail_units:
        _insert_unit(conn, unit)

    post_ref_sha = _trail_event_ref_sha(source_repo) or pre_ref_sha
    limitations: list[str] = []
    if post_ref_sha != pre_ref_sha:
        limitations.append("trail_event_ref_advanced_during_rebuild")
    _record_trail_source(
        conn, project_slug, source_repo, post_ref_sha, limitations=limitations
    )


def _refresh_trail_projection(
    conn: sqlite3.Connection,
    project_slug: str,
    source_repo: Path,
) -> None:
    ref_sha = _trail_event_ref_sha(source_repo)
    repo_path = str(source_repo.resolve())
    existing = conn.execute(
        "select repo_path, ref_sha from trail_sources where project_slug = ?",
        (project_slug,),
    ).fetchone()
    if existing and existing["repo_path"] == repo_path and existing["ref_sha"] == (ref_sha or ""):
        return

    _delete_trail_projection_for_project(conn, project_slug)
    if ref_sha:
        _rebuild_trail_projection(conn, project_slug, source_repo)
    else:
        conn.execute("delete from trail_sources where project_slug = ?", (project_slug,))


def _record_trail_source(
    conn: sqlite3.Connection,
    project_slug: str,
    source_repo: Path,
    ref_sha: str,
    *,
    limitations: list[str] | None = None,
) -> None:
    conn.execute(
        """
        insert or replace into trail_sources(project_slug, repo_path, ref_sha, indexed_at, limitations_json)
        values (?, ?, ?, ?, ?)
        """,
        (
            project_slug,
            str(source_repo.resolve()),
            ref_sha,
            _utc_now(),
            json.dumps(sorted(set(limitations or []))),
        ),
    )


def _resolve_git_dir(source_repo: Path) -> Path:
    """Resolve the git dir for ``source_repo`` without spawning git.

    Handles a normal ``.git`` directory, a worktree/submodule ``.git`` FILE
    (one-line ``gitdir: <path>`` pointer), and a bare repo (no ``.git``).

    For a linked worktree the per-worktree gitdir holds only per-worktree
    refs (HEAD, etc.); shared refs like the opentraces event log live in the
    *common* gitdir pointed at by ``commondir``. The event-log ref is a
    shared ref, so we follow ``commondir`` when present and return the
    common dir for ref lookups.
    """
    dot_git = source_repo / ".git"
    gitdir: Path
    if dot_git.is_dir():
        gitdir = dot_git
    elif dot_git.is_file():
        gitdir = source_repo
        text = dot_git.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("gitdir:"):
                pointer = line[len("gitdir:"):].strip()
                ptr_path = Path(pointer)
                if not ptr_path.is_absolute():
                    ptr_path = (source_repo / ptr_path).resolve()
                gitdir = ptr_path
                break
    else:
        # No .git entry: treat source_repo itself as a (possibly bare) gitdir.
        gitdir = source_repo

    # Linked worktrees store shared refs in the common gitdir.
    commondir = gitdir / "commondir"
    if commondir.is_file():
        common = commondir.read_text(encoding="utf-8", errors="replace").strip()
        if common:
            common_path = Path(common)
            if not common_path.is_absolute():
                common_path = (gitdir / common_path).resolve()
            return common_path
    return gitdir


def _trail_event_ref_sha(source_repo: Path) -> str | None:
    from .trails import EVENT_LOG_REF

    try:
        gitdir = _resolve_git_dir(source_repo)

        # 1) Loose ref file: gitdir/refs/opentraces/local/events/v1
        loose = gitdir
        for part in EVENT_LOG_REF.split("/"):
            loose = loose / part
        if loose.exists():
            sha = loose.read_text(encoding="utf-8", errors="replace").strip()
            return sha or None

        # 2) packed-refs fallback.
        packed = gitdir / "packed-refs"
        if packed.exists():
            for raw in packed.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or line.startswith("^"):
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[1] == EVENT_LOG_REF:
                    return parts[0] or None

        # 3) Neither yields the ref — a missing event-log ref is a normal
        #    state (project never captured a trail).
        return None
    except (OSError, ValueError):
        # Last-resort fallback for unusual/unreadable layouts only.
        try:
            proc = subprocess.run(
                ["git", "show-ref", "--hash", EVENT_LOG_REF],
                cwd=source_repo,
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, ValueError):
            return None
        if proc.returncode != 0:
            return None
        ref_sha = proc.stdout.strip()
        return ref_sha or None


def _delete_trace_ids(conn: sqlite3.Connection, trace_ids: list[str]) -> None:
    ids = sorted({trace_id for trace_id in trace_ids if trace_id})
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    _delete_units_where(conn, f"trace_id in ({placeholders})", ids)
    conn.execute(f"delete from traces where trace_id in ({placeholders})", ids)
    conn.execute(f"delete from trace_map_nodes where trace_id in ({placeholders})", ids)
    conn.execute(f"delete from trace_map_edges where trace_id in ({placeholders})", ids)


def _delete_units_by_types(conn: sqlite3.Connection, unit_types: set[str]) -> None:
    if not unit_types:
        return
    values = sorted(unit_types)
    placeholders = ",".join("?" for _ in values)
    _delete_units_where(conn, f"unit_type in ({placeholders})", values)


def _project_has_trail_rows(conn: sqlite3.Connection, project_slug: str) -> bool:
    """R3 guard: does this project have any trail projection to prune?

    Two cheap indexed EXISTS probes: a trail_sources PK lookup and a
    units(project_slug, unit_type) covering-index scan for patch/git_anchor
    units. Returns True if either has a row, so the caller only runs the
    expensive delete sweep when there is something to delete.
    """
    has_source = conn.execute(
        "select 1 from trail_sources where project_slug = ? limit 1",
        (project_slug,),
    ).fetchone()
    if has_source is not None:
        return True
    has_units = conn.execute(
        """
        select 1 from units
        where project_slug = ? and unit_type in ('patch', 'git_anchor')
        limit 1
        """,
        (project_slug,),
    ).fetchone()
    return has_units is not None


def _delete_trail_projection_for_project(conn: sqlite3.Connection, project_slug: str) -> None:
    trace_ids = [
        str(row[0])
        for row in conn.execute(
            """
            select distinct trace_id from units
            where project_slug = ? and unit_type in ('patch', 'git_anchor')
            """,
            (project_slug,),
        )
    ]
    _delete_units_where(
        conn,
        "project_slug = ? and unit_type in ('patch', 'git_anchor')",
        [project_slug],
    )
    _delete_trail_maps_for_trace_ids(conn, trace_ids)


def _delete_units_where(conn: sqlite3.Connection, clause: str, params: list[str]) -> None:
    rows = list(conn.execute(f"select rowid, unit_id from units where {clause}", params))
    for rowid, unit_id in rows:
        conn.execute("delete from units_fts where rowid = ?", (rowid,))
        conn.execute("delete from facets where unit_id = ?", (unit_id,))
        conn.execute("delete from signals where unit_id = ?", (unit_id,))
        conn.execute("delete from units where unit_id = ?", (unit_id,))


def _delete_trail_maps_for_trace_ids(conn: sqlite3.Connection, trace_ids: list[str]) -> None:
    ids = sorted({trace_id for trace_id in trace_ids if trace_id})
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    conn.execute(
        f"delete from trace_map_edges where trace_id in ({placeholders}) and edge_id like 'tme:%:trail:%'",
        ids,
    )
    conn.execute(
        f"delete from trace_map_nodes where trace_id in ({placeholders}) and node_id like 'tmn:%:trail:%'",
        ids,
    )


def _project_sources_by_slug() -> dict[str, Path]:
    try:
        from .config import load_config

        cfg = load_config()
    except (OSError, ValueError, json.JSONDecodeError, ValidationError):
        return {}
    out: dict[str, Path] = {}
    for project_path, registration in getattr(cfg, "projects", {}).items():
        slug = getattr(registration, "slug", None)
        if slug:
            out[str(slug)] = Path(project_path)
    return out


def _trail_projection_for_project(
    project_slug: str,
    project_sources: dict[str, Path],
    cache: dict[str, TrailQueryProjection | None],
) -> TrailQueryProjection | None:
    if project_slug in cache:
        return cache[project_slug]
    source_repo = project_sources.get(project_slug)
    if source_repo is None or not source_repo.exists():
        cache[project_slug] = None
        return None
    try:
        projection = build_trail_query_projection(source_repo)
    except Exception:
        projection = None
    cache[project_slug] = projection
    return projection


def _iter_trace_records(project_home: Path) -> list[tuple[TraceRecord, Path]]:
    records: list[tuple[TraceRecord, Path]] = []
    for trace_path in _iter_trace_paths(project_home):
        records.extend((record, trace_path) for record in _iter_trace_file_records(trace_path))
    return records


def _iter_trace_paths(project_home: Path) -> list[Path]:
    traces_dir = project_home / "traces"
    if not traces_dir.exists():
        return []
    return sorted(traces_dir.glob("*.jsonl"))


def _iter_trace_file_records(trace_path: Path) -> list[TraceRecord]:
    bucket_obj = read_trace_record_object(trace_path)
    if bucket_obj is not None:
        return [bucket_obj.record]
    records: list[TraceRecord] = []
    for line in trace_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            records.append(load_record_json(line))
        except (ValueError, json.JSONDecodeError, ValidationError):
            continue
    return records


def _insert_trace(
    conn: sqlite3.Connection,
    record: TraceRecord,
    project_slug: str,
    trace_path: Path,
    trace_map: TraceMap,
) -> None:
    title = _index_text(summary_for_record(record, fallback=_first_user_text(record)) or record.trace_id)
    conn.execute(
        """
        insert into traces(trace_id, project_slug, session_id, generation_index, trace_path, title)
        values (?, ?, ?, ?, ?, ?)
        """,
        (
            record.trace_id,
            project_slug,
            record.session_id,
            record.generation_index,
            str(trace_path),
            title,
        ),
    )
    for ordinal, node in enumerate(trace_map.nodes, 1):
        conn.execute(
            """
            insert into trace_map_nodes(node_id, trace_id, ordinal, payload)
            values (?, ?, ?, ?)
            """,
            (node.node_id, node.trace_id, ordinal, node.model_dump_json()),
        )
    for ordinal, edge in enumerate(trace_map.edges, 1):
        conn.execute(
            """
            insert into trace_map_edges(edge_id, trace_id, ordinal, payload)
            values (?, ?, ?, ?)
            """,
            (edge.edge_id, edge.trace_id, ordinal, edge.model_dump_json()),
        )


def _insert_trail_map(conn: sqlite3.Connection, trace_map: TraceMap) -> None:
    node_offset = int(
        conn.execute(
            "select coalesce(max(ordinal), 0) from trace_map_nodes where trace_id = ?",
            (trace_map.trace_id,),
        ).fetchone()[0]
    )
    edge_offset = int(
        conn.execute(
            "select coalesce(max(ordinal), 0) from trace_map_edges where trace_id = ?",
            (trace_map.trace_id,),
        ).fetchone()[0]
    )
    for ordinal, node in enumerate(trace_map.nodes, node_offset + 1):
        conn.execute(
            """
            insert or replace into trace_map_nodes(node_id, trace_id, ordinal, payload)
            values (?, ?, ?, ?)
            """,
            (node.node_id, node.trace_id, ordinal, node.model_dump_json()),
        )
    for ordinal, edge in enumerate(trace_map.edges, edge_offset + 1):
        conn.execute(
            """
            insert or replace into trace_map_edges(edge_id, trace_id, ordinal, payload)
            values (?, ?, ?, ?)
            """,
            (edge.edge_id, edge.trace_id, ordinal, edge.model_dump_json()),
        )


def _insert_unit(conn: sqlite3.Connection, unit: TraceUnit) -> None:
    facets = _unit_facets(unit)
    conn.execute(
        """
        insert into units(
            unit_id, unit_type, trace_id, project_slug,
            title_text, intent_text, action_text, evidence_text, artifact_text,
            files_json, skills_json, facets_json, signals_json, metadata_json, trail_refs_json
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            unit.unit_id,
            unit.unit_type,
            unit.trace_id,
            unit.project_slug,
            unit.title_text,
            unit.intent_text,
            unit.action_text,
            unit.evidence_text,
            unit.artifact_text,
            json.dumps(unit.files),
            json.dumps(unit.skills),
            json.dumps([facet.model_dump(mode="json") for facet in facets]),
            json.dumps([signal.model_dump(mode="json") for signal in unit.signals]),
            json.dumps(unit.metadata),
            json.dumps(unit.trail_refs),
        ),
    )
    rowid = conn.execute("select last_insert_rowid()").fetchone()[0]
    conn.execute(
        """
        insert into units_fts(
            rowid, title_text, intent_text, action_text, evidence_text, artifact_text
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (
            rowid,
            unit.title_text,
            unit.intent_text,
            unit.action_text,
            unit.evidence_text,
            unit.artifact_text,
        ),
    )
    for facet in facets:
        conn.execute(
            "insert into facets(unit_id, name, value) values (?, ?, ?)",
            (unit.unit_id, facet.name, str(facet.value)),
        )
    for signal in unit.signals:
        conn.execute(
            "insert into signals(unit_id, name, value) values (?, ?, ?)",
            (unit.unit_id, signal.name, json.dumps(signal.value)),
        )


def _unit_facets(unit: TraceUnit) -> list[TraceFacet]:
    facets = list(unit.facets)
    if not any(facet.name == "unit.type" for facet in facets):
        facets.append(TraceFacet(name="unit.type", value=unit.unit_type, source="exact_schema"))
    return facets


def _build_units(
    record: TraceRecord,
    trace_map: TraceMap,
    project_slug: str,
    *,
    layer: str = "canonical",
) -> list[TraceUnit]:
    files = _trace_files(trace_map)
    skills = _trace_skills(record)
    tools = _trace_tools(record)
    test_commands = _test_commands(record)
    signals = _trace_signals(record, trace_map)
    facets = _trace_facets(record, project_slug, skills, tools, files)
    facets.append(TraceFacet(name="source_layer", value=layer, source="exact_schema"))
    display_summary = summary_for_record(record, fallback=_first_user_text(record))
    display_headline = headline_from_summary(display_summary)
    intent_text = _index_text(intent_text_for_record(record) or display_summary)
    provenance = provenance_from_record(record)
    title = _index_text(display_headline or record.task.description or _first_user_text(record) or record.trace_id)
    summary_metadata = {
        "summary": display_summary,
        "headline": display_headline,
        "provenance_color": provenance.get("provenance_color"),
        "outcome_commit_sha": provenance.get("commit_sha"),
        "commit_subject": provenance.get("commit_subject"),
    }
    unit = TraceUnit(
        unit_id=f"tu:{record.trace_id}:trace",
        unit_type="trace",
        trace_id=record.trace_id,
        project_slug=project_slug,
        files=files,
        skills=skills,
        title_text=title,
        intent_text=intent_text,
        action_text=" ".join(_tool_texts(record)),
        evidence_text=" ".join(_observation_summaries(record)),
        artifact_text=" ".join([*files, *record.dependencies]),
        facets=facets,
        signals=signals,
        metadata={
            "session_id": record.session_id,
            "generation_index": record.generation_index,
            "timestamp_start": record.timestamp_start,
            "timestamp_end": record.timestamp_end,
            "test_commands": test_commands,
            **summary_metadata,
            "map_node_refs": [
                node.node_id
                for node in trace_map.nodes
                if node.action_type
                in {"user_instruction", "agent_plan", "file_edit", "test_run", "final_response"}
            ],
        },
    )
    units = [
        unit,
        _trace_intent_unit(record, project_slug, title, facets, signals),
        _trace_slice_unit(record, trace_map, project_slug, title, facets, signals),
        _tool_sequence_unit(record, project_slug, title, facets),
    ]
    units.extend(_skill_invocation_units(record, project_slug, title, facets))
    for node in trace_map.nodes:
        units.append(
            TraceUnit(
                unit_id=node.unit_id,
                unit_type="trace_map_node",
                trace_id=record.trace_id,
                project_slug=project_slug,
                files=[*node.files_read, *node.files_modified],
                skills=skills if node.action_type == "skill_invocation" else [],
                title_text=title,
                intent_text=intent_text,
                action_text=node.text_preview or "",
                evidence_text=node.text_preview or "",
                artifact_text=" ".join([*node.files_read, *node.files_modified]),
                facets=facets if node.action_type == "skill_invocation" else [],
                signals=[],
                metadata={
                    "node_id": node.node_id,
                    "session_id": record.session_id,
                    "generation_index": record.generation_index,
                    "timestamp_start": record.timestamp_start,
                    "timestamp_end": record.timestamp_end,
                    **summary_metadata,
                },
            )
        )
    if any(signal.name == "tested_successful_fix_candidate" for signal in signals):
        units.append(
            TraceUnit(
                unit_id=f"tu:{record.trace_id}:signal:tested_successful_fix_candidate",
                unit_type="test_or_error_signal",
                trace_id=record.trace_id,
                project_slug=project_slug,
                files=files,
                skills=skills,
                title_text=title,
                intent_text=intent_text,
                action_text=" ".join(test_commands),
                evidence_text="test failed then passed after edit",
                artifact_text=" ".join(files),
                facets=facets,
                signals=signals,
                metadata={
                    "session_id": record.session_id,
                    "generation_index": record.generation_index,
                    "timestamp_start": record.timestamp_start,
                    "timestamp_end": record.timestamp_end,
                    "test_commands": test_commands,
                    **summary_metadata,
                    "map_node_refs": unit.metadata["map_node_refs"],
                },
            )
        )
    units = _propagate_supersession(units, record)
    return _rollup_child_facets(units)


def _propagate_supersession(
    units: list[TraceUnit], record: TraceRecord
) -> list[TraceUnit]:
    """Bundle S / Bug #3 helper: hoist record-level supersession onto units.

    ``_latest_units`` runs over indexed :class:`TraceUnit` rows, so the
    writer-declared supersession markers must live in unit metadata. Without
    this propagation a record-scope ``metadata.superseded_by`` is invisible
    to the dedupe pass.
    """

    superseded_by = record.metadata.get("superseded_by")
    superseded_ids = record.metadata.get("superseded_trace_ids")
    if not superseded_by and not superseded_ids:
        return units
    new_units: list[TraceUnit] = []
    for unit in units:
        merged = dict(unit.metadata)
        if superseded_by and "superseded_by" not in merged:
            merged["superseded_by"] = superseded_by
        if superseded_ids and "superseded_trace_ids" not in merged:
            merged["superseded_trace_ids"] = list(superseded_ids)
        new_units.append(unit.model_copy(update={"metadata": merged}))
    return new_units


def _rollup_child_facets(units: list[TraceUnit]) -> list[TraceUnit]:
    """Bundle C / Bug #2: roll up every child unit's facets onto the parent trace.

    Default queries return one row per trace (unit_type='trace'). For the
    rollup to be lossless, the trace unit's facet list must be a superset of
    every child unit's facets. Child units keep their own facet lists for
    `--candidate-kind` escape-hatch queries.
    """

    if not units:
        return units
    parent_idx = next((i for i, u in enumerate(units) if u.unit_type == "trace"), None)
    if parent_idx is None:
        return units
    parent = units[parent_idx]
    seen: set[tuple[str, str]] = {(f.name, str(f.value)) for f in parent.facets}
    rolled: list[TraceFacet] = list(parent.facets)
    for unit in units:
        if unit is parent:
            continue
        for facet in unit.facets:
            key = (facet.name, str(facet.value))
            if key in seen:
                continue
            seen.add(key)
            rolled.append(facet)
    units[parent_idx] = parent.model_copy(update={"facets": rolled})
    return units


def _trace_intent_unit(
    record: TraceRecord,
    project_slug: str,
    title: str,
    facets: list[TraceFacet],
    signals: list[TraceSignal],
) -> TraceUnit:
    display_summary = summary_for_record(record, fallback=_first_user_text(record))
    return TraceUnit(
        unit_id=f"tu:{record.trace_id}:intent",
        unit_type="trace_intent_candidate",
        trace_id=record.trace_id,
        project_slug=project_slug,
        files=[],
        skills=_trace_skills(record),
        title_text=title,
        intent_text=_index_text(intent_text_for_record(record) or display_summary),
        action_text="",
        evidence_text="intent candidate derived from task and first user instruction",
        artifact_text="",
        facets=facets,
        signals=signals,
        metadata={
            "session_id": record.session_id,
            "generation_index": record.generation_index,
            "timestamp_start": record.timestamp_start,
            "timestamp_end": record.timestamp_end,
            "summary": display_summary,
            "headline": headline_from_summary(display_summary),
        },
    )


def _trace_slice_unit(
    record: TraceRecord,
    trace_map: TraceMap,
    project_slug: str,
    title: str,
    facets: list[TraceFacet],
    signals: list[TraceSignal],
) -> TraceUnit:
    display_summary = summary_for_record(record, fallback=_first_user_text(record))
    selected_nodes = [
        node
        for node in trace_map.nodes
        if node.action_type
        in {"user_instruction", "agent_plan", "file_edit", "test_run", "final_response"}
    ][:12]
    files = sorted(
        dict.fromkeys(
            path
            for node in selected_nodes
            for path in [*node.files_read, *node.files_modified]
        )
    )
    return TraceUnit(
        unit_id=f"tu:{record.trace_id}:slice:intent",
        unit_type="trace_slice",
        trace_id=record.trace_id,
        project_slug=project_slug,
        files=files,
        skills=_trace_skills(record),
        title_text=title,
        intent_text=_index_text(intent_text_for_record(record) or display_summary),
        action_text=" ".join(node.action_type for node in selected_nodes),
        evidence_text="bounded intent-to-evidence Trace Map slice",
        artifact_text=" ".join(files),
        facets=facets,
        signals=signals,
        metadata={
            "session_id": record.session_id,
            "generation_index": record.generation_index,
            "timestamp_start": record.timestamp_start,
            "timestamp_end": record.timestamp_end,
            "slice_kind": "intent",
            "summary": display_summary,
            "headline": headline_from_summary(display_summary),
            "map_node_refs": [node.node_id for node in selected_nodes],
        },
    )


def _skill_invocation_units(
    record: TraceRecord,
    project_slug: str,
    title: str,
    trace_facets: list[TraceFacet],
) -> list[TraceUnit]:
    units: list[TraceUnit] = []
    display_summary = summary_for_record(record, fallback=_first_user_text(record))
    summary_metadata = {
        "summary": display_summary,
        "headline": headline_from_summary(display_summary),
    }
    seen_invocations: set[tuple[str, str | None, int | None, str]] = set()
    for invocation in detect_skill_invocations(record):
        dedupe_key = (
            invocation.skill_name,
            invocation.tool_call_id,
            invocation.metadata_index,
            invocation.args,
        )
        if dedupe_key in seen_invocations:
            continue
        seen_invocations.add(dedupe_key)
        skill_name = invocation.skill_name
        facets = [
            facet
            for facet in trace_facets
            if facet.name in {"project_slug", "agent.name", "model", "provider.kind"}
        ]
        facets.append(TraceFacet(name="skill.name", value=skill_name, source="exact_schema"))
        unit_id = _skill_invocation_unit_id(record, invocation)
        command_name = invocation.command_name or f"/{skill_name}"
        command_args = invocation.args
        evidence_refs = (
            [f"tu:{record.trace_id}:tool:{invocation.tool_call_id}"]
            if invocation.tool_call_id
            else []
        )
        action_text = _skill_invocation_action_text(invocation)
        intent_source = (
            record.task.description or _first_user_text(record) or ""
            if invocation.tool_call_id
            else command_args or record.task.description or _first_user_text(record) or ""
        )
        units.append(
            TraceUnit(
                unit_id=unit_id,
                unit_type="skill_invocation",
                trace_id=record.trace_id,
                project_slug=project_slug,
                skills=[skill_name],
                title_text=f"Skill invocation {skill_name}",
                intent_text=_index_text(intent_source),
                action_text=action_text,
                evidence_text=f"skill.name={skill_name}",
                facets=facets,
                signals=[
                    TraceSignal(
                        name="skill_invoked",
                        value=[skill_name],
                        confidence="high",
                        evidence_refs=evidence_refs,
                    )
                ],
                metadata={
                    **_skill_invocation_metadata(
                        record,
                        invocation,
                        command_name=command_name,
                        command_args=command_args,
                    ),
                    **summary_metadata,
                },
            )
        )
    return units


def _skill_invocation_unit_id(record: TraceRecord, invocation: SkillInvocation) -> str:
    if invocation.tool_call_id:
        return f"tu:{record.trace_id}:skill:{invocation.tool_call_id}"
    idx = invocation.metadata_index if invocation.metadata_index is not None else 0
    return f"tu:{record.trace_id}:skill:metadata:{idx}"


def _skill_invocation_action_text(invocation: SkillInvocation) -> str:
    if invocation.tool_call_id:
        return " ".join(
            part for part in [invocation.tool_name or "Skill", invocation.skill_name] if part
        )
    command_name = invocation.command_name or f"/{invocation.skill_name}"
    return " ".join(part for part in [command_name, invocation.args] if part)


def _skill_invocation_metadata(
    record: TraceRecord,
    invocation: SkillInvocation,
    *,
    command_name: str,
    command_args: str,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "session_id": record.session_id,
        "generation_index": record.generation_index,
        "timestamp_start": record.timestamp_start,
        "timestamp_end": record.timestamp_end,
        "source": invocation.source,
        "skill_name": invocation.skill_name,
    }
    if invocation.step_index is not None:
        metadata["step_index"] = invocation.step_index
    if invocation.tool_call_id:
        metadata["tool_call_id"] = invocation.tool_call_id
    if not invocation.tool_call_id:
        metadata.update(
            {
                "command_name": command_name,
                "command_args": command_args,
                "command_line_no": invocation.evidence.get("command_line_no"),
                "body_line_no": invocation.evidence.get("body_line_no"),
            }
        )
    return metadata


def _tool_sequence_unit(
    record: TraceRecord,
    project_slug: str,
    title: str,
    facets: list[TraceFacet],
) -> TraceUnit:
    tools = [call.tool_name for step in record.steps for call in step.tool_calls]
    display_summary = summary_for_record(record, fallback=_first_user_text(record))
    return TraceUnit(
        unit_id=f"tu:{record.trace_id}:tool-sequence",
        unit_type="tool_sequence",
        trace_id=record.trace_id,
        project_slug=project_slug,
        files=[],
        skills=_trace_skills(record),
        title_text=f"Tool sequence for {title}",
        intent_text=_index_text(intent_text_for_record(record) or display_summary),
        action_text=" ".join(tools),
        evidence_text=f"{len(tools)} tool calls",
        artifact_text="",
        facets=facets,
        metadata={
            "session_id": record.session_id,
            "generation_index": record.generation_index,
            "timestamp_start": record.timestamp_start,
            "timestamp_end": record.timestamp_end,
            "tools": tools,
            "summary": display_summary,
            "headline": headline_from_summary(display_summary),
        },
    )


def _build_trail_units(repo: Path, project_slug: str) -> list[TraceUnit]:
    """Project the canonical TrailEvent log into queryable Trace Units.

    Cluster A — A1: this used to ``except`` every error and silently
    return ``[]``. The bare-except is gone: callers (only
    ``_rebuild_trail_projection``) now catch and handle failures
    explicitly so the cache is never recorded as ``fresh-but-empty``.
    Tests pin the contract.
    """
    from .trails import build_trail_query_projection

    projection = build_trail_query_projection(repo)

    units: list[TraceUnit] = []
    for patch in projection.patches_by_id.values():
        trace_id = patch.get("trace_id")
        patch_id = patch.get("trace_patch_id")
        if not trace_id or not patch_id:
            continue
        file_path = patch.get("file_path") or patch.get("path")
        commit_sha = patch.get("commit_sha")
        facets = _trail_facets(patch)
        signals = [
            TraceSignal(
                name="trail_patch_created",
                value=True,
                confidence="high",
                evidence_refs=list(_trail_refs(patch)),
            )
        ]
        if "git_anchor_unknown" in (patch.get("limitations") or []):
            signals.append(
                TraceSignal(
                    name="trail_anchor_unknown",
                    value=True,
                    confidence="medium",
                    evidence_refs=list(_trail_refs(patch)),
                )
            )
        units.append(
            TraceUnit(
                unit_id=f"tu:{trace_id}:patch:{patch_id}",
                unit_type="patch",
                trace_id=trace_id,
                project_slug=project_slug,
                files=[file_path] if file_path else [],
                title_text=f"Trace patch {patch_id}",
                action_text=" ".join(
                    str(value)
                    for value in (
                        file_path,
                        patch.get("relation"),
                        patch.get("evidence_tier"),
                        patch.get("evidence_firmness"),
                    )
                    if value
                ),
                evidence_text=" ".join(
                    str(value)
                    for value in (
                        patch_id,
                        patch.get("relation"),
                        commit_sha,
                        " ".join(patch.get("limitations") or []),
                    )
                    if value
                ),
                artifact_text=" ".join(str(value) for value in (file_path, commit_sha) if value),
                facets=facets,
                signals=signals,
                metadata=_trail_metadata(patch),
                trail_refs=list(_trail_refs(patch)),
            )
        )

    for anchor in projection.anchors_by_id.values():
        trace_id = anchor.get("trace_id")
        anchor_id = anchor.get("git_anchor_id")
        patch_id = anchor.get("trace_patch_id")
        if not trace_id or not anchor_id:
            continue
        file_path = anchor.get("file_path") or anchor.get("path")
        commit_sha = anchor.get("commit_sha")
        # Cluster G — P1: defer the per-anchor ``sync_patch`` call out of
        # the refresh path. Calling ``with_current_survival`` here cost
        # 6+ git invocations per anchor; a project with 315 anchors took
        # 10+ minutes to refresh. Survival is now resolved lazily at
        # query-time by reading the ``patch_survival_cached`` events the
        # batch ``trail track`` flow writes. When no cache is available
        # the facet says ``"unknown"`` and the caller can run
        # ``trail track --patch <id>`` for fresh data.
        survival_row = anchor
        survival_state = "unknown"
        anchor_facets = _trail_facets(survival_row)
        anchor_facets.append(
            TraceFacet(
                name="trail.survival_state",
                value=str(survival_state),
                source="trail_projection",
            )
        )
        units.append(
            TraceUnit(
                unit_id=f"tu:{trace_id}:git-anchor:{anchor_id}",
                unit_type="git_anchor",
                trace_id=trace_id,
                project_slug=project_slug,
                files=[file_path] if file_path else [],
                title_text=f"Git anchor {anchor_id}",
                evidence_text=" ".join(
                    str(value)
                    for value in (
                        anchor_id,
                        patch_id,
                        commit_sha,
                        anchor.get("evidence_tier"),
                        anchor.get("evidence_firmness"),
                        survival_state,
                    )
                    if value
                ),
                artifact_text=" ".join(str(value) for value in (file_path, commit_sha) if value),
                facets=anchor_facets,
                signals=[
                    TraceSignal(
                        name="trail_git_anchored",
                        value=True,
                        confidence="high",
                        evidence_refs=list(_trail_refs(anchor)),
                    )
                ],
                metadata=_trail_metadata(survival_row),
                trail_refs=list(_trail_refs(anchor)),
            )
        )
    return _dedupe_units_by_id(units)


def _dedupe_units_by_id(units: list[TraceUnit]) -> list[TraceUnit]:
    """Keep trail projection rebuilds idempotent when event views duplicate ids."""

    seen: set[str] = set()
    out: list[TraceUnit] = []
    for unit in units:
        if unit.unit_id in seen:
            continue
        seen.add(unit.unit_id)
        out.append(unit)
    return out


def _disambiguate_trail_unit_collisions(
    conn: sqlite3.Connection,
    project_slug: str,
    units: list[TraceUnit],
) -> list[TraceUnit]:
    """Avoid cross-project primary-key collisions for Trail projection units.

    Historical trail unit ids were trace/patch scoped, not project scoped. Real
    buckets can contain the same trace id + patch/anchor id in more than one
    registered project, while ``units.unit_id`` is global. Keep the old ids when
    they are free (preserving normal refs), and add the project slug only when a
    different project already owns the id.
    """

    if not units:
        return units
    ids = sorted({unit.unit_id for unit in units})
    placeholders = ",".join("?" for _ in ids)
    existing: dict[str, str] = {}
    for row in conn.execute(
        f"select unit_id, project_slug from units where unit_id in ({placeholders})",
        ids,
    ):
        unit_id = row["unit_id"] if isinstance(row, sqlite3.Row) else row[0]
        owner = row["project_slug"] if isinstance(row, sqlite3.Row) else row[1]
        existing[str(unit_id)] = str(owner)
    used: set[str] = set()
    out: list[TraceUnit] = []
    for unit in units:
        owner = existing.get(unit.unit_id)
        if owner is None or owner == project_slug:
            if unit.unit_id not in used:
                used.add(unit.unit_id)
                out.append(unit)
            continue

        new_id = _project_scoped_trail_unit_id(unit, project_slug)
        suffix = 2
        while new_id in used or conn.execute(
            "select 1 from units where unit_id = ? limit 1", (new_id,)
        ).fetchone():
            new_id = f"{_project_scoped_trail_unit_id(unit, project_slug)}:{suffix}"
            suffix += 1
        metadata = dict(unit.metadata)
        metadata.setdefault("canonical_unit_id", unit.unit_id)
        out.append(unit.model_copy(update={"unit_id": new_id, "metadata": metadata}))
        used.add(new_id)
    return out


def _project_scoped_trail_unit_id(unit: TraceUnit, project_slug: str) -> str:
    safe_project = re.sub(r"[^A-Za-z0-9_.-]+", "-", project_slug).strip("-") or "project"
    if unit.unit_type == "patch":
        patch_id = unit.metadata.get("trace_patch_id") or unit.unit_id.rsplit(":", 1)[-1]
        return f"tu:{unit.trace_id}:project:{safe_project}:patch:{patch_id}"
    if unit.unit_type == "git_anchor":
        anchor_id = unit.metadata.get("git_anchor_id") or unit.unit_id.rsplit(":", 1)[-1]
        return f"tu:{unit.trace_id}:project:{safe_project}:git-anchor:{anchor_id}"
    return f"{unit.unit_id}:project:{safe_project}"


def _anchor_with_survival(
    projection: Any, anchor: dict[str, Any]
) -> dict[str, Any]:
    """Return ``anchor`` augmented with ``current_survival`` if available.

    Cluster A — A4: ``TrailQueryProjection.with_current_survival`` runs
    ``sync_patch`` against Git which is read-only but can raise on broken
    refs / interrupted writes. Wrap with a defensive try/except so the
    indexer never explodes on a single misbehaving anchor; fall back to
    a copy of the input row tagged with ``survival_state=unknown``.
    """
    try:
        return projection.with_current_survival(anchor)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        fallback = dict(anchor)
        fallback["current_survival"] = {"survival_state": "unknown"}
        fallback["survival_state"] = "unknown"
        return fallback


def _trail_facets(row: dict[str, Any]) -> list[TraceFacet]:
    facets: list[TraceFacet] = []

    def add(name: str, value: Any) -> None:
        if value is not None:
            facets.append(TraceFacet(name=name, value=str(value), source="trail_projection"))

    add("file.path", row.get("file_path") or row.get("path"))
    add("trail.relation", row.get("relation"))
    add("trail.evidence_tier", row.get("evidence_tier"))
    add("trail.evidence_firmness", row.get("evidence_firmness"))
    add("trace_patch.id", row.get("trace_patch_id"))
    add("git_anchor.id", row.get("git_anchor_id"))
    add("git.commit_sha", row.get("commit_sha"))
    return facets


def _trail_refs(row: dict[str, Any]) -> tuple[str, ...]:
    refs = row.get("resource_refs") or {}
    values = [value for value in refs.values() if value]
    trace_slice = row.get("trace_slice") or {}
    if trace_slice.get("trace_patch_ref"):
        values.append(trace_slice["trace_patch_ref"])
    if trace_slice.get("git_anchor_ref"):
        values.append(trace_slice["git_anchor_ref"])
    return tuple(dict.fromkeys(ref for value in values if (ref := _trail_ref_value(value))))


def _trail_ref_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value if value.startswith("ot://") else None
    if isinstance(value, dict):
        ref = value.get("ref")
        return str(ref) if isinstance(ref, str) and ref.startswith("ot://") else None
    return None


def _trail_metadata(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "trace_patch_id",
        "step_id",
        "step_index",
        "generation_index",
        "relation",
        "patch_status",
        "affected_range",
        "line_count",
        "git_anchor_id",
        "commit_sha",
        "commit_id",
        "evidence_tier",
        "evidence_firmness",
        "limitations",
        "source_refs",
        "range",
        "line_origin",
    )
    out = {key: row.get(key) for key in keys if row.get(key) is not None}
    # Cluster A — A4: surface the originating ``event_time`` as
    # ``timestamp_start`` / ``timestamp_end`` so ``--since`` filters can age
    # patch / git_anchor candidates the same way they age trace units.
    event_time = row.get("event_time")
    patch_event_time = row.get("trace_patch_event_time") or event_time
    if patch_event_time:
        out["timestamp_start"] = patch_event_time
    if event_time:
        out["timestamp_end"] = event_time
    return out


def _trail_maps_from_units(units: list[TraceUnit]) -> list[TraceMap]:
    by_trace: dict[str, list[TraceUnit]] = {}
    for unit in units:
        by_trace.setdefault(unit.trace_id, []).append(unit)

    maps: list[TraceMap] = []
    for trace_id, trace_units in sorted(by_trace.items()):
        ordered = sorted(
            trace_units,
            key=lambda unit: (
                int(unit.metadata.get("step_index") or 0),
                0 if unit.unit_type == "patch" else 1,
                unit.unit_id,
            ),
        )
        nodes: list[TraceMapNode] = []
        edges: list[TraceMapEdge] = []
        previous: TraceMapNode | None = None
        for ordinal, unit in enumerate(ordered, 1):
            action_type = "patch_created" if unit.unit_type == "patch" else "git_anchor"
            node = TraceMapNode(
                node_id=f"tmn:{trace_id}:trail:{ordinal}",
                trace_id=trace_id,
                unit_id=unit.unit_id,
                action_type=action_type,  # type: ignore[arg-type]
                step_index=unit.metadata.get("step_index"),
                start_step_index=unit.metadata.get("step_index"),
                end_step_index=unit.metadata.get("step_index"),
                previous_node_id=previous.node_id if previous else None,
                files_modified=unit.files if unit.unit_type == "patch" else [],
                anchor_refs=unit.trail_refs,
                text_preview=_preview(unit.title_text or unit.evidence_text),
                metadata={
                    key: unit.metadata[key]
                    for key in (
                        "trace_patch_id",
                        "git_anchor_id",
                        "commit_sha",
                        "relation",
                        "evidence_tier",
                        "evidence_firmness",
                    )
                    if key in unit.metadata
                },
            )
            unit.metadata["map_node_refs"] = [node.node_id]
            if previous:
                previous.next_node_id = node.node_id
                edges.append(
                    TraceMapEdge(
                        edge_id=f"tme:{trace_id}:trail:{len(edges) + 1}",
                        trace_id=trace_id,
                        source_node_id=previous.node_id,
                        target_node_id=node.node_id,
                        edge_type="previous_next",
                    )
                )
            nodes.append(node)
            previous = node
        if nodes:
            maps.append(
                TraceMap(
                    trace_id=trace_id,
                    root_node_ids=[nodes[0].node_id],
                    nodes=nodes,
                    edges=edges,
                    limitations=["trail_event_projection_only"],
                )
            )
    return maps


def _unit_from_row(row: sqlite3.Row) -> TraceUnit:
    return TraceUnit(
        unit_id=row["unit_id"],
        unit_type=row["unit_type"],
        trace_id=row["trace_id"],
        project_slug=row["project_slug"],
        files=json.loads(row["files_json"]),
        skills=json.loads(row["skills_json"]),
        title_text=row["title_text"],
        intent_text=row["intent_text"],
        action_text=row["action_text"],
        evidence_text=row["evidence_text"],
        artifact_text=row["artifact_text"],
        facets=[TraceFacet.model_validate(f) for f in json.loads(row["facets_json"])],
        signals=[TraceSignal.model_validate(s) for s in json.loads(row["signals_json"])],
        metadata=json.loads(row["metadata_json"]),
        trail_refs=json.loads(row["trail_refs_json"]),
    )


def _latest_units(units: list[TraceUnit]) -> list[TraceUnit]:
    """Bundle S / Bug #3: dedupe only when supersession is writer-declared.

    Plan-59 D1=(b): different content under the same ``session_id`` is **not**
    treated as supersession. Only a unit whose ``metadata.superseded_by`` is
    explicitly set by the parser/writer is dropped — that is the only signal
    we trust for "this trace was replaced." A trace that exposes a
    superseded-by chain via ``metadata.superseded_trace_ids`` (the inverse
    pointer used by some writers) is also honoured.
    """

    superseded_unit_ids: set[str] = set()
    superseded_trace_ids: set[str] = set()
    for unit in units:
        marker = unit.metadata.get("superseded_by")
        if marker:
            superseded_unit_ids.add(unit.unit_id)
        replaced = unit.metadata.get("superseded_trace_ids") or []
        if isinstance(replaced, list):
            for trace_id in replaced:
                if trace_id:
                    superseded_trace_ids.add(str(trace_id))
    if not superseded_unit_ids and not superseded_trace_ids:
        return list(units)
    return [
        unit
        for unit in units
        if unit.unit_id not in superseded_unit_ids
        and unit.trace_id not in superseded_trace_ids
    ]


def _candidate_packet(
    unit: TraceUnit,
    score: float,
    score_parts: dict[str, float],
    matched_fields: dict[str, list[str]],
    *,
    include_slice: str | None = None,
    max_slice_nodes: int = 40,
    index_path: Path | None = None,
) -> CandidatePacket:
    title = unit.title_text or unit.trace_id
    summary = _candidate_summary(unit, index_path=index_path)
    headline = headline_from_summary(summary or title)
    provenance = _candidate_provenance(unit, index_path=index_path)
    map_node_refs = [str(ref) for ref in unit.metadata.get("map_node_refs", [])]
    slice_preview = _slice_preview_for_unit(unit, max_slice_nodes, index_path) if include_slice else None
    return CandidatePacket(
        unit_id=unit.unit_id,
        unit_type=unit.unit_type,
        trace_id=unit.trace_id,
        project_slug=unit.project_slug,
        title=title,
        headline=headline or None,
        summary=summary or None,
        provenance_color=provenance.get("provenance_color"),
        committed=provenance.get("committed"),
        commit_sha=provenance.get("commit_sha"),
        commit_subject=provenance.get("commit_subject"),
        intent_preview=_preview(unit.intent_text),
        candidate_kind=_candidate_kind(unit),
        match_explanation=_match_explanation(matched_fields, unit),
        score=round(score, 3),
        score_parts={key: round(value, 3) for key, value in score_parts.items() if value},
        matched_fields=matched_fields,
        facets=_packet_facets(unit.facets),
        signals=unit.signals,
        skills=unit.skills,
        files=unit.files,
        test_commands=[str(cmd) for cmd in unit.metadata.get("test_commands", [])],
        trail_refs=unit.trail_refs,
        map_ref=f"ot://trace/{unit.trace_id}/map",
        map_node_refs=map_node_refs,
        refs={
            "trace": unit.trace_id,
            "unit": unit.unit_id,
            "map": f"ot://trace/{unit.trace_id}/map",
        },
        slice_preview=slice_preview,
        limitations=["candidate_packet_intent_only"],
        metadata={
            "session_id": unit.metadata.get("session_id"),
            "generation_index": unit.metadata.get("generation_index"),
            "include_slice": include_slice,
        },
    )


def _candidate_summary(unit: TraceUnit, *, index_path: Path | None = None) -> str:
    """Return the human-facing discovery summary for a candidate.

    New index builds persist ``metadata.summary``. For older warm indexes, load
    the bounded trace record for the returned candidate and compose the summary
    at packet time so users do not need a rebuild to stop seeing boilerplate.
    """

    metadata_summary = strip_injected(str(unit.metadata.get("summary") or ""))
    if metadata_summary:
        return metadata_summary

    trace_path = get_trace_path(unit.trace_id, index_path=index_path)
    if trace_path is not None:
        loaded = _record_summary_from_path(unit.trace_id, str(trace_path))
        if loaded:
            return loaded

    return summary_from_parts(
        task_description=unit.title_text,
        user_texts=[unit.intent_text],
        fallback=unit.intent_text or unit.title_text or unit.trace_id,
    )


def _candidate_provenance(unit: TraceUnit, *, index_path: Path | None = None) -> dict[str, Any]:
    provenance = provenance_from_unit(unit)
    if provenance.get("commit_sha") or provenance.get("provenance_color") == "reverted":
        return provenance
    trace_path = get_trace_path(unit.trace_id, index_path=index_path)
    if trace_path is not None:
        loaded = _record_provenance_from_path(unit.trace_id, str(trace_path))
        if loaded:
            return loaded
    return provenance


@lru_cache(maxsize=1024)
def _record_summary_from_path(trace_id: str, trace_path: str) -> str:
    path = Path(trace_path)
    if not path.exists():
        return ""
    try:
        records = _iter_trace_file_records(path)
    except (OSError, ValueError, ValidationError):
        return ""
    for record in records:
        if record.trace_id == trace_id:
            return summary_for_record(record, fallback=_first_user_text(record))
    return ""


@lru_cache(maxsize=1024)
def _record_provenance_from_path(trace_id: str, trace_path: str) -> dict[str, Any]:
    path = Path(trace_path)
    if not path.exists():
        return {}
    try:
        records = _iter_trace_file_records(path)
    except (OSError, ValueError, ValidationError):
        return {}
    for record in records:
        if record.trace_id == trace_id:
            return provenance_from_record(record)
    return {}


def _lexical_score(unit: TraceUnit, terms: list[str]) -> tuple[float, dict[str, list[str]]]:
    if not terms:
        return 0.0, {}
    score = 0.0
    matched: dict[str, list[str]] = {}
    for field, weight in _FIELD_WEIGHTS.items():
        field_terms = set(_terms(str(getattr(unit, field, "")).lower()))
        hits = [term for term in terms if term in field_terms]
        if hits:
            matched[field] = hits
            score += weight * len(hits)
    return score, matched


def _metadata_score(
    unit: TraceUnit,
    *,
    tool: str | None,
    files: str | None,
    file_kind: str | None,
    file_op: str | None,
    success: bool | None,
    committed: bool | None,
    candidate_kind: str | None,
    project: str | None,
    facet_filters: tuple[tuple[str, str], ...] = (),
    metadata_filters: tuple[tuple[str, str], ...] = (),
    named_filters: tuple[tuple[str, str], ...] = (),
    since: str | None = None,
) -> float:
    score = 0.0
    if tool and tool in _facet_values(unit, "tool.name"):
        score += 2.0
    if files and any(fnmatch(path, files) for path in unit.files):
        score += 2.0
    if file_kind and file_kind.lstrip(".") in _facet_values(unit, "file.kind"):
        score += 1.0
    if file_op and file_op in _facet_values(unit, "file.operation"):
        score += 1.0
    if success is not None and _bool_facet(unit, "outcome.success") is success:
        score += 1.0
    if committed is not None and _bool_facet(unit, "outcome.committed") is committed:
        score += 1.0
    if candidate_kind and _candidate_kind(unit) == candidate_kind:
        score += 2.0
    if project and unit.project_slug == project:
        score += 1.0
    score += float(len(facet_filters) + len(metadata_filters) + len(named_filters))
    if since:
        score += 1.0
    return score


def _parse_key_value_filters(filters: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    parsed: list[tuple[str, str]] = []
    for item in filters:
        if "=" not in item:
            raise ValueError("filters must use name=value syntax")
        name, value = item.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name or not value:
            raise ValueError("filters must use name=value syntax")
        parsed.append((name, value))
    return tuple(parsed)


def _matches_facet_filters(unit: TraceUnit, filters: tuple[tuple[str, str], ...]) -> bool:
    """Bundle V / Bug #12: boolean facet comparisons are case-insensitive.

    Schema-derived booleans serialize as Python ``True`` / ``False`` whose
    ``str`` is title-cased, but ``--facet outcome.success=true`` and the
    canonical ``--success`` flag both produce lowercase tokens. Without this
    fold, ``--facet outcome.success=true`` silently zero-results on every
    real trace.
    """

    for name, value in filters:
        values = _facet_values(unit, name)
        if value in values:
            continue
        lowered = value.lower()
        if lowered in {"true", "false"} and any(v.lower() == lowered for v in values):
            continue
        return False
    return True


def _matches_metadata_filters(unit: TraceUnit, filters: tuple[tuple[str, str], ...]) -> bool:
    return all(_metadata_value_matches(unit.metadata.get(name), value) for name, value in filters)


def _named_facet_filters(
    *,
    provider: str | None,
    cmd_family: str | None,
    bash_action: str | None,
    test_framework: str | None,
    service: str | None,
    service_channel: str | None,
    dependency: str | None,
    git_tier: str | None,
    survival: str | None,
) -> tuple[tuple[str, str], ...]:
    filters: list[tuple[str, str]] = []
    for name, value in (
        ("provider.kind", provider),
        ("bash.command_family", cmd_family),
        ("bash.action", bash_action),
        ("test.framework", test_framework),
        ("service.name", service),
        ("service.channel", service_channel),
        ("dependency.name", dependency),
        ("git_link_tier", git_tier),
        ("survival.state", survival),
    ):
        if value:
            filters.append((name, value))
    return tuple(filters)


def _requested_unit_type(
    facet_filters: tuple[tuple[str, str], ...],
    candidate_kind: str | None,
) -> str | None:
    for name, value in facet_filters:
        if name == "unit.type" and value in _M1_UNIT_TYPES:
            return value
    if candidate_kind in _M1_UNIT_TYPES:
        return candidate_kind
    return None


def _count_index_units(db_path: Path, unit_type: str) -> int:
    """Total indexed units of a type - only called under OT_SEARCH_DIAG (U3)."""

    try:
        with _connect(db_path) as conn:
            return int(
                conn.execute(
                    "select count(*) from units where unit_type = ?", (unit_type,)
                ).fetchone()[0]
            )
    except sqlite3.DatabaseError:
        return 0


def _select_unit_rows(
    conn: sqlite3.Connection,
    terms: list[str],
    unit_type_filter: str | None,
) -> list[sqlite3.Row]:
    unit_type = unit_type_filter or "trace"
    if terms:
        return list(
            conn.execute(
                """
                select units.* from units
                join units_fts on units_fts.rowid = units.rowid
                where units_fts match ? and units.unit_type = ?
                order by units.trace_id, units.unit_id
                """,
                (_fts_query(terms), unit_type),
            )
        )
    return list(
        conn.execute(
            "select * from units where unit_type = ? order by trace_id, unit_id",
            (unit_type,),
        )
    )


def _metadata_value_matches(actual: Any, expected: str) -> bool:
    if isinstance(actual, bool):
        return str(actual).lower() == expected.lower()
    if isinstance(actual, (str, int, float)):
        return str(actual) == expected
    if isinstance(actual, list):
        return any(_metadata_value_matches(item, expected) for item in actual)
    return False


def _facet_values(unit: TraceUnit, name: str) -> set[str]:
    return {str(facet.value) for facet in unit.facets if facet.name == name}


def _bool_facet(unit: TraceUnit, name: str) -> bool | None:
    for facet in unit.facets:
        if facet.name != name:
            continue
        if isinstance(facet.value, bool):
            return facet.value
        if isinstance(facet.value, str):
            return facet.value.lower() in {"1", "true", "yes"}
    return None


def _json_list(raw: Any) -> list[Any]:
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _parse_since(value: str) -> datetime:
    stripped = value.strip()
    now = datetime.now(timezone.utc)
    duration = re.fullmatch(r"(\d+)([dhm])", stripped.lower())
    if duration:
        amount = int(duration.group(1))
        unit = duration.group(2)
        if unit == "d":
            return now - timedelta(days=amount)
        if unit == "h":
            return now - timedelta(hours=amount)
        return now - timedelta(minutes=amount)
    try:
        parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("--since must be an ISO date/time or duration like 7d") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _unit_timestamp(unit: TraceUnit) -> datetime:
    raw = unit.metadata.get("timestamp_end") or unit.metadata.get("timestamp_start")
    if not raw:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _trace_files(trace_map: TraceMap) -> list[str]:
    files: list[str] = []
    for node in trace_map.nodes:
        files.extend(node.files_read)
        files.extend(node.files_modified)
    return sorted(dict.fromkeys(files))


def _trace_skills(record: TraceRecord) -> list[str]:
    return trace_skill_names(record)


def _trace_tools(record: TraceRecord) -> list[str]:
    tools: list[str] = []
    for step in record.steps:
        tools.extend(call.tool_name for call in step.tool_calls)
    return sorted(dict.fromkeys(tools))


def _test_commands(record: TraceRecord) -> list[str]:
    commands: list[str] = []
    for step in record.steps:
        for call in step.tool_calls:
            command = str(call.input.get("command") or "")
            if command and _TEST_COMMAND_RE.search(command):
                commands.append(command)
    return commands


def _trace_signals(record: TraceRecord, trace_map: TraceMap) -> list[TraceSignal]:
    observations = " ".join(_observation_summaries(record)).lower()
    failed = any(word in observations for word in ("failed", "failures", "error"))
    passed = "passed" in observations or "success" in observations
    edited = any(node.action_type == "file_edit" for node in trace_map.nodes)
    test_seen = any(node.action_type == "test_run" for node in trace_map.nodes)
    signals: list[TraceSignal] = []

    def add(name: str, value: bool, confidence: str = "high") -> None:
        if value:
            signals.append(
                TraceSignal(
                    name=name,
                    value=True,
                    confidence=confidence,  # type: ignore[arg-type]
                    evidence_refs=[
                        node.node_id
                        for node in trace_map.nodes
                        if node.action_type in {"test_run", "file_edit", "tool_result"}
                    ][:8],
                )
            )

    add("test_command_seen", test_seen)
    add("test_failed_seen", failed)
    add("test_passed_seen", passed)
    add("patch_or_edit_seen", edited)
    add("test_passed_after_edit_seen", test_seen and passed and edited)
    add("test_failed_then_passed_seen", failed and passed)
    add("outcome_success", record.outcome.success is True)
    add("outcome_committed", record.outcome.committed is True)
    add(
        "tested_successful_fix_candidate",
        test_seen and failed and passed and edited and record.outcome.success is True,
    )
    if _trace_skills(record):
        signals.append(
            TraceSignal(
                name="skill_invoked",
                value=_trace_skills(record),
                confidence="high",
                evidence_refs=[
                    node.node_id for node in trace_map.nodes if node.action_type == "skill_invocation"
                ],
            )
        )
    return signals


def _trace_facets(
    record: TraceRecord,
    project_slug: str,
    skills: list[str],
    tools: list[str],
    files: list[str],
) -> list[TraceFacet]:
    facets = [
        TraceFacet(name="project_slug", value=project_slug, source="exact_schema"),
        TraceFacet(name="agent.name", value=record.agent.name, source="exact_schema"),
    ]
    provider = _provider_kind(record)
    if provider:
        facets.append(TraceFacet(name="provider.kind", value=provider, source="exact_schema"))
    if record.agent.model:
        facets.append(TraceFacet(name="model", value=record.agent.model, source="exact_schema"))
    facets.append(
        TraceFacet(name="outcome.committed", value=record.outcome.committed, source="exact_schema")
    )
    if record.outcome.success is not None:
        facets.append(
            TraceFacet(name="outcome.success", value=record.outcome.success, source="exact_schema")
        )
    for skill in skills:
        facets.append(TraceFacet(name="skill.name", value=skill, source="exact_schema"))
    for tool in tools:
        facets.append(TraceFacet(name="tool.name", value=tool, source="exact_schema"))
    for link in record.git_links:
        facets.append(TraceFacet(name="git_link_tier", value=link.tier, source="exact_schema"))
    for dependency in _validated_dependencies(record):
        facets.append(TraceFacet(name="dependency.name", value=dependency, source="dependency"))
    for state in _survival_states(record):
        facets.append(TraceFacet(name="survival.state", value=state, source="exact_schema"))
    for file_path in files:
        facets.append(TraceFacet(name="file.path", value=file_path, source="file_path"))
        suffix = Path(file_path).suffix.lstrip(".")
        if suffix:
            facets.append(TraceFacet(name="file.kind", value=suffix, source="file_path"))
    facets.extend(semantic_facets_for_trace(record, files=files, skills=skills))
    facets.extend(_command_facets(record))
    facets.extend(_file_operation_facets(record))
    return facets


def _provider_kind(record: TraceRecord) -> str | None:
    model = record.agent.model or ""
    if "/" in model:
        provider = model.split("/", 1)[0].strip()
        return provider or None
    if record.agent.name:
        return record.agent.name.split("-", 1)[0]
    return None


def _survival_states(record: TraceRecord) -> list[str]:
    """Bundle L / Bug #6: derive survival from real liveness signals.

    The legacy reader only consulted ``record.metadata.survival_state``,
    which is empty on the vast majority of captured traces because the
    Trace-Trails reconciler writes survival projections into the
    ``refs/opentraces/local/events/v1`` event log instead of the trace
    record itself. This helper now derives a best-effort survival vocabulary
    from ``git_links[].content_alive`` / ``commit_reachable`` whenever the
    record metadata is silent. The canonical phase-4 vocabulary is preserved
    (``alive_on_path | alive_transformed | reverted | lost | unknown``); the
    sync-projection-driven phase-5 states (``alive_moved``,
    ``partially_preserved``, ``repaired``) and the trail event projections
    are still consumed when the writer pre-populated them.
    """

    raw = record.metadata.get("survival_state") or record.metadata.get("survival_states")
    if raw is not None:
        if isinstance(raw, list):
            states = sorted({str(item) for item in raw if item})
        else:
            states = [str(raw)]
        if states:
            return states

    states: set[str] = set()
    for link in record.git_links:
        alive = link.content_alive
        reachable = link.commit_reachable
        if alive is True:
            states.add("alive_on_path")
        elif alive is False:
            if reachable is False:
                states.add("reverted")
            else:
                states.add("lost")
        elif reachable is False:
            states.add("reverted")
    if not states and record.git_links:
        states.add("unknown")
    return sorted(states)


def _validated_dependencies(record: TraceRecord) -> list[str]:
    """Bundle V / Bug #11: drop garbage dependency tokens.

    ``record.dependencies`` is populated upstream by parser/enrichment code
    and on real traces routinely contains shell artefacts like ``2``, ``#``,
    ``\\``, or partial command lines. We only honour entries that look like
    a package identifier and that are not a shell keyword. Version
    specifiers (``foo==1.2``, ``foo@^1``) are stripped before validation
    so common ecosystems still flow through.
    """

    out: list[str] = []
    seen: set[str] = set()
    for dep in record.dependencies:
        if not isinstance(dep, str):
            continue
        bare = re.split(r"[<>=~!@\s]", dep, maxsplit=1)[0].strip()
        if not bare or bare in seen:
            continue
        if bare in _SHELL_KEYWORDS:
            continue
        if not _DEPENDENCY_NAME_RE.match(bare):
            continue
        seen.add(bare)
        out.append(bare)
    return out


def _command_facets(record: TraceRecord) -> list[TraceFacet]:
    facets: list[TraceFacet] = []
    for step in record.steps:
        for call in step.tool_calls:
            if call.tool_name.lower() not in {"bash", "shell"}:
                continue
            command = str(call.input.get("command") or "")
            if not command:
                continue
            family = _command_family(command)
            if family:
                facets.append(
                    TraceFacet(
                        name="bash.command_family",
                        value=family,
                        source="bash_command",
                        evidence_ref=f"tu:{record.trace_id}:tool:{call.tool_call_id}",
                    )
                )
            action = _bash_action(command)
            if action:
                facets.append(
                    TraceFacet(
                        name="bash.action",
                        value=action,
                        source="bash_command",
                        evidence_ref=f"tu:{record.trace_id}:tool:{call.tool_call_id}",
                    )
                )
            framework = _test_framework(command)
            if framework:
                facets.append(
                    TraceFacet(
                        name="test.framework",
                        value=framework,
                        source="bash_command",
                        evidence_ref=f"tu:{record.trace_id}:tool:{call.tool_call_id}",
                    )
                )
            for name, channel in _service_mentions(command):
                facets.append(
                    TraceFacet(
                        name="service.name",
                        value=name,
                        source="web_url",
                        confidence="medium",
                        evidence_ref=f"tu:{record.trace_id}:tool:{call.tool_call_id}",
                    )
                )
                facets.append(
                    TraceFacet(
                        name="service.channel",
                        value=channel,
                        source="web_url",
                        confidence="medium",
                        evidence_ref=f"tu:{record.trace_id}:tool:{call.tool_call_id}",
                    )
                )
    return _dedupe_facets(facets)


def _file_operation_facets(record: TraceRecord) -> list[TraceFacet]:
    facets: list[TraceFacet] = []
    for step in record.steps:
        for call in step.tool_calls:
            normalized = call.tool_name.lower().replace("_", "").replace("-", "")
            if normalized in {"edit", "write", "multiedit"}:
                facets.append(
                    TraceFacet(
                        name="file.operation",
                        value="edit",
                        source="tool_input",
                        evidence_ref=f"tu:{record.trace_id}:tool:{call.tool_call_id}",
                    )
                )
            elif normalized in {"read", "view", "glob", "grep"}:
                facets.append(
                    TraceFacet(
                        name="file.operation",
                        value="read",
                        source="tool_input",
                        evidence_ref=f"tu:{record.trace_id}:tool:{call.tool_call_id}",
                    )
                )
    return _dedupe_facets(facets)


def _dedupe_facets(facets: list[TraceFacet]) -> list[TraceFacet]:
    seen: set[tuple[str, str]] = set()
    out: list[TraceFacet] = []
    for facet in facets:
        key = (facet.name, str(facet.value))
        if key in seen:
            continue
        seen.add(key)
        out.append(facet)
    return out


def _command_family(command: str) -> str | None:
    """Bundle V / Bug #11: only return a real command identifier.

    Reject shell control keywords (``for``, ``while``, ``if``…), comments,
    pipes, redirections, and any token that is not shaped like a binary
    name. Without this guard the index emits ``bash.command_family=for``
    or ``=#`` from arbitrary script shapes which then pollute every
    ``--cmd-family`` query with garbage values.
    """

    stripped = command.lstrip()
    if not stripped or stripped.startswith("#"):
        return None
    parts = stripped.split()
    if not parts:
        return None
    first = parts[0]
    if first in _SHELL_KEYWORDS:
        return None
    if not _COMMAND_FAMILY_RE.match(first):
        return None
    if len(parts) >= 2 and first in {"npm", "pnpm", "yarn", "go", "cargo", "mvn", "gradle"}:
        if parts[1] == "test":
            return f"{first} test"
    return first


def _bash_action(command: str) -> str | None:
    if _TEST_COMMAND_RE.search(command):
        return "test"
    family = _command_family(command)
    if family in {"curl", "wget"}:
        return "service_probe"
    return None


def _test_framework(command: str) -> str | None:
    lowered = command.lower()
    for framework in ("pytest", "vitest", "jest", "rspec"):
        if framework in lowered:
            return framework
    if "npm test" in lowered:
        return "npm test"
    if "pnpm test" in lowered:
        return "pnpm test"
    if "yarn test" in lowered:
        return "yarn test"
    if "cargo test" in lowered:
        return "cargo test"
    if "go test" in lowered:
        return "go test"
    return None


def _service_mentions(command: str) -> list[tuple[str, str]]:
    mentions: list[tuple[str, str]] = []
    for match in _URL_RE.findall(command):
        try:
            parsed = urlparse(match)
            host = parsed.hostname
        except ValueError:
            continue
        if host and parsed.scheme:
            mentions.append((host, parsed.scheme))
    return mentions


def _first_user_text(record: TraceRecord) -> str | None:
    for step in sorted(record.steps, key=lambda s: s.step_index):
        if step.role == "user" and step.content:
            return step.content
    return None


def _tool_texts(record: TraceRecord) -> list[str]:
    texts: list[str] = []
    for step in record.steps:
        for call in step.tool_calls:
            command = call.input.get("command")
            file_path = call.input.get("file_path") or call.input.get("file")
            if command:
                texts.append(_index_text(command))
            elif file_path:
                texts.append(_index_text(f"{call.tool_name} {file_path}"))
            else:
                texts.append(call.tool_name)
    return texts


def _observation_summaries(record: TraceRecord) -> list[str]:
    summaries: list[str] = []
    for step in record.steps:
        for observation in step.observations:
            text = observation.output_summary or observation.content or observation.error
            if text:
                summaries.append(_preview(text, limit=240))
    return summaries


def _candidate_kind(unit: TraceUnit) -> str:
    """Return the canonical candidate kind for ``unit``.

    Cluster A — A4 + A5: surfaces patches/git_anchors as queryable
    candidates and guarantees that no candidate ever has a ``null``
    candidate_kind. Priority order:

    1. ``bug_fix`` if the signal-derived label fires (back-compat).
    2. The unit type itself for the M1 closed vocabulary
       (``patch``, ``git_anchor``, ``trace``, ``trace_slice``,
       ``trace_intent_candidate``, ``skill_invocation``,
       ``tool_sequence``, ``test_or_error_signal``, ``trace_map_node``).
    3. ``trace`` as a final fallback so the value is always non-null.
    """
    signal_names = {signal.name for signal in unit.signals}
    if "tested_successful_fix_candidate" in signal_names:
        return "bug_fix"
    if unit.unit_type in _M1_UNIT_TYPES:
        return unit.unit_type
    return "trace"


def _match_explanation(matched_fields: dict[str, list[str]], unit: TraceUnit) -> str:
    parts = [f"{field} matched {', '.join(terms)}" for field, terms in matched_fields.items()]
    if unit.skills:
        parts.append(f"skill.name matched {', '.join(unit.skills)}")
    signal_names = [signal.name for signal in unit.signals if bool(signal.value)]
    if signal_names:
        parts.append(f"signals matched {', '.join(signal_names)}")
    return "; ".join(parts) or "metadata filter matched"


def _packet_facets(facets: list[TraceFacet]) -> list[TraceFacet]:
    priority = {"skill.name": 0, "outcome.success": 1, "outcome.committed": 2}
    return sorted(
        facets,
        key=lambda facet: (priority.get(facet.name, 10), facet.name, str(facet.value)),
    )


def _slice_preview_for_unit(
    unit: TraceUnit,
    max_slice_nodes: int,
    index_path: Path | None,
) -> TraceMap | None:
    trace_map = get_trace_map(unit.trace_id, index_path=index_path)
    if trace_map is None:
        return None
    candidate_node = _candidate_node_for_unit(trace_map, unit)
    if candidate_node is None:
        return None
    return slice_trace_map_for_candidate(trace_map, candidate_node.node_id, max_steps=max_slice_nodes)


def _candidate_node_for_unit(trace_map: TraceMap, unit: TraceUnit) -> TraceMapNode | None:
    for node in trace_map.nodes:
        if node.unit_id == unit.unit_id or node.node_id == unit.unit_id:
            return node
    preferred = ("file_edit", "test_run", "agent_plan", "user_instruction")
    if unit.unit_type in {"trace", "test_or_error_signal"}:
        for action_type in preferred:
            node = next((n for n in trace_map.nodes if n.action_type == action_type), None)
            if node:
                return node
    return trace_map.nodes[0] if trace_map.nodes else None


def _preview(text: str, *, limit: int = 240) -> str:
    compact = " ".join(_index_text(text).split())
    return compact[: limit - 3] + "..." if len(compact) > limit else compact


def _index_text(text: object) -> str:
    return redact_index_text(text)

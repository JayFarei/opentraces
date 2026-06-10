"""SQLite connection helpers, lock primitives, and DDL schema for the Trace Index.

This module is extracted from ``trace_index.py`` as part of a structural split.
All symbols here are re-exported from ``opentraces.core.trace_index`` so external
callers that import from the facade are unaffected.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, TypeVar

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


def _checkpoint_wal_truncate(db_path: Path) -> None:
    """Best-effort ``PRAGMA wal_checkpoint(TRUNCATE)`` on the index DB.

    The legacy Trace Index runs in WAL mode and is mutated by the
    capture-time keep-warm sync; without an explicit checkpoint the
    ``-wal`` sidecar grows without bound (issue #22 — multi-GB WAL files
    observed on real machines). Called once after a sync write transaction
    commits. Swallows every SQLite error: a busy/blocked checkpoint just
    means a later sync will truncate instead.
    """
    try:
        with _connect(db_path) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass


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

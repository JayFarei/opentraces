"""Local bucket-shaped search projection over Trace Units.

This is intentionally object-store friendly: each rebuild writes an immutable
``builds/<build-id>/`` directory and then advances a tiny ``current.json``
pointer. Today it lives only under ``~/.opentraces/bucket``; a later remote
sync can mirror the same shape without changing query or dataset workflows.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Iterator

try:  # POSIX advisory file locking; available on macOS + Linux.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback.
    _fcntl = None

from opentraces_schema import TraceFacet, TraceSignal, TraceUnit

from . import paths
from . import search_diag
from .bucket_store import bucket_manifest, trace_record_snapshot
from .semantic import expand_semantic_query, semantic_profile_from_facets
from .trace_index import (
    QueryPage,
    _trail_freshness_for_query,
    candidate_packet_for_unit,
    default_index_path,
    get_unit,
    latest_units,
    list_units,
    rebuild_index,
)


SEARCH_PROJECTION_VERSION = "v1"
# Bumped v1 -> v2 for the docs_fts delete-by-rowid fix: docs now carries an
# ``fts_rowid`` column (the docs_fts rowid captured at insert) so per-doc FTS
# deletes are an O(1) rowid lookup instead of a full-FTS scan over an
# ``unindexed`` doc_id column. Old-schema (v1) builds lack this mapping and are
# migrated by a one-time full rebuild on the next refresh (see
# ``_build_has_rowid_mapping`` / ``refresh_search_projection``).
SEARCH_DOC_SCHEMA_VERSION = "opentraces.search_doc.v2"
SEARCH_MANIFEST_SCHEMA_VERSION = "opentraces.search_projection.v1"

# Versioned corpus-hash algorithm (#5). ``corpus_hash`` is derived from an
# order-independent XOR aggregate of per-trace contribution hashes plus the
# doc/trace counts, so a full build and a build-then-delta-refresh of the same
# corpus produce an identical hash, and a delta refresh updates the aggregate in
# O(delta) (subtract old touched contribs, add new) instead of re-hashing the
# whole corpus.
CORPUS_HASH_ALGO = "trace-xor-v1"

# projection_meta keys (#4). The serving sqlite's ``projection_meta`` table is
# the source of truth for counts + corpus hash; manifest.json is a best-effort
# export.
_META_DOC_COUNT = "doc_count"
_META_TRACE_COUNT = "trace_count"
_META_CORPUS_HASH = "corpus_hash"
_META_CORPUS_HASH_ALGO = "corpus_hash_algo"
_META_CORPUS_XOR = "corpus_xor"
_META_LAST_REFRESH_AT = "last_refresh_at"
_META_DOCS_JSONL_STALE = "docs_jsonl_stale"
_META_BUILT_FROM_DIGEST = "built_from_digest"
_META_BUILT_FROM_CHEAP_SIGNAL = "built_from_cheap_signal"
_META_BUILD_ID = "build_id"
_META_DOC_SCHEMA_VERSION = "doc_schema_version"

# Default provenance tag for docs sourced from the local bucket (R7). Remote
# pulls feed ``refresh_search_projection`` with ``source="remote:<owner/repo>"``.
DEFAULT_SOURCE = "local"


class RefreshConcurrencyError(RuntimeError):
    """Retained for back-compat. The in-place WAL model serializes writers with
    an exclusive file lock, so this is no longer raised by the refresh path."""


@contextlib.contextmanager
def _search_projection_write_lock(root: Path) -> Iterator[None]:
    """Serialize ALL projection writers (full rebuild publish, incremental
    refresh, GC) with an exclusive advisory file lock (#2).

    The lock is a sibling of the projection root so it survives swapping out
    build dirs. ``fcntl.flock(LOCK_EX)`` blocks until the lock is free, so two
    concurrent in-place refreshes are serialized rather than racing the same
    serving sqlite; a full rebuild's pointer advance can never orphan an
    in-flight in-place delta. Normal queries take NO lock.

    On platforms without ``fcntl`` (none in our targets) this degrades to a
    no-op so behavior is unchanged on a single writer.
    """

    root.mkdir(parents=True, exist_ok=True)
    if _fcntl is None:  # pragma: no cover - POSIX-only targets.
        yield
        return
    lock_path = root / ".search_projection.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        _fcntl.flock(fd, _fcntl.LOCK_EX)
        try:
            yield
        finally:
            _fcntl.flock(fd, _fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _normalize_source(source: str | None) -> str:
    """A query's source scope. ``None``/empty means the default local source."""

    value = (source or "").strip()
    return value or DEFAULT_SOURCE


def _doc_source(doc: dict[str, Any]) -> str:
    """A doc's effective provenance. Back-compat: source-less / pre-U3 docs
    (missing or empty ``source``) are treated as ``local`` (R7, U3)."""

    value = (doc.get("source") or "").strip()
    return value or DEFAULT_SOURCE


@dataclass(frozen=True)
class SearchProjectionSummary:
    root_path: Path
    build_path: Path
    current_path: Path
    manifest_path: Path
    docs_path: Path
    sqlite_path: Path
    index_path: Path
    build_id: str
    doc_count: int
    unit_count: int
    trace_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "projection": "search",
            "version": SEARCH_PROJECTION_VERSION,
            "root_path": str(self.root_path),
            "build_path": str(self.build_path),
            "current_path": str(self.current_path),
            "manifest_path": str(self.manifest_path),
            "docs_path": str(self.docs_path),
            "sqlite_path": str(self.sqlite_path),
            "index_path": str(self.index_path),
            "build_id": self.build_id,
            "doc_count": self.doc_count,
            "unit_count": self.unit_count,
            "trace_count": self.trace_count,
        }


def build_search_projection(
    *,
    index_path: Path | None = None,
    root_path: Path | None = None,
    rebuild_index_first: bool = False,
) -> SearchProjectionSummary:
    """Materialize the local search-document projection from Trace Units."""

    db_path = index_path or default_index_path()
    if rebuild_index_first or not db_path.exists():
        rebuild_index(db_path)

    root = root_path or paths.search_projection_root(SEARCH_PROJECTION_VERSION)
    units = list_units(index_path=db_path)
    docs = [_doc_for_unit(unit, source=DEFAULT_SOURCE) for unit in units]
    doc_lines = [_canonical_json(doc) for doc in docs]
    # Build-path-independent corpus hash: an order-independent XOR aggregate over
    # the per-trace contributions, so a full build and a build-then-delta-refresh
    # of the same corpus produce the identical hash (#5/#6).
    corpus_xor = _corpus_xor_from_docs(docs)
    doc_count = len(docs)
    trace_count = len({doc["trace_id"] for doc in docs})
    corpus_hash = _corpus_hash_from_aggregate(corpus_xor, doc_count, trace_count)
    bucket_snapshot = trace_record_snapshot(include_objects=False)
    built_from_digest = _current_bucket_digest()
    built_from_cheap_signal = _current_bucket_cheap_signal()
    built_at = _utc_now()
    build_id = f"{built_at.strftime('%Y%m%dT%H%M%S%fZ')}-{corpus_hash[:12]}"
    built_at_iso = built_at.isoformat().replace("+00:00", "Z")

    build_path = root / "builds" / build_id
    build_path.mkdir(parents=True, exist_ok=False)
    # Full rebuild is the ONLY writer that materializes docs.jsonl (#6); the
    # incremental refresh never rewrites it.
    docs_path = build_path / "docs.jsonl"
    docs_path.write_text(("\n".join(doc_lines) + "\n") if doc_lines else "")
    sqlite_path = build_path / "search.sqlite"
    _write_search_sqlite(sqlite_path, docs)
    # Seed projection_meta — the source of truth for counts + corpus hash (#4).
    _write_projection_meta(
        sqlite_path,
        {
            _META_DOC_COUNT: doc_count,
            _META_TRACE_COUNT: trace_count,
            _META_CORPUS_XOR: corpus_xor,
            _META_CORPUS_HASH_ALGO: CORPUS_HASH_ALGO,
            _META_CORPUS_HASH: corpus_hash,
            _META_LAST_REFRESH_AT: built_at_iso,
            _META_DOCS_JSONL_STALE: "false",
            _META_BUILT_FROM_DIGEST: built_from_digest,
            _META_BUILT_FROM_CHEAP_SIGNAL: built_from_cheap_signal,
            _META_BUILD_ID: build_id,
            _META_DOC_SCHEMA_VERSION: SEARCH_DOC_SCHEMA_VERSION,
        },
    )

    manifest = {
        "schema_version": SEARCH_MANIFEST_SCHEMA_VERSION,
        "projection": "search",
        "version": SEARCH_PROJECTION_VERSION,
        "build_id": build_id,
        "built_at": built_at.isoformat().replace("+00:00", "Z"),
        "storage": {
            "mode": "local",
            "root": str(paths.bucket_dir()),
        },
        "input": {
            "index_path": str(db_path),
            "trace_unit_count": len(units),
            "bucket_trace_records": bucket_snapshot,
        },
        "corpus_hash": corpus_hash,
        "built_from_digest": built_from_digest,
        "built_from_cheap_signal": built_from_cheap_signal,
        "doc_count": len(docs),
        "unit_count": len(units),
        "trace_count": trace_count,
        "corpus_hash_algo": CORPUS_HASH_ALGO,
        "docs_jsonl_stale": False,
        "files": {
            "docs_jsonl": "docs.jsonl",
            "search_sqlite": "search.sqlite",
        },
        "capabilities": {
            "search_docs": True,
            "lexical_ready": True,
            "projection_sqlite_ready": True,
            "semantic_ready": True,
            "embedding_ready": False,
            "evidence_refs": True,
            "incremental_ready": True,
        },
    }
    manifest_path = build_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    # Full-rebuild publish takes the write lock so its current.json swap can't
    # orphan an in-flight in-place delta, and GC of old build dirs is serialized
    # against concurrent refreshers (#2).
    with _search_projection_write_lock(root):
        current_path = _advance_pointer(root, build_id, manifest["built_at"])
        _gc_builds(root, keep_build_id=build_id, keep_recent=1)

    return SearchProjectionSummary(
        root_path=root,
        build_path=build_path,
        current_path=current_path,
        manifest_path=manifest_path,
        docs_path=docs_path,
        sqlite_path=sqlite_path,
        index_path=db_path,
        build_id=build_id,
        doc_count=len(docs),
        unit_count=len(units),
        trace_count=trace_count,
    )


def refresh_search_projection(
    *,
    changed_trace_ids: list[str] | None = None,
    deleted_trace_ids: list[str] | None = None,
    changed_trail_project_slugs: list[str] | None = None,
    source: str = DEFAULT_SOURCE,
    index_path: Path | None = None,
    root_path: Path | None = None,
    _retries_left: int = 3,  # retained for back-compat; unused under flock model
) -> SearchProjectionSummary:
    """Incrementally refresh the search projection IN PLACE for a bounded delta.

    NO new build dir, NO whole-tree copy, NO compare-and-swap retry. We open the
    CURRENTLY-SERVING ``search.sqlite`` (the one ``current.json`` points at) and
    mutate IT in a single transaction under an exclusive writer lock (#1, #2):

    * Acquire :func:`_search_projection_write_lock` (``flock(LOCK_EX)``) so two
      concurrent in-place refreshes serialize instead of racing — both deltas
      land, none is dropped, and a full-rebuild publish can't swap the serving
      build out from under us.
    * Re-read ``current.json`` AFTER taking the lock so we mutate whatever build
      is serving right now.
    * Apply all deletes + inserts for the delta ``trace_ids`` (and trail slugs),
      the touched ``projection_traces`` rows, and the ``projection_meta`` update
      in ONE ``BEGIN IMMEDIATE`` transaction over a ``journal_mode=WAL`` DB. A
      crash before COMMIT leaves the old projection intact; after COMMIT it is
      complete and queryable. COMMIT *is* the publish — there is no pointer
      advance (#3).
    * docs.jsonl is NEVER rewritten; ``docs_jsonl_stale`` is set true (#6).
    * corpus_hash is recomputed O(delta) by XOR-ing out the touched traces' old
      contributions and XOR-ing in the new ones (#5).

    If there is no serving build yet, fall back to a full
    :func:`build_search_projection` so callers never special-case cold start.
    """

    db_path = index_path or default_index_path()
    root = root_path or paths.search_projection_root(SEARCH_PROJECTION_VERSION)

    changed = list(dict.fromkeys(changed_trace_ids or []))
    deleted = list(dict.fromkeys(deleted_trace_ids or []))
    trail_slugs = list(dict.fromkeys(changed_trail_project_slugs or []))

    # Materialize the delta's docs (the only _doc_for_unit calls — O(delta)). We
    # do this BEFORE taking the lock since it is read-only against the index.
    new_docs: list[dict[str, Any]] = []
    for tid in changed:
        for unit in list_units(index_path=db_path, trace_id=tid):
            new_docs.append(_doc_for_unit(unit, source=source))
    for slug in trail_slugs:
        for unit_type in ("patch", "git_anchor"):
            for unit in list_units(
                index_path=db_path, project_slug=slug, unit_type=unit_type
            ):
                new_docs.append(_doc_for_unit(unit, source=source))

    with _search_projection_write_lock(root):
        # Re-read the serving build UNDER the lock — never against a stale status
        # captured before another writer published.
        status = search_projection_status(root)
        if status.get("state") != "ok":
            # No serving build to mutate; fall through to a full build (still
            # under our lock would deadlock the inner publish lock — release by
            # exiting the with-block first).
            pass
        elif not _build_has_rowid_mapping(Path(status["sqlite_path"])):
            # OLD-SCHEMA build (v1: docs_fts unindexed, no fts_rowid mapping, and
            # possibly corrupt projection_meta counts from a prior buggy build).
            # We migrate by a one-time FULL rebuild rather than an in-place
            # delta. Rationale: an in-place delta on a v1 build would keep paying
            # the O(corpus) full-FTS-scan delete (the very blocker we're fixing),
            # and cheaply backfilling the rowid mapping is impossible — FTS5
            # gives no stable doc_id->rowid join for an unindexed column without
            # scanning the whole FTS per doc. A full rebuild writes the v2 schema
            # + correct counts in a single ~minute pass and is self-healing for
            # the corrupt-meta case. Fall through (exit the lock first so the
            # inner publish lock doesn't deadlock).
            pass
        else:
            sqlite_path = Path(status["sqlite_path"])
            build_path = sqlite_path.parent
            build_id = status.get("build_id")
            manifest = status.get("manifest") or {}
            manifest_path = build_path / "manifest.json"
            docs_path = build_path / "docs.jsonl"

            # HOT PATH: stamp only the CHEAP stat-only bucket signal (~20ms).
            # We deliberately do NOT run the heavy ``_current_bucket_digest``
            # (``bucket_manifest`` full scan, ~9s on the live bucket) here — the
            # cheap signal is the authoritative freshness gate (F2), and
            # ``built_from_digest`` is visibility-only lag. Carry forward the
            # prior build's ``built_from_digest`` (from status / projection_meta)
            # so a delta refresh stays bounded by the delta, not a full rescan.
            built_from_digest = status.get("built_from_digest") or manifest.get(
                "built_from_digest"
            )
            built_from_cheap_signal = _current_bucket_cheap_signal()
            refreshed_at = _utc_now().isoformat().replace("+00:00", "Z")

            # Single in-place transaction: deletes + inserts + projection_traces
            # + projection_meta. COMMIT is the publish (#1, #3).
            corpus_hash, doc_count, trace_count = _apply_inplace_delta(
                sqlite_path,
                changed_trace_ids=changed,
                deleted_trace_ids=deleted,
                changed_trail_project_slugs=trail_slugs,
                new_docs=new_docs,
                source=source,
                refreshed_at=refreshed_at,
                built_from_digest=built_from_digest,
                built_from_cheap_signal=built_from_cheap_signal,
            )

            # Manifest is best-effort export (#4). It tracks projection_meta but
            # is NOT the source of truth; keep it consistent for human/legacy
            # readers. docs.jsonl is left untouched and flagged stale (#6).
            export_manifest = dict(manifest)
            export_manifest.update(
                {
                    "schema_version": SEARCH_MANIFEST_SCHEMA_VERSION,
                    "projection": "search",
                    "version": SEARCH_PROJECTION_VERSION,
                    "build_id": build_id,
                    "built_at": refreshed_at,
                    "corpus_hash": corpus_hash,
                    "corpus_hash_algo": CORPUS_HASH_ALGO,
                    "built_from_digest": built_from_digest,
                    "built_from_cheap_signal": built_from_cheap_signal,
                    "doc_count": doc_count,
                    "unit_count": doc_count,
                    "trace_count": trace_count,
                    "docs_jsonl_stale": True,
                    "refresh": {
                        "base_build_id": build_id,
                        "changed_trace_ids": changed,
                        "deleted_trace_ids": deleted,
                        "changed_trail_project_slugs": trail_slugs,
                        "source": source,
                    },
                }
            )
            manifest_path.write_text(
                json.dumps(export_manifest, indent=2, sort_keys=True) + "\n"
            )

            return SearchProjectionSummary(
                root_path=root,
                build_path=build_path,
                current_path=root / "current.json",
                manifest_path=manifest_path,
                docs_path=docs_path,
                sqlite_path=sqlite_path,
                index_path=db_path,
                build_id=build_id,
                doc_count=doc_count,
                unit_count=doc_count,
                trace_count=trace_count,
            )

    # Cold start: no serving build. build_search_projection takes the lock itself.
    return build_search_projection(index_path=db_path, root_path=root_path)


def search_projection_status(root_path: Path | None = None) -> dict[str, Any]:
    """Return the current local search projection pointer and manifest."""

    root = root_path or paths.search_projection_root(SEARCH_PROJECTION_VERSION)
    current_path = root / "current.json"
    base: dict[str, Any] = {
        "projection": "search",
        "version": SEARCH_PROJECTION_VERSION,
        "root_path": str(root),
        "current_path": str(current_path),
    }
    if not current_path.exists():
        return {**base, "state": "missing"}
    try:
        pointer = json.loads(current_path.read_text())
    except Exception as exc:
        return {**base, "state": "error", "error": str(exc)}

    manifest_rel = pointer.get("manifest_path")
    manifest_path = root / manifest_rel if isinstance(manifest_rel, str) else None
    if manifest_path is None or not manifest_path.exists():
        return {
            **base,
            "state": "dangling",
            "current": pointer,
            "manifest_path": str(manifest_path) if manifest_path else None,
        }

    try:
        manifest = json.loads(manifest_path.read_text())
    except Exception as exc:
        return {
            **base,
            "state": "error",
            "current": pointer,
            "manifest_path": str(manifest_path),
            "error": str(exc),
        }

    sqlite_path = manifest_path.parent / (manifest.get("files") or {}).get(
        "search_sqlite", "search.sqlite"
    )

    # projection_meta in the serving sqlite is the SOURCE OF TRUTH for counts +
    # corpus hash (#4); manifest.json is a best-effort export that an in-place
    # refresh updates after COMMIT, but the sqlite is authoritative even if the
    # manifest write lagged or was skipped. Fall back to the manifest when meta
    # is absent (cold legacy build).
    meta = _read_projection_meta(sqlite_path)
    if meta is not None:
        doc_count = int(meta.get(_META_DOC_COUNT) or manifest.get("doc_count", 0))
        trace_count = int(
            meta.get(_META_TRACE_COUNT) or manifest.get("trace_count", 0)
        )
        corpus_hash = meta.get(_META_CORPUS_HASH) or manifest.get("corpus_hash")
        docs_jsonl_stale = (meta.get(_META_DOCS_JSONL_STALE) or "").lower() == "true"
        last_refresh_at = meta.get(_META_LAST_REFRESH_AT)
        built_from_digest = meta.get(_META_BUILT_FROM_DIGEST) or manifest.get(
            "built_from_digest"
        )
        built_from_cheap_signal = meta.get(
            _META_BUILT_FROM_CHEAP_SIGNAL
        ) or manifest.get("built_from_cheap_signal")
        corpus_hash_algo = meta.get(_META_CORPUS_HASH_ALGO) or manifest.get(
            "corpus_hash_algo"
        )
    else:
        doc_count = manifest.get("doc_count", 0)
        trace_count = manifest.get("trace_count", 0)
        corpus_hash = manifest.get("corpus_hash")
        docs_jsonl_stale = bool(manifest.get("docs_jsonl_stale"))
        last_refresh_at = manifest.get("built_at")
        built_from_digest = manifest.get("built_from_digest")
        built_from_cheap_signal = manifest.get("built_from_cheap_signal")
        corpus_hash_algo = manifest.get("corpus_hash_algo")

    return {
        **base,
        "state": "ok",
        "current": pointer,
        "manifest_path": str(manifest_path),
        "sqlite_path": str(sqlite_path),
        "build_id": manifest.get("build_id"),
        "doc_count": doc_count,
        "unit_count": doc_count,
        "trace_count": trace_count,
        "corpus_hash": corpus_hash,
        "corpus_hash_algo": corpus_hash_algo,
        "docs_jsonl_stale": docs_jsonl_stale,
        "last_refresh_at": last_refresh_at,
        "built_from_digest": built_from_digest,
        "built_from_cheap_signal": built_from_cheap_signal,
        "embedding_ready": bool(
            (manifest.get("capabilities") or {}).get("embedding_ready")
        ),
        "manifest": manifest,
    }


def search_projection_freshness(root_path: Path | None = None) -> dict[str, Any]:
    """Report search-projection freshness cheaply, WITHOUT a heavy repair (U5).

    Compares the serving build's ``built_from_digest`` against the current
    bucket digest. This is a read-only probe: it never builds, rebuilds, or
    refreshes the projection — it just tells doctor (and other callers) whether
    the warm projection is up to date with the bucket. The fix, when stale, is
    ``trace index refresh`` (cheap sync) or, in the worst case,
    ``trace index rebuild``.

    Returns ``state`` mirroring :func:`search_projection_status`
    (``ok`` / ``missing`` / ``dangling`` / ``error``) plus, when ``ok``:
    ``built_from_digest`` (the digest the projection was built from),
    ``current_digest`` (the live bucket digest), and ``fresh`` (their equality).
    ``fresh`` is ``None`` when the current digest can't be computed.
    """

    status = search_projection_status(root_path)
    state = status.get("state")
    base = {
        "state": state,
        "build_id": status.get("build_id"),
    }
    if state != "ok":
        return {
            **base,
            "built_from_digest": None,
            "persisted_bucket_digest": None,
            "current_digest": None,
            "fresh": None,
        }

    manifest = status.get("manifest") or {}
    # Prefer the projection_meta-sourced fields surfaced by status (#4); an
    # in-place refresh updates those even when the manifest export lagged.
    built_from_digest = status.get("built_from_digest") or manifest.get(
        "built_from_digest"
    )
    # F2 (#5): compare the cheap, stat-only bucket signal — NOT the ~46s heavy
    # bucket_manifest full scan — so ``opentraces doctor`` stays a cheap
    # read-only probe (R5). Builds stamp the cheap signal they were built from;
    # legacy builds without it report ``fresh=None`` (unknown) rather than
    # forcing the heavy digest scan here.
    built_from_signal = status.get("built_from_cheap_signal") or manifest.get(
        "built_from_cheap_signal"
    )
    current_signal = _current_bucket_cheap_signal()
    fresh: bool | None
    if built_from_signal is None or current_signal is None:
        fresh = None
    else:
        fresh = built_from_signal == current_signal
    # F3: read the PERSISTED on-disk bucket digest cheaply (single small JSON
    # read, NOT a bucket_manifest recompute) and report the digest the
    # projection was built from vs. the digest the bucket last persisted. This
    # surfaces ``built_from_digest`` lag without any heavy work; the cheap
    # stat-only signal above remains the authoritative fresh/stale gate.
    persisted_bucket_digest = _persisted_bucket_digest()
    built_from_digest_lag: bool | None
    if built_from_digest is None or persisted_bucket_digest is None:
        built_from_digest_lag = None
    else:
        built_from_digest_lag = built_from_digest != persisted_bucket_digest
    return {
        **base,
        "built_from_digest": built_from_digest,
        "built_from_cheap_signal": built_from_signal,
        "current_cheap_signal": current_signal,
        "persisted_bucket_digest": persisted_bucket_digest,
        "built_from_digest_lag": built_from_digest_lag,
        "fresh": fresh,
    }


def query_search_projection_page(
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
    source: str | None = None,
    limit: int = 20,
    page_token: str | None = None,
    include_slice: str | None = None,
    max_slice_nodes: int = 40,
    index_path: Path | None = None,
    root_path: Path | None = None,
    build_if_missing: bool = True,
) -> QueryPage:
    """Query the immutable local search projection and return CandidatePackets."""

    db_path = index_path or default_index_path()
    search_diag.reset()
    status = search_projection_status(root_path)
    if status.get("state") != "ok":
        if not build_if_missing:
            return QueryPage(candidates=[], next_page_token=None, total=0)
        build_search_projection(index_path=db_path, root_path=root_path)
        status = search_projection_status(root_path)
    sqlite_path = Path(status["sqlite_path"])
    if not sqlite_path.exists():
        if not build_if_missing:
            return QueryPage(candidates=[], next_page_token=None, total=0)
        build_search_projection(index_path=db_path, root_path=root_path)
        status = search_projection_status(root_path)
        sqlite_path = Path(status["sqlite_path"])

    parsed_facets = _parse_key_value_filters(facet_filters)
    parsed_metadata = _parse_key_value_filters(metadata_filters)
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
    since_dt = _parse_since(since) if since else None
    semantic_expansion = expand_semantic_query(semantic) if semantic else None
    semantic_ids = set((semantic_expansion or {}).get("concept_ids") or [])
    terms = _terms(lex or semantic or "")
    docs = (
        _select_docs_by_semantic_ids(sqlite_path, semantic_ids)
        if semantic_ids
        else _select_docs(sqlite_path, terms)
    )
    if search_diag.enabled():
        search_diag.record(
            path="projection_concept" if semantic_ids else "projection_fts",
            docs_selected=len(docs),
            corpus_docs=_count_projection_docs(sqlite_path),
        )
    scored: list[tuple[float, dict[str, float], dict[str, list[str]], TraceUnit]] = []
    for doc in docs:
        if since_dt and _doc_timestamp(doc) < since_dt:
            continue
        if not _doc_matches(
            doc,
            skill=skill,
            tool=tool,
            files=files,
            file_kind=file_kind,
            file_op=file_op,
            signal=signal,
            facet_filters=parsed_facets,
            metadata_filters=parsed_metadata,
            named_filters=named_filters,
            project=project,
            source=source,
            success=success,
            success_unknown=success_unknown,
            committed=committed,
            committed_unknown=committed_unknown,
            candidate_kind=candidate_kind,
        ):
            continue
        lexical_score, matched_fields = _lexical_score_doc(doc, terms)
        semantic_score, semantic_matches = _semantic_score_doc(
            doc,
            semantic_expansion,
        )
        matched_fields.update(semantic_matches)
        metadata_score = _metadata_score_doc(
            doc,
            skill=skill,
            tool=tool,
            files=files,
            file_kind=file_kind,
            file_op=file_op,
            signal=signal,
            facet_filters=parsed_facets,
            metadata_filters=parsed_metadata,
            named_filters=named_filters,
            project=project,
            success=success,
            success_unknown=success_unknown,
            committed=committed,
            committed_unknown=committed_unknown,
            candidate_kind=candidate_kind,
            since=since,
            semantic=semantic,
        )
        if terms and lexical_score <= 0 and semantic_score <= 0:
            continue
        if not terms and metadata_score <= 0 and semantic_score <= 0:
            continue
        search_diag.incr("docs_scored")
        unit = get_unit(str(doc["unit_id"]), index_path=db_path)
        if unit is None:
            continue
        scored.append(
            (
                lexical_score + metadata_score + semantic_score,
                {
                    "projection_lexical": lexical_score,
                    "projection_semantic": semantic_score,
                    "projection_metadata": metadata_score,
                },
                matched_fields,
                unit,
            )
        )

    if latest_generation:
        latest_ids = {unit.unit_id for unit in latest_units([item[3] for item in scored])}
        scored = [item for item in scored if item[3].unit_id in latest_ids]
    scored.sort(key=lambda item: (-item[0], item[3].trace_id, item[3].unit_id))
    offset = _page_offset(page_token)
    page_size = max(1, limit)
    selected = scored[offset : offset + page_size]
    next_offset = offset + page_size
    next_page_token = f"offset:{next_offset}" if next_offset < len(scored) else None
    selected_units = [unit for _score, _parts, _matched, unit in selected]
    return QueryPage(
        candidates=[
            candidate_packet_for_unit(
                unit,
                score,
                score_parts,
                matched_fields,
                include_slice=include_slice,
                max_slice_nodes=max_slice_nodes,
                index_path=db_path,
            )
            for score, score_parts, matched_fields, unit in selected
        ],
        next_page_token=next_page_token,
        total=len(scored),
        warnings=_trail_freshness_for_query(
            db_path,
            selected_units,
            project=project,
            survival=survival,
            candidate_kind=candidate_kind,
            facet_filters=facet_filters,
        ),
    )


def _doc_for_unit(unit: TraceUnit, *, source: str = DEFAULT_SOURCE) -> dict[str, Any]:
    fields = {
        "title": unit.title_text,
        "intent": unit.intent_text,
        "action": unit.action_text,
        "evidence": unit.evidence_text,
        "artifact": unit.artifact_text,
    }
    facets = [_facet_payload(facet) for facet in unit.facets]
    signals = [_signal_payload(signal) for signal in unit.signals]
    material = {
        "schema_version": SEARCH_DOC_SCHEMA_VERSION,
        "doc_id": f"sd:{unit.unit_id}",
        "doc_type": unit.unit_type,
        "unit_id": unit.unit_id,
        "trace_id": unit.trace_id,
        "project_slug": unit.project_slug,
        "title": unit.title_text,
        "text": "\n\n".join(value for value in fields.values() if value),
        "fields": fields,
        "files": sorted(set(unit.files)),
        "skills": sorted(set(unit.skills)),
        "facets": facets,
        "signals": signals,
        "metadata": unit.metadata,
        "trail_refs": unit.trail_refs,
        "evidence_refs": _evidence_refs(unit),
        "semantic": semantic_profile_from_facets(unit.facets),
        "source": source,
    }
    material["search_text"] = _projection_search_text(material)
    return {**material, "content_hash": _sha256_json(material)}


def _write_search_sqlite(sqlite_path: Path, docs: list[dict[str, Any]]) -> None:
    if sqlite_path.exists():
        sqlite_path.unlink()
    with sqlite3.connect(sqlite_path) as conn:
        # WAL so an in-place incremental refresh is crash-safe: a crash before
        # COMMIT leaves the prior projection intact (#3). WAL persists in the DB
        # header, so every later open (read or write) inherits it.
        conn.execute("pragma journal_mode=WAL")
        _create_search_schema(conn)
        for doc in docs:
            _insert_doc(conn, doc)
        _rewrite_projection_traces(conn, docs)
        conn.commit()


def _create_search_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table docs (
            doc_id text primary key,
            unit_id text not null,
            trace_id text not null,
            project_slug text not null,
            doc_type text not null,
            source text not null default 'local',
            title text not null,
            payload_json text not null,
            fts_rowid integer
        );
        create index if not exists idx_docs_trace on docs(trace_id);
        create index if not exists idx_docs_project_type on docs(project_slug, doc_type);
        create virtual table docs_fts using fts5(
            doc_id unindexed,
            title,
            text,
            search_text
        );
        create table projection_traces (
            trace_id text primary key,
            doc_count integer not null,
            trace_hash text not null,
            trace_contrib text not null default '',
            source text not null default 'local'
        );
        create table projection_meta (
            key text primary key,
            value text
        );
        """
    )


def _insert_doc(conn: sqlite3.Connection, doc: dict[str, Any]) -> None:
    payload = _canonical_json(doc)
    # Insert the FTS row FIRST so we can capture its rowid (cur.lastrowid) and
    # persist it on the docs row. The mapping makes per-doc FTS deletes an O(1)
    # ``delete from docs_fts where rowid = ?`` instead of a full-FTS scan over the
    # ``unindexed`` doc_id column (FTS5 cannot index an unindexed column, so a
    # ``where doc_id = ?`` delete is O(corpus)).
    cur = conn.execute(
        """
        insert into docs_fts(doc_id, title, text, search_text)
        values (?, ?, ?, ?)
        """,
        (
            doc["doc_id"],
            doc["title"],
            doc["text"],
            doc["search_text"],
        ),
    )
    fts_rowid = cur.lastrowid
    conn.execute(
        """
        insert into docs(
            doc_id, unit_id, trace_id, project_slug, doc_type, source, title,
            payload_json, fts_rowid
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc["doc_id"],
            doc["unit_id"],
            doc["trace_id"],
            doc["project_slug"],
            doc["doc_type"],
            doc.get("source", DEFAULT_SOURCE),
            doc["title"],
            payload,
            fts_rowid,
        ),
    )


def _delete_doc(conn: sqlite3.Connection, doc_id: str) -> None:
    # Delete the FTS row by its rowid (O(1)) using the mapping stored on the docs
    # row at insert time. The v2 invariant is that a docs row and its docs_fts
    # row are always inserted and deleted together, so if there is no docs row
    # there is no FTS row to delete — we MUST NOT fall back to
    # ``delete from docs_fts where doc_id = ?`` (an O(corpus) full-FTS scan over
    # the unindexed doc_id column, the very blocker this fix removes; on the real
    # 339k-doc bucket that is ~0.5s per call, so the idempotent re-key delete in
    # the reinsert loop would re-introduce a multi-minute stall per trace).
    row = conn.execute(
        "select fts_rowid from docs where doc_id = ?", (doc_id,)
    ).fetchone()
    if row is None:
        # No docs row -> no FTS row (v2 co-management invariant). Nothing to do.
        return
    # Support both sqlite3.Row and plain tuple rows.
    try:
        fts_rowid = row["fts_rowid"]
    except (IndexError, KeyError, TypeError):
        fts_rowid = row[0]
    conn.execute("delete from docs where doc_id = ?", (doc_id,))
    if fts_rowid is not None:
        conn.execute("delete from docs_fts where rowid = ?", (fts_rowid,))
    else:
        # Defensive: a docs row exists but its mapping is null. This cannot
        # happen on a v2 build (the refresh gate routes such builds to a full
        # rebuild), but if it ever does, prefer correctness over speed here.
        conn.execute("delete from docs_fts where doc_id = ?", (doc_id,))


def _rewrite_projection_traces(
    conn: sqlite3.Connection, docs: list[dict[str, Any]]
) -> None:
    conn.execute("delete from projection_traces")
    for trace_id, rows in _group_docs_by_trace(docs).items():
        trace_hash = _trace_hash(rows)
        conn.execute(
            """
            insert into projection_traces(
                trace_id, doc_count, trace_hash, trace_contrib, source
            ) values (?, ?, ?, ?, ?)
            """,
            (
                trace_id,
                len(rows),
                trace_hash,
                _trace_contrib(trace_id, trace_hash),
                rows[0].get("source", DEFAULT_SOURCE),
            ),
        )


def _trace_contrib(trace_id: str, trace_hash: str) -> str:
    """Per-trace XOR contribution (#5): sha256(trace_id + '\\0' + trace_hash).

    The order-independent XOR aggregate of every trace's contrib is the corpus
    fingerprint; a delta refresh XORs out the old touched contribs and XORs in
    the new, so corpus_hash is maintained in O(delta).
    """

    return _sha256_text(f"{trace_id}\0{trace_hash}")


def _group_docs_by_trace(
    docs: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        grouped.setdefault(str(doc["trace_id"]), []).append(doc)
    return grouped


def _trace_hash(docs: list[dict[str, Any]]) -> str:
    """Deterministic per-trace hash over its docs' content hashes."""

    parts = sorted(str(doc.get("content_hash", "")) for doc in docs)
    return _sha256_text("\n".join(parts))


def _apply_inplace_delta(
    sqlite_path: Path,
    *,
    changed_trace_ids: list[str],
    deleted_trace_ids: list[str],
    changed_trail_project_slugs: list[str],
    new_docs: list[dict[str, Any]],
    source: str,
    refreshed_at: str,
    built_from_digest: str | None,
    built_from_cheap_signal: str | None,
) -> tuple[str, int, int]:
    """Mutate the CURRENTLY-SERVING search.sqlite in place in ONE transaction.

    Opens the live serving DB (NOT a copy), opens it WAL + ``BEGIN IMMEDIATE``
    so SQLite enforces a single writer (#2, #3), applies all deletes + inserts
    for the delta trace_ids / trail slugs, recomputes the touched
    ``projection_traces`` rows, maintains the O(delta) XOR corpus aggregate and
    counts, and writes ``projection_meta``. COMMIT is the publish: a crash before
    it leaves the old projection intact (#3).

    Returns ``(corpus_hash, doc_count, trace_count)`` derived from the post-COMMIT
    projection_meta.
    """

    conn = sqlite3.connect(sqlite_path)
    # Autocommit mode (isolation_level=None) so WE control transaction
    # boundaries explicitly — otherwise sqlite3 auto-opens a deferred
    # transaction on the first statement and ``BEGIN IMMEDIATE`` errors with
    # "cannot start a transaction within a transaction".
    conn.isolation_level = None
    try:
        conn.row_factory = sqlite3.Row
        # WAL is sticky in the DB header, but a build that predates this code
        # could be in another mode; force it (a no-op if already WAL). Must run
        # OUTSIDE any open transaction, which autocommit guarantees.
        conn.execute("pragma journal_mode=WAL")
        # Ensure the schema has the incremental shape (older builds may not).
        # These DDL/backfill statements run in autocommit (each its own txn).
        _ensure_incremental_schema(conn)

        # Single writer: BEGIN IMMEDIATE takes the RESERVED lock now so a second
        # concurrent writer can't interleave (belt + braces with the flock).
        conn.execute("BEGIN IMMEDIATE")

        # Read the prior aggregate + counts BEFORE mutating.
        prior_xor = _meta_get(conn, _META_CORPUS_XOR) or _ZERO_XOR
        prior_doc_count = int(_meta_get(conn, _META_DOC_COUNT) or "0")
        prior_trace_count = int(_meta_get(conn, _META_TRACE_COUNT) or "0")

        purge_traces = set(changed_trace_ids) | set(deleted_trace_ids)
        touched = purge_traces | {str(doc["trace_id"]) for doc in new_docs}

        # Old per-trace state for every touched trace (contrib + doc_count) so we
        # can XOR them out and adjust counts in O(delta).
        old_state: dict[str, tuple[str, int]] = {}
        for tid in touched:
            row = conn.execute(
                "select doc_count, trace_hash, trace_contrib "
                "from projection_traces where trace_id = ?",
                (tid,),
            ).fetchone()
            if row is not None:
                contrib = row["trace_contrib"] or _trace_contrib(
                    tid, row["trace_hash"]
                )
                old_state[tid] = (contrib, int(row["doc_count"]))

        # Delete docs for every touched trace + trail slug.
        for tid in purge_traces:
            for row in conn.execute(
                "select doc_id from docs where trace_id = ?", (tid,)
            ).fetchall():
                _delete_doc(conn, row["doc_id"])
        for slug in changed_trail_project_slugs:
            for row in conn.execute(
                "select doc_id from docs where project_slug = ? "
                "and doc_type in ('patch', 'git_anchor')",
                (slug,),
            ).fetchall():
                _delete_doc(conn, row["doc_id"])

        # Reinsert the freshly materialized docs (idempotent re-key).
        for doc in new_docs:
            _delete_doc(conn, doc["doc_id"])
            _insert_doc(conn, doc)

        # Recompute projection_traces + the running aggregate for touched traces.
        running_xor = prior_xor
        doc_count = prior_doc_count
        trace_count = prior_trace_count
        for tid in touched:
            old = old_state.get(tid)
            if old is not None:
                running_xor = _xor_hex(running_xor, old[0])
                doc_count -= old[1]
                trace_count -= 1
            conn.execute(
                "delete from projection_traces where trace_id = ?", (tid,)
            )
            rows = [
                json.loads(r["payload_json"])
                for r in conn.execute(
                    "select payload_json from docs where trace_id = ?", (tid,)
                ).fetchall()
            ]
            if rows:
                trace_hash = _trace_hash(rows)
                contrib = _trace_contrib(tid, trace_hash)
                conn.execute(
                    """
                    insert into projection_traces(
                        trace_id, doc_count, trace_hash, trace_contrib, source
                    ) values (?, ?, ?, ?, ?)
                    """,
                    (
                        tid,
                        len(rows),
                        trace_hash,
                        contrib,
                        rows[0].get("source", DEFAULT_SOURCE),
                    ),
                )
                running_xor = _xor_hex(running_xor, contrib)
                doc_count += len(rows)
                trace_count += 1

        corpus_hash = _corpus_hash_from_aggregate(
            running_xor, doc_count, trace_count
        )
        _meta_set_many(
            conn,
            {
                _META_DOC_COUNT: doc_count,
                _META_TRACE_COUNT: trace_count,
                _META_CORPUS_XOR: running_xor,
                _META_CORPUS_HASH_ALGO: CORPUS_HASH_ALGO,
                _META_CORPUS_HASH: corpus_hash,
                _META_LAST_REFRESH_AT: refreshed_at,
                _META_DOCS_JSONL_STALE: "true",
                _META_BUILT_FROM_DIGEST: built_from_digest,
                _META_BUILT_FROM_CHEAP_SIGNAL: built_from_cheap_signal,
            },
        )
        conn.execute("COMMIT")  # <-- the publish (#3)
    except BaseException:
        # In autocommit mode the only open txn is our explicit BEGIN IMMEDIATE;
        # roll it back so a mid-transaction crash leaves the prior projection
        # intact (#3). Tolerate "no transaction" if we failed before BEGIN.
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise
    finally:
        conn.close()
    return corpus_hash, doc_count, trace_count


def _build_has_rowid_mapping(sqlite_path: Path) -> bool:
    """Return True iff the serving build carries the v2 docs_fts delete-by-rowid
    mapping (``docs.fts_rowid`` exists and is populated for every doc).

    A v1 build (or a partially-migrated one) returns False, which routes the
    refresh to a one-time full rebuild instead of an in-place delta. We check the
    actual column + population state rather than trusting the recorded
    ``doc_schema_version`` so a hand-corrupted / pre-meta build is also caught.
    """

    try:
        with sqlite3.connect(sqlite_path) as conn:
            cols = {
                row[1]
                for row in conn.execute("pragma table_info(docs)").fetchall()
            }
            if "fts_rowid" not in cols:
                return False
            # Any doc without a populated mapping means delete-by-rowid would
            # silently fall back to the O(corpus) doc_id delete — treat the whole
            # build as needing a rebuild.
            missing = conn.execute(
                "select 1 from docs where fts_rowid is null limit 1"
            ).fetchone()
            return missing is None
    except sqlite3.Error:
        # Unreadable / malformed serving sqlite: let the full-build fallback
        # rematerialize from scratch.
        return False


def _ensure_incremental_schema(conn: sqlite3.Connection) -> None:
    """Add the source / trace_contrib columns + projection_traces /
    projection_meta tables to a legacy build that predates them."""

    cols = {
        row[1]
        for row in conn.execute("pragma table_info(docs)").fetchall()
    }
    if "source" not in cols:
        conn.execute(
            "alter table docs add column source text not null default 'local'"
        )
    if "fts_rowid" not in cols:
        # Defensive: the refresh gate (``_build_has_rowid_mapping``) already
        # routes a build lacking this mapping to a full rebuild, so an in-place
        # delta only runs on a v2 build that has it. Add it anyway so the column
        # ``_insert_doc`` writes always exists.
        conn.execute("alter table docs add column fts_rowid integer")
    conn.execute(
        """
        create table if not exists projection_traces (
            trace_id text primary key,
            doc_count integer not null,
            trace_hash text not null,
            trace_contrib text not null default '',
            source text not null default 'local'
        )
        """
    )
    pt_cols = {
        row[1]
        for row in conn.execute("pragma table_info(projection_traces)").fetchall()
    }
    if "trace_contrib" not in pt_cols:
        conn.execute(
            "alter table projection_traces add column "
            "trace_contrib text not null default ''"
        )
    conn.execute(
        """
        create table if not exists projection_meta (
            key text primary key,
            value text
        )
        """
    )
    conn.execute(
        "create index if not exists idx_docs_trace on docs(trace_id)"
    )
    conn.execute(
        "create index if not exists idx_docs_project_type "
        "on docs(project_slug, doc_type)"
    )
    # Backfill projection_meta from projection_traces for a legacy build that
    # has no meta rows yet (e.g. first refresh after upgrade), and backfill any
    # empty trace_contrib so the XOR aggregate is correct from here on.
    if _meta_get(conn, _META_CORPUS_XOR) is None:
        rows = conn.execute(
            "select trace_id, doc_count, trace_hash, trace_contrib "
            "from projection_traces"
        ).fetchall()
        running = _ZERO_XOR
        doc_count = 0
        for r in rows:
            contrib = r["trace_contrib"] or _trace_contrib(
                r["trace_id"], r["trace_hash"]
            )
            if not r["trace_contrib"]:
                conn.execute(
                    "update projection_traces set trace_contrib = ? "
                    "where trace_id = ?",
                    (contrib, r["trace_id"]),
                )
            running = _xor_hex(running, contrib)
            doc_count += int(r["doc_count"])
        trace_count = len(rows)
        _meta_set_many(
            conn,
            {
                _META_DOC_COUNT: doc_count,
                _META_TRACE_COUNT: trace_count,
                _META_CORPUS_XOR: running,
                _META_CORPUS_HASH_ALGO: CORPUS_HASH_ALGO,
                _META_CORPUS_HASH: _corpus_hash_from_aggregate(
                    running, doc_count, trace_count
                ),
                _META_DOCS_JSONL_STALE: "false",
            },
        )


def _all_docs(sqlite_path: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(sqlite_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select payload_json from docs order by trace_id, unit_id"
        ).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def export_docs_jsonl(
    root_path: Path | None = None, *, force: bool = False
) -> Path | None:
    """Deterministically regenerate the lazy docs.jsonl export from the serving
    sqlite (#6).

    Incremental refresh never rewrites docs.jsonl (it flags
    ``docs_jsonl_stale=true``); this regenerates it on demand from the canonical
    docs table, ordered ``trace_id, doc_id`` for byte-determinism, and clears the
    stale flag in projection_meta. No-op (returns the path) when not stale unless
    ``force``. Takes the write lock so it can't race a refresher's COMMIT.
    Returns the docs.jsonl path, or ``None`` if there's no serving build.
    """

    root = root_path or paths.search_projection_root(SEARCH_PROJECTION_VERSION)
    with _search_projection_write_lock(root):
        status = search_projection_status(root)
        if status.get("state") != "ok":
            return None
        sqlite_path = Path(status["sqlite_path"])
        docs_path = sqlite_path.parent / "docs.jsonl"
        if not force and not status.get("docs_jsonl_stale"):
            return docs_path
        with sqlite3.connect(sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "select payload_json from docs order by trace_id, doc_id"
            ).fetchall()
            doc_lines = [
                _canonical_json(json.loads(row["payload_json"])) for row in rows
            ]
            docs_path.write_text(
                ("\n".join(doc_lines) + "\n") if doc_lines else ""
            )
            _meta_set_many(conn, {_META_DOCS_JSONL_STALE: "false"})
            conn.commit()
    return docs_path


# --- XOR corpus aggregate + projection_meta (#4, #5) ----------------------

_ZERO_XOR = "0" * 64  # sha256 hex width


def _xor_hex(a: str, b: str) -> str:
    """XOR two 64-char sha256 hex strings, returning a 64-char hex string."""

    return format(int(a or _ZERO_XOR, 16) ^ int(b or _ZERO_XOR, 16), "064x")


def _corpus_xor_from_docs(docs: list[dict[str, Any]]) -> str:
    """Order-independent XOR aggregate of per-trace contributions (#5).

    Identical regardless of doc/trace ordering or build path, so a full build
    and a build-then-delta-refresh of the same corpus produce the same value.
    """

    running = _ZERO_XOR
    for trace_id, rows in _group_docs_by_trace(docs).items():
        running = _xor_hex(running, _trace_contrib(trace_id, _trace_hash(rows)))
    return running


def _corpus_hash_from_aggregate(
    corpus_xor: str, doc_count: int, trace_count: int
) -> str:
    """Derive the versioned ``corpus_hash`` from the XOR aggregate + counts (#5).

    Versioned by :data:`CORPUS_HASH_ALGO` so a future change to the aggregation
    scheme is distinguishable. Build-path-independent: depends only on the
    order-independent XOR and the (also order-independent) counts.
    """

    return _sha256_text(
        f"{CORPUS_HASH_ALGO}\0{corpus_xor}\0{doc_count}\0{trace_count}"
    )


def _projection_stats_from_sqlite(sqlite_path: Path) -> tuple[str, int, int]:
    """Recompute (corpus_hash, doc_count, trace_count) from projection_traces.

    Source of truth is ``projection_meta`` (#4); this is a fallback / verifier
    that rederives the same value straight from the canonical
    ``projection_traces`` rows, build-path-independently.
    """

    with sqlite3.connect(sqlite_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select trace_id, doc_count, trace_hash, trace_contrib "
            "from projection_traces"
        ).fetchall()
        live_doc_count = conn.execute("select count(*) from docs").fetchone()[0]
    running = _ZERO_XOR
    doc_count = 0
    for row in rows:
        contrib = row["trace_contrib"] or _trace_contrib(
            row["trace_id"], row["trace_hash"]
        )
        running = _xor_hex(running, contrib)
        doc_count += int(row["doc_count"])
    trace_count = len(rows)
    # docs table is the authoritative doc count; projection_traces should agree.
    corpus_hash = _corpus_hash_from_aggregate(running, doc_count, trace_count)
    return corpus_hash, int(live_doc_count), trace_count


def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "select value from projection_meta where key = ?", (key,)
    ).fetchone()
    if row is None:
        return None
    value = row[0] if not isinstance(row, sqlite3.Row) else row["value"]
    return value


def _meta_set_many(conn: sqlite3.Connection, items: dict[str, Any]) -> None:
    for key, value in items.items():
        stored = None if value is None else str(value)
        conn.execute(
            "insert into projection_meta(key, value) values (?, ?) "
            "on conflict(key) do update set value = excluded.value",
            (key, stored),
        )


def _write_projection_meta(sqlite_path: Path, items: dict[str, Any]) -> None:
    """Seed projection_meta on a freshly built sqlite (own connection)."""

    with sqlite3.connect(sqlite_path) as conn:
        conn.execute(
            "create table if not exists projection_meta (key text primary key, value text)"
        )
        _meta_set_many(conn, items)
        conn.commit()


def _read_projection_meta(sqlite_path: Path) -> dict[str, str] | None:
    """Read the whole projection_meta table; ``None`` if absent/unreadable."""

    try:
        with sqlite3.connect(sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "select key, value from projection_meta"
            ).fetchall()
    except sqlite3.Error:
        return None
    if not rows:
        return None
    return {row["key"]: row["value"] for row in rows}


def _safe_rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _gc_builds(root: Path, *, keep_build_id: str, keep_recent: int = 1) -> None:
    """Prune stale build dirs, keeping ``keep_build_id`` (current) plus the
    ``keep_recent`` most-recent prior builds (so an in-flight reader on the
    just-superseded build is not pulled out from under it). Best-effort: any
    failure is swallowed so a GC hiccup never breaks a successful refresh."""

    try:
        builds_dir = root / "builds"
        if not builds_dir.exists():
            return
        entries = [p for p in builds_dir.iterdir() if p.is_dir()]
        # Newest first by name (build ids are timestamp-prefixed, sortable).
        entries.sort(key=lambda p: p.name, reverse=True)
        keep: set[Path] = set()
        for entry in entries:
            if entry.name == keep_build_id:
                keep.add(entry)
        kept_recent = 0
        for entry in entries:
            if entry in keep:
                continue
            if kept_recent < keep_recent:
                keep.add(entry)
                kept_recent += 1
        for entry in entries:
            if entry not in keep:
                _safe_rmtree(entry)
    except Exception:  # noqa: BLE001 — GC must never break a good refresh.
        return


def _advance_pointer(root: Path, build_id: str, updated_at: str) -> Path:
    """Atomically point current.json at ``build_id``.

    Writes to a temp file then os.replace so an interrupted write never leaves
    a half-written pointer (R8). Returns the current.json path.
    """

    import os
    import tempfile

    pointer = {
        "projection": "search",
        "version": SEARCH_PROJECTION_VERSION,
        "build_id": build_id,
        "manifest_path": f"builds/{build_id}/manifest.json",
        "docs_path": f"builds/{build_id}/docs.jsonl",
        "updated_at": updated_at,
    }
    root.mkdir(parents=True, exist_ok=True)
    current_path = root / "current.json"
    fd, tmp_name = tempfile.mkstemp(dir=str(root), prefix=".current-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(pointer, indent=2, sort_keys=True) + "\n")
        os.replace(tmp_name, current_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return current_path


def _current_bucket_digest() -> str | None:
    """Best-effort bucket manifest digest used as ``built_from_digest`` (R7)."""

    try:
        manifest = bucket_manifest(write=False, include_objects=False)
    except Exception:
        return None
    digest = manifest.get("bucket_digest") or manifest.get("digest")
    return str(digest) if digest is not None else None


def _persisted_bucket_digest() -> str | None:
    """Read the PERSISTED on-disk bucket digest cheaply (F3, no recompute).

    Reads ``bucket/manifest.json`` straight off disk and returns its stored
    ``bucket_digest`` / ``digest`` field. This is a single small JSON read — it
    does NOT run :func:`bucket_manifest`, so it never triggers the ~46s
    full sync+parse scan. Used by doctor's freshness probe to report the digest
    the bucket last persisted (the projection's ``built_from_digest`` lag is
    measured against this).

    NOTE (F1): capture does not currently keep the on-disk manifest digest
    authoritative (ingest writes traces but never rewrites the manifest), so
    this persisted digest can lag a freshly-recomputed digest. It is reported
    for visibility / lag, not used as the authoritative freshness gate — the
    cheap stat-only signal (``_current_bucket_cheap_signal``) remains the
    freshness source of truth. Returns ``None`` if the manifest is absent or
    unreadable.
    """

    try:
        from .bucket_layout import bucket_manifest_path

        path = bucket_manifest_path()
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError, Exception):  # noqa: BLE001
        return None
    if not isinstance(raw, dict):
        return None
    digest = raw.get("bucket_digest") or raw.get("digest")
    return str(digest) if digest is not None else None


def _current_bucket_cheap_signal() -> str | None:
    """Cheap, stat-only bucket fingerprint for freshness (F2 #5).

    Reuses the F1 ``_cheap_bucket_signal`` (os.scandir+stat over the trace-source
    roots, ~17ms on the live bucket) instead of the ~46s ``bucket_manifest`` full
    scan. Returns ``None`` on any error so callers treat freshness as unknown
    rather than crashing doctor (R5: doctor must never crash, stays cheap).
    """

    try:
        from .trace_index import _cheap_bucket_signal

        signal = _cheap_bucket_signal()
    except Exception:  # noqa: BLE001 — freshness probe must stay cheap + safe.
        return None
    return signal or None


def _short_token() -> str:
    import uuid

    return uuid.uuid4().hex[:12]


def _count_projection_docs(sqlite_path: Path) -> int:
    """Total docs in the projection - only called under OT_SEARCH_DIAG (U3)."""

    try:
        with sqlite3.connect(sqlite_path) as conn:
            return int(conn.execute("select count(*) from docs").fetchone()[0])
    except sqlite3.DatabaseError:
        return 0


def _select_docs(sqlite_path: Path, terms: list[str]) -> list[dict[str, Any]]:
    with sqlite3.connect(sqlite_path) as conn:
        conn.row_factory = sqlite3.Row
        if terms:
            rows = conn.execute(
                """
                select docs.payload_json from docs
                join docs_fts on docs_fts.doc_id = docs.doc_id
                where docs_fts match ?
                order by docs.trace_id, docs.unit_id
                """,
                (_fts_query(terms),),
            ).fetchall()
        else:
            rows = conn.execute(
                "select payload_json from docs order by trace_id, unit_id"
            ).fetchall()
    search_diag.record(rows_scanned=len(rows))
    return [json.loads(row["payload_json"]) for row in rows]


def _select_docs_by_semantic_ids(
    sqlite_path: Path,
    semantic_ids: set[str],
) -> list[dict[str, Any]]:
    if not semantic_ids:
        return []
    with sqlite3.connect(sqlite_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select payload_json from docs order by trace_id, unit_id"
        ).fetchall()
    search_diag.record(rows_scanned=len(rows))
    docs = [json.loads(row["payload_json"]) for row in rows]
    return [
        doc
        for doc in docs
        if semantic_ids.intersection(set((doc.get("semantic") or {}).get("concept_ids") or []))
    ]


def _doc_matches(
    doc: dict[str, Any],
    *,
    skill: str | None,
    tool: str | None,
    files: str | None,
    file_kind: str | None,
    file_op: str | None,
    signal: str | None,
    facet_filters: tuple[tuple[str, str], ...],
    metadata_filters: tuple[tuple[str, str], ...],
    named_filters: tuple[tuple[str, str], ...],
    project: str | None,
    source: str | None = None,
    success: bool | None,
    success_unknown: bool,
    committed: bool | None,
    committed_unknown: bool,
    candidate_kind: str | None,
) -> bool:
    if _doc_source(doc) != _normalize_source(source):
        return False
    if project and doc.get("project_slug") != project:
        return False
    if (
        candidate_kind
        and doc.get("doc_type") != candidate_kind
        and _doc_candidate_kind(doc) != candidate_kind
    ):
        return False
    if skill and skill not in doc.get("skills", []):
        return False
    if tool and tool not in _facet_values(doc, "tool.name"):
        return False
    if files and not any(fnmatch(path, files) for path in doc.get("files", [])):
        return False
    if file_kind and file_kind.lstrip(".") not in _facet_values(doc, "file.kind"):
        return False
    if file_op and file_op not in _facet_values(doc, "file.operation"):
        return False
    if signal and not _has_signal(doc, signal):
        return False
    if facet_filters and not _matches_facet_filters(doc, facet_filters):
        return False
    if metadata_filters and not _matches_metadata_filters(doc, metadata_filters):
        return False
    if named_filters and not _matches_facet_filters(doc, named_filters):
        return False
    if success_unknown and success is not None:
        raise ValueError("Use --success/--no-success or --unknown-success, not both.")
    if committed_unknown and committed is not None:
        raise ValueError("Use --committed/--uncommitted or --unknown-committed, not both.")
    success_value = _bool_facet(doc, "outcome.success")
    committed_value = _bool_facet(doc, "outcome.committed")
    if success is not None and success_value is not success:
        return False
    if success_unknown and success_value is not None:
        return False
    if committed is not None and committed_value is not committed:
        return False
    if committed_unknown and committed_value is not None:
        return False
    return True


def _lexical_score_doc(
    doc: dict[str, Any],
    terms: list[str],
) -> tuple[float, dict[str, list[str]]]:
    if not terms:
        return 0.0, {}
    weights = {
        "title": 5.0,
        "intent": 4.0,
        "action": 3.0,
        "evidence": 2.0,
        "artifact": 1.0,
        "search_text": 0.5,
    }
    score = 0.0
    matched: dict[str, list[str]] = {}
    fields = dict(doc.get("fields") or {})
    fields["search_text"] = doc.get("search_text", "")
    for field, weight in weights.items():
        field_terms = set(_terms(str(fields.get(field, "")).lower()))
        hits = [term for term in terms if term in field_terms]
        if hits:
            matched[f"{field}_text" if field != "search_text" else field] = hits
            score += weight * len(hits)
    return score, matched


def _semantic_score_doc(
    doc: dict[str, Any],
    expansion: dict[str, Any] | None,
) -> tuple[float, dict[str, list[str]]]:
    if not expansion:
        return 0.0, {}
    expected_ids = set(expansion.get("concept_ids") or [])
    doc_ids = set((doc.get("semantic") or {}).get("concept_ids") or [])
    matched_ids = sorted(expected_ids.intersection(doc_ids))
    if not matched_ids:
        return 0.0, {}
    labels: list[str] = []
    by_id = {
        str(concept.get("concept_id")): concept
        for concept in (expansion.get("concepts") or [])
        if concept.get("concept_id")
    }
    for concept_id in matched_ids:
        concept = by_id.get(concept_id) or {}
        labels.append(str(concept.get("name") or concept_id))
    return 8.0 * len(matched_ids), {"semantic": labels}


def _metadata_score_doc(
    doc: dict[str, Any],
    *,
    skill: str | None,
    tool: str | None,
    files: str | None,
    file_kind: str | None,
    file_op: str | None,
    signal: str | None,
    facet_filters: tuple[tuple[str, str], ...],
    metadata_filters: tuple[tuple[str, str], ...],
    named_filters: tuple[tuple[str, str], ...],
    project: str | None,
    success: bool | None,
    success_unknown: bool,
    committed: bool | None,
    committed_unknown: bool,
    candidate_kind: str | None,
    since: str | None,
    semantic: str | None,
) -> float:
    score = 0.0
    if skill:
        score += 3.0
    if tool:
        score += 2.0
    if files:
        score += 2.0
    if file_kind or file_op:
        score += 1.0
    if signal:
        score += 5.0
    if project:
        score += 1.0
    if success is not None or success_unknown:
        score += 1.0
    if committed is not None or committed_unknown:
        score += 1.0
    if candidate_kind:
        score += 2.0
    if since:
        score += 1.0
    if semantic:
        score += 1.0
    score += float(len(facet_filters) + len(metadata_filters) + len(named_filters))
    return score


def _facet_payload(facet: TraceFacet) -> dict[str, Any]:
    return facet.model_dump(mode="json")


def _signal_payload(signal: TraceSignal) -> dict[str, Any]:
    return signal.model_dump(mode="json")


def _evidence_refs(unit: TraceUnit) -> list[str]:
    refs: list[str] = [unit.unit_id, f"ot://trace/{unit.trace_id}/map"]
    refs.extend(unit.trail_refs)
    for facet in unit.facets:
        if facet.evidence_ref:
            refs.append(facet.evidence_ref)
    for signal in unit.signals:
        refs.extend(signal.evidence_refs)
    refs.extend(_metadata_refs(unit.metadata))
    return sorted({ref for ref in refs if isinstance(ref, str) and ref})


def _metadata_refs(metadata: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in (
        "map_ref",
        "map_node_ref",
        "map_node_refs",
        "patch_ref",
        "patch_refs",
        "trace_patch_ref",
        "trace_patch_refs",
        "resource_ref",
        "resource_refs",
        "anchor_ref",
        "anchor_refs",
        "git_anchor_ref",
        "git_anchor_refs",
        "containing_segment_id",
        "slice_id",
    ):
        value = metadata.get(key)
        if isinstance(value, str):
            refs.append(value)
        elif isinstance(value, list):
            refs.extend(item for item in value if isinstance(item, str))
    return refs


def _projection_search_text(doc: dict[str, Any]) -> str:
    parts: list[str] = []
    parts.extend(str(value) for value in (doc.get("fields") or {}).values() if value)
    parts.extend(str(value) for value in doc.get("files", []))
    parts.extend(str(value) for value in doc.get("skills", []))
    for facet in doc.get("facets", []):
        parts.append(str(facet.get("name", "")))
        parts.append(str(facet.get("value", "")))
    for signal in doc.get("signals", []):
        parts.append(str(signal.get("name", "")))
        parts.append(str(signal.get("value", "")))
    parts.extend(_simple_metadata_values(doc.get("metadata") or {}))
    parts.extend(_simple_metadata_values(doc.get("semantic") or {}))
    parts.extend(str(value) for value in doc.get("trail_refs", []))
    return " ".join(part for part in parts if part)


def _simple_metadata_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, bool):
        return [str(value).lower()]
    if isinstance(value, (str, int, float)):
        return [str(value)]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_simple_metadata_values(item))
        return out
    if isinstance(value, dict):
        out: list[str] = []
        for key, item in value.items():
            out.append(str(key))
            out.extend(_simple_metadata_values(item))
        return out
    return []


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
    return tuple(
        (name, value)
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
        )
        if value is not None
    )


def _matches_facet_filters(
    doc: dict[str, Any],
    filters: tuple[tuple[str, str], ...],
) -> bool:
    for name, expected in filters:
        values = _facet_values(doc, name)
        if not any(str(value).lower() == expected.lower() for value in values):
            return False
    return True


def _matches_metadata_filters(
    doc: dict[str, Any],
    filters: tuple[tuple[str, str], ...],
) -> bool:
    metadata = doc.get("metadata") or {}
    for name, expected in filters:
        if not _metadata_value_matches(metadata.get(name), expected):
            return False
    return True


def _metadata_value_matches(actual: Any, expected: str) -> bool:
    if isinstance(actual, bool):
        return str(actual).lower() == expected.lower()
    if isinstance(actual, (str, int, float)):
        return str(actual) == expected
    if isinstance(actual, list):
        return any(_metadata_value_matches(item, expected) for item in actual)
    return False


def _facet_values(doc: dict[str, Any], name: str) -> set[str]:
    return {
        str(facet.get("value"))
        for facet in doc.get("facets", [])
        if facet.get("name") == name
    }


def _bool_facet(doc: dict[str, Any], name: str) -> bool | None:
    for facet in doc.get("facets", []):
        if facet.get("name") != name:
            continue
        value = facet.get("value")
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in {"1", "true", "yes"}
    return None


def _has_signal(doc: dict[str, Any], name: str) -> bool:
    for signal in doc.get("signals", []):
        if signal.get("name") == name and bool(signal.get("value")):
            return True
    return False


def _doc_candidate_kind(doc: dict[str, Any]) -> str:
    if _has_signal(doc, "tested_successful_fix_candidate"):
        return "bug_fix"
    return str(doc.get("doc_type") or "trace")


def _terms(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[A-Za-z0-9_@./-]+", text)]


def _fts_query(terms: list[str]) -> str:
    quoted: list[str] = []
    for term in terms:
        safe = term.replace('"', "")
        if safe:
            quoted.append(f'"{safe}"')
    return " ".join(quoted)


def _page_offset(page_token: str | None) -> int:
    if not page_token:
        return 0
    if not page_token.startswith("offset:"):
        raise ValueError("page token must have the form offset:<n>")
    try:
        return max(0, int(page_token.split(":", 1)[1]))
    except ValueError as exc:
        raise ValueError("page token must have the form offset:<n>") from exc


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
    parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _doc_timestamp(doc: dict[str, Any]) -> datetime:
    metadata = doc.get("metadata") or {}
    raw = metadata.get("timestamp_end") or metadata.get("timestamp_start")
    if isinstance(raw, str) and raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.fromtimestamp(0, tz=timezone.utc)


def _sha256_json(data: dict[str, Any]) -> str:
    return _sha256_text(_canonical_json(data))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

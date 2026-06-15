"""Read-only trace-level SQLite FTS search snapshot.

The legacy Trace Index is still useful for rich TraceMap/unit lookup, but it
is the wrong serving primitive for search. This module owns the compact
trace-level projection used by query-like commands: maintenance builds it from
raw traces, then search opens it read-only and immutable.

Contract (issue #30): query verbs stay read-only in steady state but self-heal
exactly once per invocation when no servable snapshot exists. ``search_traces``
takes ``auto_rebuild=True`` by default: a missing snapshot, or a verification
failure (``stale`` / ``unreadable`` / ``missing_schema`` / ``wrong_schema``),
triggers a single compact ``build_trace_search_snapshot`` build + retry and
then serves rc=0 results with ``rebuilt_index=True``. This is an auto-rebuild
of the compact v3 snapshot, NOT a raw trace scan and NOT the heavy legacy index
rebuild (the #22 perf trap is not reintroduced). ``maintenance_needed`` / exit 3
remains only when the self-heal build itself fails (or the snapshot is still
missing after building). Pass ``auto_rebuild=False`` to keep the strict
raise-on-missing behavior.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from opentraces_schema import CandidatePacket, TraceFacet, TraceMap, TraceSignal, TraceUnit

from . import paths
from .boilerplate import headline_from_summary, intent_text_for_record, summary_for_record
from .provenance import provenance_from_record
from .query_helpers import _fts_query, _page_offset, _terms
from .semantic import expand_semantic_query, semantic_profile_from_facets
from .text_redaction import redact_index_text
from .trace_map import build_trace_map, slice_trace_map_for_candidate
from .trace_search_state import clear_dirty_marker_if_unchanged, current_dirty_token


SNAPSHOT_SCHEMA_VERSION = "opentraces.trace_search_snapshot.v5"
SNAPSHOT_DB_NAME = "search.sqlite"
DEFAULT_LIMIT = 20
VISIBLE_FILE_LIMIT = 8
VISIBLE_FACET_LIMIT = 24
VISIBLE_SIGNAL_LIMIT = 12

TITLE_LIMIT = 240
SUMMARY_LIMIT = 1200
FIELD_LIMIT = 4000
LIST_TEXT_LIMIT = 3000


class SearchSnapshotNeedsRebuild(RuntimeError):
    """Raised when a query cannot be served by the immutable snapshot."""

    def __init__(self, reason: str, *, path: Path | None = None):
        self.reason = reason
        self.path = path
        super().__init__(reason)


def _notify_rebuilding() -> None:
    """One-time stderr notice for the issue #30 self-heal build.

    Auto-rebuild on a large corpus is a silent multi-second stall otherwise.
    Emitted to stderr ONLY, and only when stderr is an interactive terminal —
    stdout carries the JSON query contract and must never be polluted (issue #27
    M). The TTY gate also keeps the notice out of piped / ``2>&1``-redirected
    machine consumers and the Click ``CliRunner`` (whose captured stderr is not
    a TTY), so the ``--json`` payload stays parseable end to end.
    """

    import sys

    try:
        interactive = sys.stderr.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if not interactive:
        return
    print(
        "opentraces: rebuilding search snapshot (one-time)...",
        file=sys.stderr,
        flush=True,
    )


@dataclass(frozen=True)
class SearchDiagnostics:
    used_search_snapshot: bool = True
    used_fts: bool = False
    rows_examined: int = 0
    hits_returned: int = 0
    hydrated_count: int = 0
    raw_trace_scan: bool = False
    wrote_to_index: bool = False
    rebuilt_index: bool = False
    python_full_corpus_sort: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "used_search_snapshot": self.used_search_snapshot,
            "used_fts": self.used_fts,
            "rows_examined": self.rows_examined,
            "hits_returned": self.hits_returned,
            "hydrated_count": self.hydrated_count,
            "raw_trace_scan": self.raw_trace_scan,
            "wrote_to_index": self.wrote_to_index,
            "rebuilt_index": self.rebuilt_index,
            "python_full_corpus_sort": self.python_full_corpus_sort,
        }


@dataclass(frozen=True)
class SearchHit:
    trace_id: str
    project_slug: str
    title: str
    summary: str
    score: float
    matched_fields: dict[str, list[str]]
    source_path: str
    source_hash: str
    timestamp_start: str | None = None
    timestamp_end: str | None = None
    files: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    facets: list[TraceFacet] = field(default_factory=list)
    signals: list[TraceSignal] = field(default_factory=list)
    committed: bool | None = None
    commit_sha: str | None = None
    commit_subject: str | None = None
    provenance_color: str | None = None
    candidate_kind: str = "trace"


@dataclass(frozen=True)
class SearchPage:
    hits: list[SearchHit]
    next_page_token: str | None
    total: int
    diagnostics: SearchDiagnostics
    warnings: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class SkillUsage:
    skill_name: str
    invocation_count: int
    trace_count: int
    agents: dict[str, int]
    sources: dict[str, int]
    projects: dict[str, int]
    latest_invocation_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "invocation_count": self.invocation_count,
            "trace_count": self.trace_count,
            "agents": self.agents,
            "sources": self.sources,
            "projects": self.projects,
            "latest_invocation_at": self.latest_invocation_at,
        }


@dataclass(frozen=True)
class SkillUsagePage:
    skills: list[SkillUsage]
    next_page_token: str | None
    total_skills: int
    total_invocations: int
    diagnostics: SearchDiagnostics


@dataclass(frozen=True)
class SearchFilters:
    project: str | None = None
    since: str | None = None
    latest_generation: bool = True
    skill: str | None = None
    tool: str | None = None
    files: str | None = None
    file_kind: str | None = None
    file_op: str | None = None
    signal: str | None = None
    facet_filters: tuple[str, ...] = ()
    provider: str | None = None
    cmd_family: str | None = None
    bash_action: str | None = None
    test_framework: str | None = None
    service: str | None = None
    service_channel: str | None = None
    dependency: str | None = None
    git_tier: str | None = None
    survival: str | None = None
    success: bool | None = None
    success_unknown: bool = False
    committed: bool | None = None
    committed_unknown: bool = False
    candidate_kind: str | None = None
    sort_order: str = "relevance"


@dataclass(frozen=True)
class SnapshotSummary:
    path: Path
    trace_count: int
    schema_version: str
    source_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "trace_count": self.trace_count,
            "schema_version": self.schema_version,
            "source_hash": self.source_hash,
        }


@dataclass
class _SearchDocument:
    trace_id: str
    project_slug: str
    timestamp_start: str | None
    timestamp_end: str | None
    title: str
    summary: str
    intent_text: str
    action_text: str
    file_text: str
    skill_text: str
    facet_text: str
    source_path: str
    source_hash: str
    search_group_key: str
    latest_generation: bool
    files: list[str]
    skills: list[str]
    tools: list[str]
    skill_invocations: list[dict[str, Any]]
    facets: list[TraceFacet]
    signals: list[TraceSignal]
    provenance: dict[str, Any]
    candidate_kind: str
    # Inverse supersession pointer (``metadata.superseded_trace_ids``). Not a
    # stored column — consumed only by the build-time second pass that marks the
    # traces it names as non-latest (issue #27 D).
    superseded_trace_ids: tuple[str, ...] = ()


def default_snapshot_path() -> Path:
    return paths.OPENTRACES_DIR / "index" / SNAPSHOT_DB_NAME


@contextlib.contextmanager
def _build_lock(snapshot_path: Path):
    """Serialize snapshot builds so one build can never publish another's tmp."""

    lock_path = snapshot_path.with_name(f"{snapshot_path.name}.build.lock")
    try:
        import fcntl
    except ImportError:  # non-POSIX fallback: best-effort, unserialized
        yield
        return
    with open(lock_path, "w", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def build_trace_search_snapshot(path: Path | None = None) -> SnapshotSummary:
    """Build a fresh compact snapshot and atomically replace the serving DB."""

    snapshot_path = path or default_snapshot_path()
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    with _build_lock(snapshot_path):
        return _build_trace_search_snapshot_locked(snapshot_path)


def _build_trace_search_snapshot_locked(snapshot_path: Path) -> SnapshotSummary:
    # Per-process tmp name: a crashed or concurrent build's leftover tmp can
    # never be adopted or published by another build.
    tmp_path = snapshot_path.with_name(f"{snapshot_path.name}.{os.getpid()}.tmp")
    for stale in snapshot_path.parent.glob(f"{snapshot_path.name}.*.tmp*"):
        try:
            stale.unlink()
        except OSError:
            pass
    if tmp_path.exists():
        tmp_path.unlink()
    _unlink_sidecars(tmp_path)

    dirty_token_before_build = current_dirty_token()
    trace_count = 0
    seen_trace_ids: set[str] = set()
    superseded_by_inverse: set[str] = set()
    try:
        with sqlite3.connect(tmp_path) as conn:
            conn.execute("pragma journal_mode=DELETE")
            _create_schema(conn)
            for doc in _iter_documents():
                if doc.trace_id in seen_trace_ids:
                    continue
                seen_trace_ids.add(doc.trace_id)
                _insert_doc(conn, doc)
                superseded_by_inverse.update(doc.superseded_trace_ids)
                trace_count += 1
            # Second pass (issue #27 D): any trace named in another trace's
            # ``superseded_trace_ids`` inverse pointer is a prior generation, so
            # demote it from latest. Deferred until every doc is inserted because
            # the pointer-holder may be processed before the trace it names.
            _apply_inverse_supersession(conn, superseded_by_inverse)
            # Order-independent corpus hash over the per-trace content hashes
            # already stored on each row, so an incremental refresh and a full
            # rebuild of identical content converge to the same value.
            source_hash = _meta_hash_from_db(conn)
            _write_meta(conn, source_hash, trace_count)
            try:
                conn.execute("insert into trace_fts(trace_fts) values('optimize')")
            except sqlite3.DatabaseError:
                pass
            conn.commit()
            try:
                conn.execute("vacuum")
            except sqlite3.DatabaseError:
                pass
        tmp_path.replace(snapshot_path)
        _unlink_sidecars(snapshot_path)
        clear_dirty_marker_if_unchanged(dirty_token_before_build)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        _unlink_sidecars(tmp_path)
        raise

    return SnapshotSummary(
        path=snapshot_path,
        trace_count=trace_count,
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        source_hash=source_hash,
    )


def _meta_hash_from_db(conn: sqlite3.Connection) -> str:
    """Corpus hash derived from the per-trace content hashes in the DB.

    Order-independent (sorted by trace_id), so a bounded incremental refresh
    and a full rebuild of identical content produce the same value.
    """

    hasher = hashlib.sha256()
    for row in conn.execute("select trace_id, source_hash from traces order by trace_id"):
        hasher.update(f"{row[0]}:{row[1]}\n".encode("utf-8"))
    return hasher.hexdigest()


# The exact FTS5 external-content column list, in declaration order (must stay
# in lockstep with the ``create virtual table trace_fts`` body in
# ``_create_schema`` and the insert in ``_insert_doc``). ``trace_fts`` is an
# external-content table (``content='traces'`` / ``content_rowid='rowid'``), so
# its index is NOT auto-maintained when a content row is deleted — the caller
# must emit a paired FTS ``'delete'`` command carrying the OLD column values
# BEFORE the content row goes away, or the FTS index silently desyncs (issue
# #41): orphaned postings keep matching deleted/changed traces, and the per-row
# refresh path corrupts the index over time.
_FTS_COLUMNS = (
    "title",
    "summary",
    "intent_text",
    "action_text",
    "file_text",
    "skill_text",
    "facet_text",
)


def _fts_delete_doc(conn: sqlite3.Connection, trace_id: str) -> None:
    """Emit the external-content FTS5 ``'delete'`` bookkeeping for one trace.

    Reads the OLD values of the seven indexed FTS columns straight from the
    ``traces`` content table (joined by ``rowid``, which is also the FTS
    ``content_rowid``) and issues the special ``insert into trace_fts(trace_fts,
    rowid, <cols...>) values('delete', ...)`` command that removes that row's
    postings from the FTS index. This MUST run while the content row still
    exists — once ``_delete_trace_rows`` removes it the old column values are
    gone and the FTS index cannot be reconciled by delete bookkeeping anymore.

    No-op when the trace_id is absent from the content table (a delete of a
    never-indexed id, or a second delete in the same refresh).
    """

    row = conn.execute(
        f"select rowid, {', '.join(_FTS_COLUMNS)} from traces where trace_id = ?",
        (trace_id,),
    ).fetchone()
    if row is None:
        return
    placeholders = ", ".join("?" for _ in _FTS_COLUMNS)
    conn.execute(
        f"insert into trace_fts(trace_fts, rowid, {', '.join(_FTS_COLUMNS)}) "
        f"values ('delete', ?, {placeholders})",
        (row[0], *row[1:]),
    )


def _delete_trace_rows(conn: sqlite3.Connection, trace_id: str) -> None:
    for table in (
        "traces",
        "trace_facets",
        "trace_files",
        "trace_tools",
        "trace_skills",
        "skill_invocations",
        "trace_signals",
        "trace_concepts",
    ):
        conn.execute(f"delete from {table} where trace_id = ?", (trace_id,))


def _load_doc_for_trace_id(trace_id: str) -> _SearchDocument | None:
    """Bounded single-trace doc load: bucket pointer first, then legacy stores."""

    from .bucket_store import read_trace_record_object, trace_records_root

    root = trace_records_root()
    if root.exists():
        for pointer_path in sorted(root.glob(f"*/{trace_id}/current.json")):
            obj = read_trace_record_object(pointer_path)
            if obj is None:
                continue
            return _doc_from_record(
                obj.record,
                project_slug=obj.project_slug,
                source_layer=obj.source_layer,
                trace_path=obj.path,
            )
    from . import trace_index as ti

    projects_root = paths.PROJECTS_DIR
    if projects_root.exists():
        for trace_path in sorted(projects_root.glob(f"*/traces/{trace_id}.jsonl")):
            for record in ti._iter_trace_file_records(trace_path):
                if record.trace_id == trace_id:
                    return _doc_from_record(
                        record,
                        project_slug=trace_path.parent.parent.name,
                        source_layer="canonical",
                        trace_path=trace_path,
                    )
    staging_root = getattr(paths, "STAGING_DIR", None)
    if staging_root and staging_root.exists():
        for trace_path in sorted(staging_root.glob(f"{trace_id}.jsonl")):
            for record in ti._iter_trace_file_records(trace_path):
                if record.trace_id == trace_id:
                    return _doc_from_record(
                        record,
                        project_slug="_staging",
                        source_layer="staging",
                        trace_path=trace_path,
                    )
    return None


def refresh_trace_search_snapshot(
    changed_trace_ids: list[str] | tuple[str, ...] = (),
    deleted_trace_ids: list[str] | tuple[str, ...] = (),
    *,
    path: Path | None = None,
) -> SnapshotSummary | None:
    """Bounded incremental refresh of the read-only snapshot (keep-warm path).

    Copies the serving file, applies the per-trace delta, rebuilds the FTS
    index from content, and atomically swaps — the serving snapshot stays
    immutable for readers throughout. Returns ``None`` (leaving any dirty
    marker in place as the backstop for an explicit ``trace index`` rebuild)
    when there is no snapshot, the schema is from another version, or there
    is nothing to apply.
    """

    snapshot_path = path or default_snapshot_path()
    if not snapshot_path.exists():
        return None
    ids = list(dict.fromkeys([*changed_trace_ids, *deleted_trace_ids]))
    if not ids:
        return None
    with _build_lock(snapshot_path):
        return _refresh_snapshot_locked(snapshot_path, changed_trace_ids, ids)


def _refresh_snapshot_locked(
    snapshot_path: Path,
    changed_trace_ids: list[str] | tuple[str, ...],
    all_ids: list[str],
) -> SnapshotSummary | None:
    import shutil

    dirty_token_before = current_dirty_token()
    tmp_path = snapshot_path.with_name(f"{snapshot_path.name}.{os.getpid()}.refresh.tmp")
    try:
        shutil.copyfile(snapshot_path, tmp_path)
        with sqlite3.connect(tmp_path) as conn:
            conn.execute("pragma journal_mode=DELETE")
            row = conn.execute(
                "select value from snapshot_meta where key = 'schema_version'"
            ).fetchone()
            if row is None or str(row[0]) != SNAPSHOT_SCHEMA_VERSION:
                tmp_path.unlink()
                return None
            # Per-row external-content FTS bookkeeping (issue #41): emit the FTS
            # ``'delete'`` for EVERY id in the delta (changed ∪ deleted) while
            # its content row still exists, THEN drop the content rows. A changed
            # trace is delete+reinsert: the delete here clears its OLD postings,
            # and ``_insert_doc`` below adds the NEW ones. This replaces the old
            # whole-table ``'rebuild'`` — which was O(corpus) per refresh and is
            # the perf trap this package closes (refresh-of-1 must touch only the
            # changed rows, never re-scan the whole content table).
            for trace_id in all_ids:
                _fts_delete_doc(conn, trace_id)
                _delete_trace_rows(conn, trace_id)
            inverse_superseded: set[str] = set()
            for trace_id in dict.fromkeys(changed_trace_ids):
                doc = _load_doc_for_trace_id(trace_id)
                if doc is not None:
                    _insert_doc(conn, doc)
                    inverse_superseded.update(doc.superseded_trace_ids)
            # Carry the inverse-supersession demotion through the keep-warm path
            # too (issue #27 D): a freshly ingested trace that replaces an older
            # one demotes that older row even when only the newer is in the delta.
            # SAFE without FTS bookkeeping: ``_apply_inverse_supersession`` updates
            # only ``traces.latest_generation`` (a content-table column NOT in
            # ``_FTS_COLUMNS``), so the external-content FTS index is unaffected.
            # INVARIANT: any future UPDATE of an FTS-indexed column on the
            # ``traces`` table MUST go through ``_fts_delete_doc`` + reinsert (or a
            # paired ``'delete'`` / ``'insert'`` pair) — a bare UPDATE of an
            # indexed column silently desyncs the external-content index.
            _apply_inverse_supersession(conn, inverse_superseded)
            trace_count = conn.execute("select count(*) from traces").fetchone()[0]
            source_hash = _meta_hash_from_db(conn)
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            conn.executemany(
                "insert into snapshot_meta(key, value) values (?, ?) "
                "on conflict(key) do update set value = excluded.value",
                [
                    ("built_at", now),
                    ("source_hash", source_hash),
                    ("trace_count", str(trace_count)),
                ],
            )
            conn.commit()
        tmp_path.replace(snapshot_path)
        _unlink_sidecars(snapshot_path)
        clear_dirty_marker_if_unchanged(dirty_token_before)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise

    return SnapshotSummary(
        path=snapshot_path,
        trace_count=trace_count,
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        source_hash=source_hash,
    )


def search_traces(
    text: str | None,
    filters: SearchFilters | None = None,
    *,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    semantic: str | None = None,
    path: Path | None = None,
    auto_rebuild: bool = True,
) -> SearchPage:
    """Search the immutable snapshot and return compact trace hits.

    Read-only-in-steady-state with a single self-heal (issue #30). When the
    snapshot is missing — or verification trips a needs-rebuild condition
    (``stale`` / ``unreadable`` / ``missing_schema`` / ``wrong_schema``) — and
    ``auto_rebuild`` is set (the default), this builds the compact v3 snapshot
    exactly once via ``build_trace_search_snapshot`` (already ``_build_lock``
    serialized + atomic-swap + dirty-marker clearing) and serves the result. It
    is NOT a raw scan and it never calls the heavy legacy index rebuild, so the
    #22 perf trap is not reintroduced; the steady-state read path stays
    untouched once a servable snapshot exists. If the snapshot is still missing
    after the build, or the build itself fails, the ``SearchSnapshotNeedsRebuild``
    propagates and the CLI surfaces ``maintenance_needed`` / exit 3. Pass
    ``auto_rebuild=False`` to keep the strict raise-on-missing behavior.
    """

    snapshot_path = path or default_snapshot_path()
    rebuilt = False
    if not snapshot_path.exists():
        if not auto_rebuild:
            raise SearchSnapshotNeedsRebuild("missing", path=snapshot_path)
        _notify_rebuilding()
        build_trace_search_snapshot(path=snapshot_path)
        rebuilt = True
        if not snapshot_path.exists():
            raise SearchSnapshotNeedsRebuild("missing", path=snapshot_path)
    filters = filters or SearchFilters()
    terms = _terms(text or "")
    semantic_expansion = expand_semantic_query(semantic) if semantic else None
    semantic_ids = set((semantic_expansion or {}).get("concept_ids") or [])
    if semantic and not semantic_ids:
        # Base parity: a semantic phrase outside the deterministic concept
        # lexicon falls back to lexical matching over the phrase itself
        # (never a maintenance_needed dead-end — no rebuild can make an
        # unknown concept match).
        terms = terms or _terms(semantic)

    offset = _page_offset(cursor)
    page_size = max(1, int(limit or DEFAULT_LIMIT))

    def _read() -> tuple[list[sqlite3.Row], int]:
        with _connect_readonly(snapshot_path) as conn:
            _verify_snapshot(conn, snapshot_path)
            return _query_rows(
                conn,
                terms=terms,
                semantic_ids=semantic_ids,
                filters=filters,
                limit=page_size + 1,
                offset=offset,
            )

    try:
        rows, total = _read()
    except SearchSnapshotNeedsRebuild:
        # A stale dirty marker (or an otherwise unservable snapshot) converges
        # with exactly one build + one retry. If the rebuild already ran above,
        # or auto_rebuild is off, re-raise so the CLI reports maintenance_needed.
        if not auto_rebuild or rebuilt:
            raise
        _notify_rebuilding()
        build_trace_search_snapshot(path=snapshot_path)
        rebuilt = True
        rows, total = _read()

    has_more = len(rows) > page_size
    selected = rows[:page_size]
    hits = [_hit_from_row(row, terms) for row in selected]
    diagnostics = SearchDiagnostics(
        used_fts=bool(terms),
        rows_examined=total,
        hits_returned=len(hits),
        rebuilt_index=rebuilt,
        wrote_to_index=rebuilt,
    )
    next_page_token = f"offset:{offset + page_size}" if has_more else None
    return SearchPage(
        hits=hits,
        next_page_token=next_page_token,
        total=total,
        diagnostics=diagnostics,
    )


def list_skill_usage(
    filters: SearchFilters | None = None,
    *,
    limit: int = 50,
    cursor: str | None = None,
    path: Path | None = None,
    auto_rebuild: bool = True,
) -> SkillUsagePage:
    """Return skill invocation counts from the compact search snapshot."""

    snapshot_path = path or default_snapshot_path()
    rebuilt = False
    if not snapshot_path.exists():
        if not auto_rebuild:
            raise SearchSnapshotNeedsRebuild("missing", path=snapshot_path)
        _notify_rebuilding()
        build_trace_search_snapshot(path=snapshot_path)
        rebuilt = True
        if not snapshot_path.exists():
            raise SearchSnapshotNeedsRebuild("missing", path=snapshot_path)
    filters = filters or SearchFilters()
    offset = _page_offset(cursor)
    page_size = max(1, int(limit or 50))

    def _read() -> tuple[list[SkillUsage], int, int]:
        with _connect_readonly(snapshot_path) as conn:
            _verify_snapshot(conn, snapshot_path)
            return _query_skill_usage(
                conn,
                filters=filters,
                limit=page_size + 1,
                offset=offset,
            )

    try:
        skills, total_skills, total_invocations = _read()
    except SearchSnapshotNeedsRebuild:
        if not auto_rebuild or rebuilt:
            raise
        _notify_rebuilding()
        build_trace_search_snapshot(path=snapshot_path)
        rebuilt = True
        skills, total_skills, total_invocations = _read()

    has_more = len(skills) > page_size
    selected = skills[:page_size]
    diagnostics = SearchDiagnostics(
        rows_examined=total_invocations,
        hits_returned=len(selected),
        rebuilt_index=rebuilt,
        wrote_to_index=rebuilt,
    )
    next_page_token = f"offset:{offset + page_size}" if has_more else None
    return SkillUsagePage(
        skills=selected,
        next_page_token=next_page_token,
        total_skills=total_skills,
        total_invocations=total_invocations,
        diagnostics=diagnostics,
    )


def list_skill_invocation_units(
    *,
    skill: str,
    project: str | None = None,
    limit: int | None = None,
    path: Path | None = None,
    auto_rebuild: bool = True,
) -> list[TraceUnit]:
    """Return lightweight ``skill_invocation`` units from the search snapshot."""

    snapshot_path = path or default_snapshot_path()
    rebuilt = False
    if not snapshot_path.exists():
        if not auto_rebuild:
            raise SearchSnapshotNeedsRebuild("missing", path=snapshot_path)
        _notify_rebuilding()
        build_trace_search_snapshot(path=snapshot_path)
        rebuilt = True
        if not snapshot_path.exists():
            raise SearchSnapshotNeedsRebuild("missing", path=snapshot_path)

    def _read() -> list[TraceUnit]:
        with _connect_readonly(snapshot_path) as conn:
            _verify_snapshot(conn, snapshot_path)
            return _query_skill_invocation_units(
                conn,
                skill=skill,
                project=project,
                limit=limit,
            )

    try:
        return _read()
    except SearchSnapshotNeedsRebuild:
        if not auto_rebuild or rebuilt:
            raise
        _notify_rebuilding()
        build_trace_search_snapshot(path=snapshot_path)
        return _read()


def candidate_packet_for_hit(
    hit: SearchHit,
    *,
    include_slice: str | None = None,
    max_slice_nodes: int = 40,
) -> tuple[CandidatePacket, int]:
    """Convert a compact search hit to the existing CandidatePacket shape.

    Returns ``(packet, hydrated_count)``. Normal query output is served
    entirely from the snapshot. Slice previews hydrate exactly one final hit.
    """

    hydrated_count = 0
    slice_preview: TraceMap | None = None
    if include_slice:
        hydrated_count = 1
        slice_preview = _slice_preview_for_hit(hit, max_slice_nodes=max_slice_nodes)
    headline = headline_from_summary(hit.summary or hit.title)
    visible_files = _visible_files(hit.files)
    visible_facets = _visible_facets(hit.facets)
    visible_signals = _visible_signals(hit.signals)
    return (
        CandidatePacket(
            unit_id=f"tu:{hit.trace_id}:trace",
            unit_type="trace",
            trace_id=hit.trace_id,
            project_slug=hit.project_slug,
            title=hit.title or hit.trace_id,
            headline=headline or None,
            summary=hit.summary or None,
            provenance_color=hit.provenance_color,
            committed=hit.committed,
            commit_sha=hit.commit_sha,
            commit_subject=hit.commit_subject,
            intent_preview=_preview(hit.summary or hit.title),
            candidate_kind=hit.candidate_kind,
            match_explanation=_match_explanation(hit.matched_fields, hit),
            score=round(hit.score, 3),
            score_parts={"snapshot_fts": round(hit.score, 3)} if hit.score else {},
            matched_fields=hit.matched_fields,
            facets=visible_facets,
            signals=visible_signals,
            skills=hit.skills,
            files=visible_files,
            map_ref=f"ot://trace/{hit.trace_id}/map",
            refs={
                "trace": hit.trace_id,
                "unit": f"tu:{hit.trace_id}:trace",
                "map": f"ot://trace/{hit.trace_id}/map",
            },
            slice_preview=slice_preview,
            limitations=["candidate_packet_trace_snapshot"],
            metadata={
                "snapshot_source_path": hit.source_path,
                "snapshot_source_hash": hit.source_hash,
                "include_slice": include_slice,
                "file_count": len(hit.files),
                "files_truncated": len(visible_files) < len(hit.files),
                "facet_count": len(hit.facets),
                "facets_truncated": len(visible_facets) < len(hit.facets),
                "signal_count": len(hit.signals),
                "signals_truncated": len(visible_signals) < len(hit.signals),
            },
        ),
        hydrated_count,
    )


def snapshot_status(path: Path | None = None) -> dict[str, Any]:
    snapshot_path = path or default_snapshot_path()
    base = {
        "path": str(snapshot_path),
        "dirty": current_dirty_token() is not None,
        "wal_exists": snapshot_path.with_name(snapshot_path.name + "-wal").exists(),
        "shm_exists": snapshot_path.with_name(snapshot_path.name + "-shm").exists(),
    }
    if not snapshot_path.exists():
        return {"state": "missing", **base}
    try:
        with _connect_readonly(snapshot_path) as conn:
            meta = {
                str(row["key"]): str(row["value"])
                for row in conn.execute("select key, value from snapshot_meta")
            }
            schema_version = meta.get("schema_version")
            if schema_version != SNAPSHOT_SCHEMA_VERSION:
                return {
                    "state": "wrong_schema",
                    **base,
                    "schema_version": schema_version,
                    "expected_schema_version": SNAPSHOT_SCHEMA_VERSION,
                    "size_bytes": snapshot_path.stat().st_size,
                }
            count = int(
                conn.execute("select count(*) from traces").fetchone()[0]
            )
    except sqlite3.DatabaseError as exc:
        return {"state": "unreadable", **base, "error": str(exc)}
    state = "stale" if base["dirty"] else "ok"
    return {
        "state": state,
        **base,
        "schema_version": meta.get("schema_version"),
        "trace_count": count,
        "source_hash": meta.get("source_hash"),
        "built_at": meta.get("built_at"),
        "size_bytes": snapshot_path.stat().st_size,
    }


def get_trace_source_path(trace_id: str, path: Path | None = None) -> Path | None:
    snapshot_path = path or default_snapshot_path()
    if not snapshot_path.exists():
        return None
    try:
        with _connect_readonly(snapshot_path) as conn:
            _verify_snapshot(conn, snapshot_path)
            row = conn.execute(
                "select source_path from traces where trace_id = ?",
                (trace_id,),
            ).fetchone()
    except (SearchSnapshotNeedsRebuild, sqlite3.DatabaseError):
        return None
    return Path(row["source_path"]) if row else None


def _iter_documents():
    # Union of the bucket layer and the legacy project/staging stores, deduped
    # by trace_id with the bucket winning. A bucket-only early-return would
    # make legacy-store traces invisible until a keep-warm sync mirrors them,
    # so an explicit rebuild must read both layers itself.
    seen: set[str] = set()
    for record, project_slug, source_layer, trace_path in _iter_bucket_trace_records():
        if record.trace_id in seen:
            continue
        seen.add(record.trace_id)
        yield _doc_from_record(
            record,
            project_slug=project_slug,
            source_layer=source_layer,
            trace_path=trace_path,
        )
    for record, project_slug, source_layer, trace_path in _iter_legacy_trace_records():
        if record.trace_id in seen:
            continue
        seen.add(record.trace_id)
        yield _doc_from_record(
            record,
            project_slug=project_slug,
            source_layer=source_layer,
            trace_path=trace_path,
        )


def _iter_bucket_trace_records():
    from .bucket_store import read_trace_record_object, trace_records_root

    root = trace_records_root()
    if not root.exists():
        return
    for pointer_path in sorted(root.glob("*/*/current.json")):
        obj = read_trace_record_object(pointer_path)
        if obj is None:
            continue
        yield obj.record, obj.project_slug, obj.source_layer, obj.path


def _iter_legacy_trace_records():
    from . import trace_index as ti

    projects_root = paths.PROJECTS_DIR
    if projects_root.exists():
        for project_home in sorted(path for path in projects_root.iterdir() if path.is_dir()):
            traces_dir = project_home / "traces"
            if not traces_dir.exists():
                continue
            for trace_path in sorted(traces_dir.glob("*.jsonl")):
                for record in ti._iter_trace_file_records(trace_path):
                    yield record, project_home.name, "canonical", trace_path
    staging_root = getattr(paths, "STAGING_DIR", None)
    if staging_root and staging_root.exists() and staging_root.is_dir():
        for trace_path in sorted(staging_root.glob("*.jsonl")):
            for record in ti._iter_trace_file_records(trace_path):
                yield record, "_staging", "staging", trace_path


def _collect_documents() -> list[_SearchDocument]:
    return list(_iter_documents())


def _doc_from_record(
    record: Any,
    *,
    project_slug: str,
    source_layer: str,
    trace_path: Path,
) -> _SearchDocument:
    from . import trace_index as ti

    summary = summary_for_record(record, fallback=ti._first_user_text(record)) or ""
    headline = headline_from_summary(summary)
    files = _record_files(record)
    skills = ti._trace_skills(record)
    tools = ti._trace_tools(record)
    facets = ti._trace_facets(record, project_slug, skills, tools, files)
    facets.append(TraceFacet(name="source_layer", value=source_layer, source="exact_schema"))
    facets = ti._dedupe_facets(facets)
    signals = _record_signals(record, skills=skills)
    facet_parts = [f"{facet.name}:{facet.value}" for facet in facets]
    signal_names = [signal.name for signal in signals if bool(signal.value)]
    concept_profile = semantic_profile_from_facets(facets)
    semantic_text = " ".join(
        [
            *[str(cid) for cid in concept_profile.get("concept_ids") or []],
            *[
                " ".join(
                    str(v)
                    for v in (
                        concept.get("name"),
                        concept.get("kind"),
                        " ".join(concept.get("aliases") or []),
                        " ".join(concept.get("categories") or []),
                    )
                    if v
                )
                for concept in concept_profile.get("concepts") or []
            ],
        ]
    )
    provenance = provenance_from_record(record)
    return _SearchDocument(
        trace_id=record.trace_id,
        project_slug=project_slug,
        timestamp_start=_normalize_ts(record.timestamp_start),
        timestamp_end=_normalize_ts(record.timestamp_end),
        title=_limit_text(headline or record.task.description or ti._first_user_text(record) or record.trace_id, TITLE_LIMIT),
        summary=_limit_text(summary, SUMMARY_LIMIT),
        intent_text=_limit_text(intent_text_for_record(record) or summary, FIELD_LIMIT),
        action_text=_limit_text(" ".join(ti._tool_texts(record)), FIELD_LIMIT),
        file_text=_limit_text(" ".join(files), LIST_TEXT_LIMIT),
        skill_text=_limit_text(" ".join([*skills, *tools]), LIST_TEXT_LIMIT),
        facet_text=_limit_text(" ".join([*facet_parts, *signal_names, semantic_text]), LIST_TEXT_LIMIT),
        source_path=str(trace_path),
        source_hash=_record_hash(record, trace_path),
        search_group_key=_search_group_key(headline or summary or record.trace_id),
        latest_generation=not bool(record.metadata.get("superseded_by")),
        superseded_trace_ids=_superseded_trace_ids(record),
        files=files,
        skills=skills,
        tools=tools,
        skill_invocations=_skill_invocation_docs(
            record,
            project_slug=project_slug,
            title=ti._index_text(headline or record.task.description or ti._first_user_text(record) or record.trace_id),
            trace_facets=facets,
            timestamp_start=_normalize_ts(record.timestamp_start),
            timestamp_end=_normalize_ts(record.timestamp_end),
        ),
        facets=facets,
        signals=signals,
        provenance=provenance,
        candidate_kind=_candidate_kind_from_signals(signals),
    )


def _skill_invocation_docs(
    record: Any,
    *,
    project_slug: str,
    title: str,
    trace_facets: list[TraceFacet],
    timestamp_start: str | None,
    timestamp_end: str | None,
) -> list[dict[str, Any]]:
    """Compact per-invocation rows used by the fast skill inventory query."""

    del project_slug, title, trace_facets
    from .skill_detection import detect_skill_invocations

    docs: list[dict[str, Any]] = []
    seen_invocations: set[tuple[str, str | None, int | None, str]] = set()
    agent_name = str(getattr(getattr(record, "agent", None), "name", None) or "unknown")
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
        if invocation.tool_call_id:
            unit_id = f"tu:{record.trace_id}:skill:{invocation.tool_call_id}"
        else:
            idx = invocation.metadata_index if invocation.metadata_index is not None else 0
            unit_id = f"tu:{record.trace_id}:skill:metadata:{idx}"
        docs.append(
            {
                "unit_id": unit_id,
                "skill_name": invocation.skill_name,
                "agent_name": agent_name,
                "source": invocation.source,
                "confidence": "high",
                "timestamp_start": timestamp_start,
                "timestamp_end": timestamp_end,
                "step_index": invocation.step_index,
                "tool_call_id": invocation.tool_call_id,
                "command_name": invocation.command_name or f"/{invocation.skill_name}",
            }
        )
    return docs


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table snapshot_meta (
            key text primary key,
            value text not null
        );
        create table traces (
            trace_id text primary key,
            project_slug text not null,
            timestamp_start text,
            timestamp_end text,
            title text not null,
            summary text not null,
            intent_text text not null,
            action_text text not null,
            file_text text not null,
            skill_text text not null,
            facet_text text not null,
            source_path text not null,
            source_hash text not null,
            search_group_key text not null,
            latest_generation integer not null default 1,
            files_json text not null,
            skills_json text not null,
            facets_json text not null,
            signals_json text not null,
            committed integer,
            commit_sha text,
            commit_subject text,
            provenance_color text,
            candidate_kind text not null
        );
        create virtual table trace_fts using fts5(
            title,
            summary,
            intent_text,
            action_text,
            file_text,
            skill_text,
            facet_text,
            content='traces',
            content_rowid='rowid'
        );
        create table trace_facets (
            trace_id text not null,
            name text not null,
            value text not null,
            value_norm text not null
        );
        create table trace_files (
            trace_id text not null,
            path text not null,
            ext text not null
        );
        create table trace_tools (
            trace_id text not null,
            tool_name text not null
        );
        create table trace_skills (
            trace_id text not null,
            skill_name text not null
        );
        create table skill_invocations (
            trace_id text not null,
            unit_id text not null,
            skill_name text not null,
            project_slug text not null,
            agent_name text not null,
            source text not null,
            confidence text not null,
            timestamp_start text,
            timestamp_end text,
            step_index integer,
            tool_call_id text,
            command_name text
        );
        create table trace_signals (
            trace_id text not null,
            signal_name text not null
        );
        create table trace_concepts (
            trace_id text not null,
            concept_id text not null,
            label text not null
        );
        create index idx_traces_project_time on traces(project_slug, timestamp_end);
        create index idx_traces_latest on traces(latest_generation, timestamp_end);
        create index idx_traces_search_group on traces(search_group_key, timestamp_end);
        create index idx_trace_facets_name_value on trace_facets(name, value_norm, trace_id);
        create index idx_trace_files_path on trace_files(path, trace_id);
        create index idx_trace_files_ext on trace_files(ext, trace_id);
        create index idx_trace_tools_name on trace_tools(tool_name, trace_id);
        create index idx_trace_skills_name on trace_skills(skill_name, trace_id);
        create index idx_skill_invocations_skill on skill_invocations(skill_name, trace_id);
        create index idx_skill_invocations_project_time on skill_invocations(project_slug, timestamp_end);
        create index idx_trace_signals_name on trace_signals(signal_name, trace_id);
        create index idx_trace_concepts_id on trace_concepts(concept_id, trace_id);
        """
    )


def _write_meta(conn: sqlite3.Connection, source_hash: str, trace_count: int) -> None:
    values = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_hash": source_hash,
        "trace_count": str(trace_count),
    }
    conn.executemany(
        "insert into snapshot_meta(key, value) values (?, ?)",
        sorted(values.items()),
    )


def _apply_inverse_supersession(
    conn: sqlite3.Connection, superseded_trace_ids: set[str]
) -> None:
    """Demote every trace named by an inverse ``superseded_trace_ids`` pointer.

    A no-op when no inverse pointers were seen. Idempotent — re-running over an
    already-marked corpus changes nothing.
    """

    if not superseded_trace_ids:
        return
    conn.executemany(
        "update traces set latest_generation = 0 where trace_id = ?",
        [(tid,) for tid in sorted(superseded_trace_ids)],
    )


def _insert_doc(conn: sqlite3.Connection, doc: _SearchDocument) -> None:
    conn.execute(
        """
        insert into traces(
            trace_id, project_slug, timestamp_start, timestamp_end, title, summary,
            intent_text, action_text, file_text, skill_text, facet_text,
            source_path, source_hash, search_group_key, latest_generation, files_json, skills_json,
            facets_json, signals_json, committed, commit_sha, commit_subject,
            provenance_color, candidate_kind
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc.trace_id,
            doc.project_slug,
            doc.timestamp_start,
            doc.timestamp_end,
            doc.title,
            doc.summary,
            doc.intent_text,
            doc.action_text,
            doc.file_text,
            doc.skill_text,
            doc.facet_text,
            doc.source_path,
            doc.source_hash,
            doc.search_group_key,
            1 if doc.latest_generation else 0,
            json.dumps(doc.files),
            json.dumps(doc.skills),
            json.dumps([facet.model_dump(mode="json") for facet in doc.facets]),
            json.dumps([signal.model_dump(mode="json") for signal in doc.signals]),
            _bool_to_int(doc.provenance.get("committed")),
            doc.provenance.get("commit_sha"),
            doc.provenance.get("commit_subject"),
            doc.provenance.get("provenance_color"),
            doc.candidate_kind,
        ),
    )
    rowid = conn.execute("select last_insert_rowid()").fetchone()[0]
    conn.execute(
        """
        insert into trace_fts(
            rowid, title, summary, intent_text, action_text, file_text, skill_text, facet_text
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rowid,
            doc.title,
            doc.summary,
            doc.intent_text,
            doc.action_text,
            doc.file_text,
            doc.skill_text,
            doc.facet_text,
        ),
    )
    for facet in doc.facets:
        value = str(facet.value)
        conn.execute(
            "insert into trace_facets(trace_id, name, value, value_norm) values (?, ?, ?, ?)",
            (doc.trace_id, facet.name, value, _norm(value)),
        )
    for path in doc.files:
        conn.execute(
            "insert into trace_files(trace_id, path, ext) values (?, ?, ?)",
            (doc.trace_id, path, Path(path).suffix.lstrip(".").lower()),
        )
    for tool in doc.tools:
        conn.execute(
            "insert into trace_tools(trace_id, tool_name) values (?, ?)",
            (doc.trace_id, tool),
        )
    for skill in doc.skills:
        conn.execute(
            "insert into trace_skills(trace_id, skill_name) values (?, ?)",
            (doc.trace_id, skill),
        )
    for invocation in doc.skill_invocations:
        conn.execute(
            """
            insert into skill_invocations(
                trace_id, unit_id, skill_name, project_slug, agent_name, source,
                confidence, timestamp_start, timestamp_end, step_index,
                tool_call_id, command_name
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc.trace_id,
                invocation.get("unit_id") or "",
                invocation.get("skill_name") or "",
                doc.project_slug,
                invocation.get("agent_name") or "unknown",
                invocation.get("source") or "unknown",
                invocation.get("confidence") or "medium",
                invocation.get("timestamp_start"),
                invocation.get("timestamp_end"),
                invocation.get("step_index"),
                invocation.get("tool_call_id"),
                invocation.get("command_name"),
            ),
        )
    for signal in doc.signals:
        if signal.value:
            conn.execute(
                "insert into trace_signals(trace_id, signal_name) values (?, ?)",
                (doc.trace_id, signal.name),
            )
    for concept in semantic_profile_from_facets(doc.facets).get("concepts") or []:
        conn.execute(
            "insert into trace_concepts(trace_id, concept_id, label) values (?, ?, ?)",
            (doc.trace_id, concept.get("concept_id"), concept.get("name") or ""),
        )


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path), safe='/')}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _verify_snapshot(conn: sqlite3.Connection, path: Path) -> None:
    try:
        row = conn.execute(
            "select value from snapshot_meta where key = 'schema_version'"
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        raise SearchSnapshotNeedsRebuild("unreadable", path=path) from exc
    if row is None:
        raise SearchSnapshotNeedsRebuild("missing_schema", path=path)
    if str(row["value"]) != SNAPSHOT_SCHEMA_VERSION:
        raise SearchSnapshotNeedsRebuild("wrong_schema", path=path)
    if current_dirty_token() is not None:
        raise SearchSnapshotNeedsRebuild("stale", path=path)


def _query_rows(
    conn: sqlite3.Connection,
    *,
    terms: list[str],
    semantic_ids: set[str],
    filters: SearchFilters,
    limit: int,
    offset: int,
) -> tuple[list[sqlite3.Row], int]:
    params: list[Any] = []
    where: list[str] = []
    if terms:
        from_clause = "trace_fts join traces on traces.rowid = trace_fts.rowid"
        score_expr = "bm25(trace_fts)"
        where.append("trace_fts match ?")
        params.append(_fts_query(terms))
    else:
        from_clause = "traces"
        score_expr = "0.0"

    if filters.latest_generation:
        where.append("traces.latest_generation = 1")
    if filters.project:
        where.append("traces.project_slug = ?")
        params.append(filters.project)
    if filters.since:
        where.append("coalesce(traces.timestamp_end, traces.timestamp_start, '') >= ?")
        params.append(_since_iso(filters.since))
    if filters.skill:
        where.append(_exists("trace_skills", "skill_name"))
        params.append(filters.skill)
    if filters.tool:
        where.append(_exists("trace_tools", "tool_name"))
        params.append(filters.tool)
    if filters.files:
        where.append(
            "exists (select 1 from trace_files tf where tf.trace_id = traces.trace_id and tf.path glob ?)"
        )
        params.append(filters.files)
    if filters.file_kind:
        where.append(
            "exists (select 1 from trace_files tf where tf.trace_id = traces.trace_id and tf.ext = ?)"
        )
        params.append(filters.file_kind.lstrip(".").lower())
    if filters.file_op:
        where.append(_facet_exists())
        params.extend(["file.operation", _norm(filters.file_op)])
    if filters.signal:
        where.append(
            "exists (select 1 from trace_signals ts where ts.trace_id = traces.trace_id and ts.signal_name = ?)"
        )
        params.append(filters.signal)
    for name, value in _parse_key_value_filters(filters.facet_filters):
        where.append(_facet_exists())
        params.extend([name, _norm(value)])
    for name, value in _named_filter_pairs(filters):
        where.append(_facet_exists())
        params.extend([name, _norm(value)])
    if filters.success is not None:
        where.append(_facet_exists())
        params.extend(["outcome.success", _norm(str(filters.success))])
    elif filters.success_unknown:
        where.append(_bool_facet_unknown())
        params.append("outcome.success")
    if filters.committed is not None:
        where.append(_facet_exists())
        params.extend(["outcome.committed", _norm(str(filters.committed))])
    elif filters.committed_unknown:
        where.append(_bool_facet_unknown())
        params.append("outcome.committed")
    if filters.candidate_kind:
        if filters.candidate_kind == "trace":
            where.append("traces.candidate_kind = 'trace'")
        elif filters.candidate_kind == "bug_fix":
            where.append("traces.candidate_kind = 'bug_fix'")
        else:
            where.append("0")
    if semantic_ids:
        placeholders = ",".join("?" for _ in semantic_ids)
        where.append(
            f"exists (select 1 from trace_concepts tc where tc.trace_id = traces.trace_id and tc.concept_id in ({placeholders}))"
        )
        params.extend(sorted(semantic_ids))

    where_sql = " and ".join(where) if where else "1"
    # Dedup partition (issue #27 D): default latest-only queries collapse equal
    # titles to the single newest generation. When the caller asks to include
    # superseded generations (``latest_generation=False``), fold generation into
    # both the partition and the distinct-count grouping so an older generation
    # that shares its title with the surviving one is not swallowed by the title
    # dedup before it can surface.
    if filters.latest_generation:
        partition_sql = "search_group_key"
        group_cols_sql = "search_group_key"
    else:
        partition_sql = "search_group_key, latest_generation"
        group_cols_sql = "search_group_key, latest_generation"
    if terms:
        if filters.sort_order == "time":
            order_sql = "coalesce(timestamp_end, timestamp_start, '') asc, trace_id"
        elif filters.sort_order == "recency":
            order_sql = "coalesce(timestamp_end, timestamp_start, '') desc, trace_id"
        else:
            order_sql = "fts_score asc, coalesce(timestamp_end, timestamp_start, '') desc, trace_id"
    else:
        if filters.sort_order == "time":
            order_sql = "coalesce(timestamp_end, timestamp_start, '') asc, trace_id"
        else:
            order_sql = "coalesce(timestamp_end, timestamp_start, '') desc, trace_id"
    count_sql = f"""
        with matched_raw as (
            select
                traces.search_group_key,
                traces.latest_generation
            from {from_clause}
            where {where_sql}
        )
        select count(*) from (
            select {group_cols_sql} from matched_raw group by {group_cols_sql}
        )
    """
    total = int(conn.execute(count_sql, params).fetchone()[0])
    sql = f"""
        with matched_raw as (
            select
                traces.*,
                {score_expr} as fts_score
            from {from_clause}
            where {where_sql}
        ),
        ranked as (
            select
                *,
                row_number() over (
                    partition by {partition_sql}
                    order by {order_sql}
                ) as search_group_rank
            from matched_raw
        )
        select * from ranked
        where search_group_rank = 1
        order by {order_sql}
        limit ? offset ?
    """
    row_params = [*params, limit, offset]
    return list(conn.execute(sql, row_params)), total


def _query_skill_usage(
    conn: sqlite3.Connection,
    *,
    filters: SearchFilters,
    limit: int,
    offset: int,
) -> tuple[list[SkillUsage], int, int]:
    params: list[Any] = []
    where: list[str] = []
    if filters.latest_generation:
        where.append("traces.latest_generation = 1")
    if filters.project:
        where.append("traces.project_slug = ?")
        params.append(filters.project)
    if filters.since:
        where.append(
            "coalesce(si.timestamp_end, si.timestamp_start, "
            "traces.timestamp_end, traces.timestamp_start, '') >= ?"
        )
        params.append(_since_iso(filters.since))
    if filters.skill:
        where.append("si.skill_name = ?")
        params.append(filters.skill)
    where_sql = " and ".join(where) if where else "1"
    from_sql = "skill_invocations si join traces on traces.trace_id = si.trace_id"

    totals = conn.execute(
        f"""
        select count(distinct si.skill_name) as total_skills,
               count(*) as total_invocations
        from {from_sql}
        where {where_sql}
        """,
        params,
    ).fetchone()
    total_skills = int(totals["total_skills"] or 0)
    total_invocations = int(totals["total_invocations"] or 0)

    rows = list(
        conn.execute(
            f"""
            select
                si.skill_name,
                count(*) as invocation_count,
                count(distinct si.trace_id) as trace_count,
                nullif(
                    max(coalesce(si.timestamp_end, si.timestamp_start,
                                 traces.timestamp_end, traces.timestamp_start, '')),
                    ''
                ) as latest_invocation_at
            from {from_sql}
            where {where_sql}
            group by si.skill_name
            order by invocation_count desc, trace_count desc, si.skill_name asc
            limit ? offset ?
            """,
            [*params, limit, offset],
        )
    )
    skill_names = [str(row["skill_name"]) for row in rows]
    agents = _skill_usage_breakdown(conn, "agent_name", where_sql, params, skill_names)
    sources = _skill_usage_breakdown(conn, "source", where_sql, params, skill_names)
    projects = _skill_usage_breakdown(conn, "project_slug", where_sql, params, skill_names)
    skills = [
        SkillUsage(
            skill_name=str(row["skill_name"]),
            invocation_count=int(row["invocation_count"] or 0),
            trace_count=int(row["trace_count"] or 0),
            agents=agents.get(str(row["skill_name"]), {}),
            sources=sources.get(str(row["skill_name"]), {}),
            projects=projects.get(str(row["skill_name"]), {}),
            latest_invocation_at=row["latest_invocation_at"],
        )
        for row in rows
    ]
    return skills, total_skills, total_invocations


def _skill_usage_breakdown(
    conn: sqlite3.Connection,
    column: str,
    where_sql: str,
    params: list[Any],
    skill_names: list[str],
) -> dict[str, dict[str, int]]:
    if not skill_names:
        return {}
    if column not in {"agent_name", "source", "project_slug"}:
        raise ValueError(f"unsupported skill usage column: {column}")
    placeholders = ",".join("?" for _ in skill_names)
    from_sql = "skill_invocations si join traces on traces.trace_id = si.trace_id"
    rows = conn.execute(
        f"""
        select si.skill_name, si.{column} as value, count(*) as n
        from {from_sql}
        where {where_sql} and si.skill_name in ({placeholders})
        group by si.skill_name, si.{column}
        order by si.skill_name asc, n desc, value asc
        """,
        [*params, *skill_names],
    )
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        skill_name = str(row["skill_name"])
        value = str(row["value"] or "unknown")
        out.setdefault(skill_name, {})[value] = int(row["n"] or 0)
    return out


def _query_skill_invocation_units(
    conn: sqlite3.Connection,
    *,
    skill: str,
    project: str | None,
    limit: int | None,
) -> list[TraceUnit]:
    where = ["si.skill_name = ?", "traces.latest_generation = 1"]
    params: list[Any] = [skill]
    if project:
        where.append("si.project_slug = ?")
        params.append(project)
    limit_sql = ""
    if limit is not None and int(limit) > 0:
        limit_sql = "limit ?"
        params.append(int(limit))
    rows = conn.execute(
        f"""
        select
            si.trace_id,
            si.unit_id,
            si.skill_name,
            si.project_slug,
            si.agent_name,
            si.source,
            si.confidence,
            si.timestamp_start,
            si.timestamp_end,
            si.step_index,
            si.tool_call_id,
            si.command_name,
            traces.title,
            traces.summary,
            traces.files_json
        from skill_invocations si
        join traces on traces.trace_id = si.trace_id
        where {" and ".join(where)}
        order by
            coalesce(si.timestamp_end, si.timestamp_start,
                     traces.timestamp_end, traces.timestamp_start, '') desc,
            si.trace_id asc,
            si.unit_id asc
        {limit_sql}
        """,
        params,
    )
    return [_skill_invocation_unit_from_row(row) for row in rows]


def _skill_invocation_unit_from_row(row: sqlite3.Row) -> TraceUnit:
    confidence = str(row["confidence"] or "medium")
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    step_index = row["step_index"]
    metadata: dict[str, Any] = {
        "skill_name": row["skill_name"],
        "source": row["source"] or "unknown",
        "snapshot_source": "trace_search_snapshot.skill_invocations",
    }
    if isinstance(step_index, int):
        metadata["step_index"] = step_index
    if row["tool_call_id"]:
        metadata["tool_call_id"] = row["tool_call_id"]
    if row["command_name"]:
        metadata["command_name"] = row["command_name"]
    agent_name = str(row["agent_name"] or "unknown")
    skill_name = str(row["skill_name"] or "")
    trace_id = str(row["trace_id"])
    title = str(row["title"] or trace_id)
    return TraceUnit(
        unit_id=str(row["unit_id"] or f"tu:{trace_id}:skill:{skill_name}"),
        unit_type="skill_invocation",
        trace_id=trace_id,
        project_slug=str(row["project_slug"] or "unknown"),
        files=[str(item) for item in _json_list(row["files_json"])],
        skills=[skill_name] if skill_name else [],
        title_text=f"{skill_name} invocation in {title}" if skill_name else title,
        intent_text=str(row["summary"] or title),
        action_text=str(row["source"] or "skill_invocation"),
        evidence_text=str(row["command_name"] or row["tool_call_id"] or ""),
        facets=[
            TraceFacet(
                name="agent.name",
                value=agent_name,
                source="exact_schema",
                confidence="high",
            )
        ],
        signals=[
            TraceSignal(
                name="skill_invoked",
                value=True,
                confidence=confidence,  # type: ignore[arg-type]
                metadata={
                    "source": row["source"] or "unknown",
                    "command_name": row["command_name"],
                    "tool_call_id": row["tool_call_id"],
                },
            )
        ],
        metadata=metadata,
    )


def _hit_from_row(row: sqlite3.Row, terms: list[str]) -> SearchHit:
    facets = [
        TraceFacet.model_validate(item)
        for item in _json_list(row["facets_json"])
        if isinstance(item, dict)
    ]
    signals = [
        TraceSignal.model_validate(item)
        for item in _json_list(row["signals_json"])
        if isinstance(item, dict)
    ]
    score = -float(row["fts_score"] or 0.0)
    return SearchHit(
        trace_id=str(row["trace_id"]),
        project_slug=str(row["project_slug"]),
        title=str(row["title"] or row["trace_id"]),
        summary=str(row["summary"] or ""),
        score=score,
        matched_fields=_matched_fields(row, terms),
        source_path=str(row["source_path"]),
        source_hash=str(row["source_hash"]),
        timestamp_start=row["timestamp_start"],
        timestamp_end=row["timestamp_end"],
        files=[str(item) for item in _json_list(row["files_json"])],
        skills=[str(item) for item in _json_list(row["skills_json"])],
        facets=facets,
        signals=signals,
        committed=_int_to_bool(row["committed"]),
        commit_sha=row["commit_sha"],
        commit_subject=row["commit_subject"],
        provenance_color=row["provenance_color"],
        candidate_kind=str(row["candidate_kind"] or "trace"),
    )


def _matched_fields(row: sqlite3.Row, terms: list[str]) -> dict[str, list[str]]:
    if not terms:
        return {}
    out: dict[str, list[str]] = {}
    for column_name in ("title", "summary", "intent_text", "action_text", "file_text", "skill_text", "facet_text"):
        field_terms = set(_terms(str(row[column_name] or "")))
        hits = [term for term in terms if term in field_terms]
        if hits:
            out[column_name] = hits
    return out


def _slice_preview_for_hit(hit: SearchHit, *, max_slice_nodes: int) -> TraceMap | None:
    record = _load_record_from_hit(hit)
    if record is None:
        return None
    trace_map = build_trace_map(record)
    node = next(
        (
            item
            for item in trace_map.nodes
            if item.action_type in {"file_edit", "test_run", "agent_plan", "user_instruction"}
        ),
        trace_map.nodes[0] if trace_map.nodes else None,
    )
    if node is None:
        return None
    return slice_trace_map_for_candidate(trace_map, node.node_id, max_steps=max_slice_nodes)


def _load_record_from_hit(hit: SearchHit) -> Any | None:
    from . import trace_index as ti

    path = Path(hit.source_path)
    if not path.exists():
        return None
    for record in ti._iter_trace_file_records(path):
        if record.trace_id == hit.trace_id:
            return record
    return None


def _exists(table: str, column: str) -> str:
    alias = table[:2]
    return (
        f"exists (select 1 from {table} {alias} "
        f"where {alias}.trace_id = traces.trace_id and {alias}.{column} = ?)"
    )


def _facet_exists() -> str:
    # The facet name is bound as a parameter (never interpolated): SQLite
    # treats double-quoted strings as identifiers when they collide with a
    # column name, which silently captured names like ``project_slug``.
    return (
        "exists (select 1 from trace_facets tf "
        "where tf.trace_id = traces.trace_id and tf.name = ? "
        "and tf.value_norm = ?)"
    )


def _bool_facet_unknown() -> str:
    # Base-parity ``--unknown-*`` semantics: the facet is absent OR carries a
    # non-boolean value (e.g. ``outcome.committed`` is always emitted, with a
    # ``none`` value when unknown).
    return (
        "not exists (select 1 from trace_facets tf "
        "where tf.trace_id = traces.trace_id and tf.name = ? "
        "and tf.value_norm in ('true', 'false'))"
    )


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


def _named_filter_pairs(filters: SearchFilters) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for name, value in (
        ("provider.kind", filters.provider),
        ("bash.command_family", filters.cmd_family),
        ("bash.action", filters.bash_action),
        ("test.framework", filters.test_framework),
        ("service.name", filters.service),
        ("service.channel", filters.service_channel),
        ("dependency.name", filters.dependency),
        ("git_link_tier", filters.git_tier),
        ("survival.state", filters.survival),
    ):
        if value:
            out.append((name, value))
    return out


def _record_files(record: Any) -> list[str]:
    paths_seen: dict[str, None] = {}
    for step in record.steps:
        for call in step.tool_calls:
            for value in _file_values_from_mapping(call.input):
                normalized = _normalize_file_path(value)
                if normalized:
                    paths_seen.setdefault(normalized, None)
    for link in getattr(record, "git_links", []) or []:
        data = link.model_dump(mode="json") if hasattr(link, "model_dump") else {}
        for key in ("file_path", "path", "old_path", "new_path"):
            normalized = _normalize_file_path(data.get(key))
            if normalized:
                paths_seen.setdefault(normalized, None)
    return list(paths_seen)


def _file_values_from_mapping(value: Any) -> list[Any]:
    if not isinstance(value, dict):
        return []
    values: list[Any] = []
    for key in (
        "file",
        "file_path",
        "filepath",
        "path",
        "notebook_path",
        "old_path",
        "new_path",
        "target_file",
    ):
        if key in value:
            values.append(value[key])
    for key in ("files", "file_paths", "paths"):
        item = value.get(key)
        if isinstance(item, list):
            values.extend(item)
    return values


def _normalize_file_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or "\n" in text:
        return None
    if len(text) > 500:
        return None
    if text.startswith(("http://", "https://", "ot://")):
        return None
    return text


def _record_signals(record: Any, *, skills: list[str]) -> list[TraceSignal]:
    from . import trace_index as ti

    observations = " ".join(ti._observation_summaries(record)).lower()
    failed = any(word in observations for word in ("failed", "failures", "error"))
    passed = "passed" in observations or "success" in observations
    test_commands = ti._test_commands(record)
    edited_refs: list[str] = []
    test_refs: list[str] = []
    for step in record.steps:
        for call in step.tool_calls:
            normalized = call.tool_name.lower().replace("_", "").replace("-", "")
            ref = f"tu:{record.trace_id}:tool:{call.tool_call_id}"
            if normalized in {"edit", "write", "multiedit"}:
                edited_refs.append(ref)
            command = str(call.input.get("command") or "")
            if command and command in test_commands:
                test_refs.append(ref)

    signals: list[TraceSignal] = []

    def add(
        name: str,
        value: bool,
        *,
        refs: list[str] | None = None,
        confidence: str = "high",
    ) -> None:
        if value:
            signals.append(
                TraceSignal(
                    name=name,
                    value=True,
                    confidence=confidence,  # type: ignore[arg-type]
                    evidence_refs=(refs or [])[:8],
                )
            )

    edited = bool(edited_refs)
    test_seen = bool(test_commands)
    add("test_command_seen", test_seen, refs=test_refs)
    add("test_failed_seen", failed, refs=test_refs)
    add("test_passed_seen", passed, refs=test_refs)
    add("patch_or_edit_seen", edited, refs=edited_refs)
    add("test_passed_after_edit_seen", test_seen and passed and edited, refs=test_refs + edited_refs)
    add("test_failed_then_passed_seen", failed and passed, refs=test_refs)
    add("outcome_success", record.outcome.success is True)
    add("outcome_committed", record.outcome.committed is True)
    add(
        "tested_successful_fix_candidate",
        test_seen and failed and passed and edited and record.outcome.success is True,
        refs=test_refs + edited_refs,
    )
    if skills:
        signals.append(
            TraceSignal(
                name="skill_invoked",
                value=skills,
                confidence="high",
                evidence_refs=[],
            )
        )
    return signals


def _candidate_kind_from_signals(signals: list[TraceSignal]) -> str:
    if any(signal.name == "tested_successful_fix_candidate" and signal.value for signal in signals):
        return "bug_fix"
    return "trace"


def _source_hash(docs: list[_SearchDocument]) -> str:
    material = "\n".join(
        f"{doc.trace_id}\t{doc.source_hash}\t{doc.latest_generation}"
        for doc in sorted(docs, key=lambda item: item.trace_id)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _record_hash(record: Any, trace_path: Path) -> str:
    material = f"{trace_path}\n{record.model_dump_json()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _limit_text(value: object, limit: int) -> str:
    text = " ".join(redact_index_text(value).split())
    return text[: limit - 3] + "..." if len(text) > limit else text


def _preview(text: str, *, limit: int = 240) -> str:
    compact = " ".join(redact_index_text(text).split())
    return compact[: limit - 3] + "..." if len(compact) > limit else compact


def _match_explanation(matched_fields: dict[str, list[str]], hit: SearchHit) -> str:
    parts = [f"{field} matched {', '.join(terms)}" for field, terms in matched_fields.items()]
    if hit.skills:
        parts.append(f"skill.name matched {', '.join(hit.skills)}")
    return "; ".join(parts) or "metadata filter matched"


def _visible_files(files: list[str]) -> list[str]:
    return files[:VISIBLE_FILE_LIMIT]


def _visible_facets(facets: list[TraceFacet]) -> list[TraceFacet]:
    return facets[:VISIBLE_FACET_LIMIT]


def _visible_signals(signals: list[TraceSignal]) -> list[TraceSignal]:
    return signals[:VISIBLE_SIGNAL_LIMIT]


def _search_group_key(value: object) -> str:
    raw = str(value or "")
    ascii_key = " ".join(re.sub(r"[^a-z0-9]+", " ", raw.lower()).split())[:240]
    if ascii_key:
        return ascii_key
    # Non-ASCII titles (CJK / Cyrillic / emoji-only) survive ASCII stripping as
    # empty, which would otherwise collapse every distinct non-ASCII title into
    # one dedup group ("__empty__") and let the row_number() partition return
    # only one of them per query (issue #27 B). Fall back to a stable hash of the
    # NFKC-casefolded title so distinct non-ASCII titles get distinct groups
    # while true duplicates (identical normalized text) still collapse together.
    normalized = unicodedata.normalize("NFKC", raw).casefold().strip()
    if not normalized:
        return "__empty__"
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:24]
    return f"__h:{digest}"


def _superseded_trace_ids(record) -> tuple[str, ...]:
    """Inverse supersession pointer: trace ids this record replaces.

    Mirrors the legacy index (``trace_index._latest_units``) which honours
    ``metadata.superseded_trace_ids`` in addition to the forward
    ``superseded_by`` marker. The snapshot's ``latest_generation`` column only
    consulted the forward pointer, so writers that emit only the inverse pointer
    left the older generation flagged latest (issue #27 D).
    """

    replaced = record.metadata.get("superseded_trace_ids") or []
    if not isinstance(replaced, list):
        return ()
    return tuple(str(tid) for tid in replaced if tid)


def _normalize_ts(value: object) -> str | None:
    """Normalize a record timestamp to UTC Z-form for lexicographic compare.

    Stored timestamps must share a canonical UTC representation with the
    ``--since`` bound (which ``_since_iso`` emits as ``...+00:00`` -> ``...Z``),
    otherwise an offset-bearing stamp like ``10:00:00+02:00`` (== ``08:00:00Z``)
    sorts after ``09:00:00Z`` as a raw string and the ``coalesce(...) >= ?``
    where-clause filters it on the wrong side of the boundary (issue #27 C).
    A value we cannot parse is preserved verbatim (best-effort, never lossy).
    """

    if value in (None, ""):
        return None
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _since_iso(value: str) -> str:
    from .trace_index import _parse_since

    return _parse_since(value).isoformat().replace("+00:00", "Z")


def _bool_to_int(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def _int_to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(int(value))


def _str_or_none(value: object) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _norm(value: str) -> str:
    return str(value).lower()


def _json_list(raw: Any) -> list[Any]:
    if not raw:
        return []
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _unlink_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        try:
            path.with_name(path.name + suffix).unlink()
        except OSError:
            pass

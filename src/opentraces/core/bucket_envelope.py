"""Bucket per-trace I/O — trace records, raw sources, per-trace envelopes.

Extracted from ``bucket_store`` (god-module decomposition) as the LOWER layer of
the bucket core: reads/writes per-trace bucket content — trace-record objects +
pointers, raw-source artifacts, the v2 per-trace envelope and its framed
companions, and the v2 ``traces[]`` summary rows. The dependency points DOWN:
this module imports only the bucket primitives (``bucket_models`` /
``bucket_layout`` / ``_bucket_io`` / ``bucket_events`` / ``bucket_context_store``),
``paths``, the schema package and stdlib — NOTHING from ``bucket_store`` (the AST
cycle-check found no real envelope->manifest calls). The manifest/sync layer that
stays in ``bucket_store`` imports these from here, and ``bucket_store`` re-exports
them so ``from ...bucket_store import write_trace_record`` call sites are unchanged.
"""

from __future__ import annotations

import gzip
import json
from opentraces.core._time import utc_now_str
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from pydantic import ValidationError

from opentraces_schema import TraceRecord, load_record_json

from ..security.privacy import (
    bucket_security_state,
)
from . import paths
from .bucket_layout import (
    _digest_hex,
    _path_part,
    legacy_trace_records_root,
    raw_sources_root,
    trace_records_root,
    trace_v1_context_path,
    trace_v1_json_path,
    trace_v1_sources_path,
    trace_v1_trail_path,
    traces_v1_dir,
    traces_v1_root,
)

# ---------------------------------------------------------------------------
# Re-export I/O utilities from the extracted _bucket_io module.
# All internal callers within this file still work; external callers that
# do ``from opentraces.core.bucket_store import _atomic_write_json`` etc.
# continue to resolve.
# ---------------------------------------------------------------------------
from ._bucket_io import (
    _atomic_write_bytes,
    _atomic_write_gzip,
    _atomic_write_json,
    _canonical_json,
    _digest_bytes,
    _digest_payload,
    _read_gzip_bytes,
)

# ---------------------------------------------------------------------------
# Re-export trail/events-mirror cluster from bucket_events.
# ---------------------------------------------------------------------------
from .bucket_events import (
    read_events_mirror_batches,
)

# ---------------------------------------------------------------------------
# Re-export Context Tree bucket projection cluster from bucket_context_store.
# ---------------------------------------------------------------------------


# Bucket data models + schema-version constants live in the dependency-free base
# module ``bucket_models``; re-exported here so ``from ...bucket_store import
# BucketTraceRecord`` / ``BUCKET_MANIFEST_SCHEMA`` (and the rest) keep working.
# Imported eagerly: ``bucket_models`` imports nothing from this package, so there
# is no cycle, and these names are used throughout the manifest code below.
from .bucket_models import (
    BUCKET_PER_TRACE_SCHEMA,
    BucketLayoutError,
    BucketTraceRecord,
    BucketTraceRecordPointer,
    RAW_SOURCE_SCHEMA,
    RAW_SOURCE_SNAPSHOT_SCHEMA,
    TRACE_RECORD_BUCKET_SCHEMA,
    TRACE_RECORD_POINTER_SCHEMA,
    TRACE_RECORD_PROJECT_STAGING,
)


def trace_record_path(
    project_slug: str,
    trace_id: str,
) -> Path:
    """Return the v2 current pointer path for a normalized TraceRecord."""

    return trace_records_root() / _path_part(project_slug) / _path_part(trace_id) / "current.json"


def trace_record_object_path(
    project_slug: str,
    trace_id: str,
    record_hash: str,
) -> Path:
    """Return the content-addressed v2 TraceRecord object path."""

    digest = _digest_hex(record_hash)
    return (
        trace_records_root()
        / _path_part(project_slug)
        / _path_part(trace_id)
        / f"{digest}.json"
    )


def write_trace_record(
    record: TraceRecord,
    *,
    project_slug: str,
    source_layer: str,
    legacy_mirror: bool = True,
    privacy_tier: str | None = None,
) -> BucketTraceRecord:
    """Write ``record`` to the local bucket as an immutable-style envelope."""

    normalized = _normalized_record(record)
    record_payload = normalized.model_dump(mode="json")
    record_hash = _digest_payload(record_payload)
    security_state = bucket_security_state(normalized, privacy_tier=privacy_tier)
    object_path = trace_record_object_path(project_slug, normalized.trace_id, record_hash)
    relative_object_path = object_path.relative_to(paths.bucket_dir()).as_posix()
    envelope = {
        "schema_version": TRACE_RECORD_BUCKET_SCHEMA,
        "project_slug": project_slug,
        "source_layer": source_layer,
        "trace_id": normalized.trace_id,
        "record_hash": record_hash,
        "legacy_mirror": legacy_mirror,
        "security": security_state,
        "written_at": utc_now_str(),
        "record": record_payload,
    }
    _atomic_write_json(object_path, envelope)
    _atomic_write_json(
        trace_record_path(project_slug, normalized.trace_id),
        {
            "schema_version": TRACE_RECORD_POINTER_SCHEMA,
            "project_slug": project_slug,
            "source_layer": source_layer,
            "trace_id": normalized.trace_id,
            "record_hash": record_hash,
            "object_path": relative_object_path,
            "updated_at": envelope["written_at"],
        },
    )
    try:
        from .trace_search_state import mark_search_snapshot_dirty

        mark_search_snapshot_dirty("trace_record_write", trace_id=normalized.trace_id)
    except Exception:
        pass
    return BucketTraceRecord(
        path=object_path,
        project_slug=project_slug,
        source_layer=source_layer,
        trace_id=normalized.trace_id,
        record_hash=record_hash,
        record=normalized,
        envelope=envelope,
    )


def read_trace_record_object(path: Path) -> BucketTraceRecord | None:
    """Read one bucket TraceRecord envelope, returning ``None`` on invalid data."""

    try:
        path = _resolve_trace_record_pointer(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("schema_version") != TRACE_RECORD_BUCKET_SCHEMA:
            return None
        record = TraceRecord.model_validate(raw.get("record") or {})
        project_slug = str(raw.get("project_slug") or "")
        source_layer = str(raw.get("source_layer") or "")
        trace_id = record.trace_id
        record_hash = str(raw.get("record_hash") or _digest_payload(record.model_dump(mode="json")))
        if not project_slug or not source_layer or not trace_id:
            return None
        raw_security = raw.get("security") if isinstance(raw.get("security"), dict) else {}
        raw["security"] = bucket_security_state(
            record,
            privacy_tier=raw_security.get("privacy_tier"),
        )
    except (OSError, ValueError, TypeError, ValidationError):
        return None
    return BucketTraceRecord(
        path=path,
        project_slug=project_slug,
        source_layer=source_layer,
        trace_id=trace_id,
        record_hash=record_hash,
        record=record,
        envelope=raw,
    )


def iter_trace_record_objects(
    project_slug: str | None = None,
) -> list[BucketTraceRecord]:
    """Return valid TraceRecord envelopes in the local bucket.

    When ``project_slug`` is given, only that project's records are
    globbed and parsed — on a shared bucket this avoids reading and
    validating every other project's JSON.
    """

    out: list[BucketTraceRecord] = []
    seen: set[tuple[str, str]] = set()
    root = trace_records_root()
    glob_prefix = f"{_path_part(project_slug)}/*" if project_slug else "*/*"
    if root.exists():
        for path in sorted(root.glob(f"{glob_prefix}/current.json")):
            obj = read_trace_record_object(path)
            if obj is not None:
                seen.add((obj.project_slug, obj.trace_id))
                out.append(obj)
    legacy_root = legacy_trace_records_root()
    if legacy_root.exists():
        for path in sorted(legacy_root.glob(f"{glob_prefix}.json")):
            obj = read_trace_record_object(path)
            if obj is None or (obj.project_slug, obj.trace_id) in seen:
                continue
            out.append(obj)
    return out


def read_bucket_record_for_trace(trace_id: str) -> BucketTraceRecord | None:
    """Resolve one trace by trace_id from the durable read sources, or ``None``.

    Single-trace hydration path that ``trace map/get/slice`` use instead of the
    deprecated legacy Trace Index (issue #89). Delegates to the canonical
    :mod:`trace_corpus` resolver so it reads exactly the same union, and applies
    the same precedence, as the search-snapshot rebuild and the legacy index
    refresh — no second copy of the union logic lives here.
    """

    if not trace_id:
        return None
    from .trace_corpus import load_record, resolve

    source = resolve(trace_id)
    if source is None:
        return None
    return load_record(source)


def project_store_record_from_path(
    path: Path,
    *,
    trace_id: str,
    project_slug: str,
    source_layer: str,
) -> BucketTraceRecord | None:
    """Hydrate the latest record matching ``trace_id`` from one JSONL shard.

    Serves traces that live only in ``projects/<slug>/traces/<id>.jsonl`` (or
    staging) and have not been mirrored into the bucket. The last matching
    record in a shard is the latest generation. The canonical
    :mod:`trace_corpus` resolver calls this for its project/staging layers.
    """

    records = [r for r in _read_jsonl_trace_records(path) if r.trace_id == trace_id]
    if not records:
        return None
    return _project_store_bucket_record(records[-1], path, project_slug, source_layer)


def _project_store_bucket_record(
    record: TraceRecord,
    path: Path,
    project_slug: str,
    source_layer: str,
) -> BucketTraceRecord:
    normalized = _normalized_record(record)
    record_hash = _digest_payload(normalized.model_dump(mode="json"))
    return BucketTraceRecord(
        path=path,
        project_slug=project_slug,
        source_layer=source_layer,
        trace_id=record.trace_id,
        record_hash=record_hash,
        record=record,
        envelope={"legacy_mirror": False, "security": bucket_security_state(record)},
    )


def iter_trace_record_pointers(
    project_slug: str | None = None,
) -> list[BucketTraceRecordPointer]:
    """Return TraceRecord bucket pointers without validating full records."""

    out: list[BucketTraceRecordPointer] = []
    seen: set[tuple[str, str]] = set()
    root = trace_records_root()
    glob_prefix = f"{_path_part(project_slug)}/*" if project_slug else "*/*"
    if root.exists():
        for path in sorted(root.glob(f"{glob_prefix}/current.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if raw.get("schema_version") != TRACE_RECORD_POINTER_SCHEMA:
                continue
            object_path = raw.get("object_path")
            trace_id = str(raw.get("trace_id") or "")
            project = str(raw.get("project_slug") or "")
            source_layer = str(raw.get("source_layer") or "")
            record_hash = str(raw.get("record_hash") or "")
            if (
                not isinstance(object_path, str)
                or not object_path
                or not trace_id
                or not project
                or not source_layer
                or not record_hash
            ):
                continue
            resolved = paths.bucket_dir() / object_path
            if not resolved.exists():
                continue
            seen.add((project, trace_id))
            out.append(
                BucketTraceRecordPointer(
                    path=resolved,
                    project_slug=project,
                    source_layer=source_layer,
                    trace_id=trace_id,
                    record_hash=record_hash,
                )
            )

    legacy_root = legacy_trace_records_root()
    if legacy_root.exists():
        for path in sorted(legacy_root.glob(f"{glob_prefix}.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if raw.get("schema_version") != TRACE_RECORD_BUCKET_SCHEMA:
                continue
            trace_id = str(raw.get("trace_id") or "")
            project = str(raw.get("project_slug") or "")
            source_layer = str(raw.get("source_layer") or "")
            record_hash = str(raw.get("record_hash") or "")
            if not trace_id or not project or not source_layer or not record_hash:
                continue
            if (project, trace_id) in seen:
                continue
            out.append(
                BucketTraceRecordPointer(
                    path=path,
                    project_slug=project,
                    source_layer=source_layer,
                    trace_id=trace_id,
                    record_hash=record_hash,
                )
            )
    return out


def trace_record_snapshot(
    *,
    include_objects: bool = False,
) -> dict[str, Any]:
    """Return a deterministic snapshot descriptor for bucket TraceRecords."""

    root = trace_records_root()
    objects = []
    for obj in iter_trace_record_objects():
        try:
            relative_path = obj.path.relative_to(root).as_posix()
        except ValueError:
            relative_path = obj.path.relative_to(paths.bucket_dir()).as_posix()
        objects.append(
            {
                "path": relative_path,
                "trace_id": obj.trace_id,
                "project_slug": obj.project_slug,
                "source_layer": obj.source_layer,
                "record_hash": obj.record_hash,
                "privacy_tier": obj.envelope.get("security", {}).get("privacy_tier"),
                "security_version": obj.envelope.get("security", {}).get("security_version"),
                "syncable": obj.envelope.get("security", {}).get("syncable"),
                "security_stale": obj.envelope.get("security", {}).get("stale"),
                "written_at": obj.envelope.get("written_at"),
            }
        )
    digest_material = [
        (
            f"{item['path']} {item['record_hash']} "
            f"{item.get('privacy_tier')} {item.get('security_version')} "
            f"{item.get('syncable')} {item.get('security_stale')}"
        )
        for item in sorted(objects, key=lambda item: item["path"])
    ]
    snapshot: dict[str, Any] = {
        "schema_version": "opentraces.bucket.trace_records_snapshot.v1",
        "root": str(root),
        "object_count": len(objects),
        "digest": _digest_payload(digest_material),
    }
    if include_objects:
        snapshot["objects"] = sorted(objects, key=lambda item: item["path"])
    return snapshot


def write_raw_source_artifact(
    source_path: Path,
    *,
    trace_id: str,
    project_slug: str,
    source_kind: str,
    parser: str,
) -> dict[str, Any]:
    """Store an optional raw source artifact in the portable bucket.

    Raw artifacts are private-bucket syncable but not dataset-publishable. They
    preserve future re-parse capability without promoting raw inputs into the
    public dataset surface.
    """

    data = source_path.read_bytes()
    digest = _digest_bytes(data)
    digest_hex = _digest_hex(digest)
    suffix = source_path.suffix or ".blob"
    blob_path = raw_sources_root() / "blobs" / digest_hex[:2] / f"{digest_hex}{suffix}"
    _atomic_write_bytes(blob_path, data)
    link = {
        "schema_version": RAW_SOURCE_SCHEMA,
        "project_slug": project_slug,
        "trace_id": trace_id,
        "source_kind": source_kind,
        "parser": parser,
        "content_digest": digest,
        "content_length": len(data),
        "blob_path": blob_path.relative_to(paths.bucket_dir()).as_posix(),
        "source_basename": source_path.name,
        "written_at": utc_now_str(),
        "remote_sync": {
            "eligible": True,
            "scope": "private_bucket_only",
            "publishable": False,
            "blocked_reasons": [],
        },
    }
    link_path = raw_sources_root() / "sources" / _path_part(project_slug) / f"{_path_part(trace_id)}.json"
    _atomic_write_json(link_path, link)
    return link


def raw_source_snapshot(*, include_objects: bool = False) -> dict[str, Any]:
    """Return a deterministic snapshot of raw source artifacts."""

    root = raw_sources_root()
    sources: list[dict[str, Any]] = []
    sources_root = root / "sources"
    if sources_root.exists():
        for path in sorted(sources_root.glob("*/*.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if item.get("schema_version") != RAW_SOURCE_SCHEMA:
                continue
            sources.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "project_slug": item.get("project_slug"),
                    "trace_id": item.get("trace_id"),
                    "source_kind": item.get("source_kind"),
                    "parser": item.get("parser"),
                    "content_digest": item.get("content_digest"),
                    "content_length": item.get("content_length"),
                    "remote_syncable": bool((item.get("remote_sync") or {}).get("eligible")),
                    "written_at": item.get("written_at"),
                    **({"object": item} if include_objects else {}),
                }
            )
    digest_material = [
        (
            f"{item.get('path')} {item.get('content_digest')} "
            f"{item.get('content_length')} {item.get('remote_syncable')}"
        )
        for item in sorted(sources, key=lambda item: str(item.get("path")))
    ]
    snapshot: dict[str, Any] = {
        "schema_version": RAW_SOURCE_SNAPSHOT_SCHEMA,
        "root": str(root),
        "object_count": len(sources),
        "remote_syncable_count": sum(1 for item in sources if item.get("remote_syncable")),
        "digest": _digest_payload(digest_material),
    }
    if include_objects:
        snapshot["objects"] = sources
    return snapshot


# ---------------------------------------------------------------------------
# Plan 080 — Per-trace envelope projector (Writer 2 per plan §9)
# ---------------------------------------------------------------------------


def _events_for_trace_from_iter(
    events_iter: Any, trace_id: str
) -> tuple[list[Any], list[Any]]:
    """Split an event iterable into (trail_events, context_events) for one trace.

    Shared by :func:`project_per_trace_exports` (Git event log) and the
    events-mirror fallback path (issue #28) so both sources filter identically.
    """

    from .context_tree.contract import (
        CONTEXT_COMPACTION_OBSERVED,
        CONTEXT_LAYER_CAPTURED,
        CONTEXT_NODE_OBSERVED,
        CONTEXT_TREE_RECONCILED,
    )

    _CONTEXT_EVENT_TYPES = {
        CONTEXT_LAYER_CAPTURED,
        CONTEXT_NODE_OBSERVED,
        CONTEXT_COMPACTION_OBSERVED,
        CONTEXT_TREE_RECONCILED,
    }

    # plan 090: a v2 anchor-search summary event has top-level trace_id=None (it
    # spans the traces it searched). The shared helper fans it into this trace's
    # companion when one of its per-patch results belongs here.
    from .trails.search_records import summary_search_touches_trace

    trail_events: list[Any] = []
    context_events: list[Any] = []
    for event in sorted(events_iter, key=lambda e: e.event_sequence):
        ev_trace_id = event.trace_id
        if not ev_trace_id and isinstance(event.payload, dict):
            ev_trace_id = event.payload.get("trace_id")
        if ev_trace_id != trace_id and not summary_search_touches_trace(event, trace_id):
            continue
        if event.event_type in _CONTEXT_EVENT_TYPES:
            context_events.append(event)
        else:
            trail_events.append(event)
    return trail_events, context_events


def _context_events_for_trace_readonly(
    project_slug: str, trace_id: str
) -> list[Any]:
    """Return this trace's context events from canonical data WITHOUT writing.

    Issue #55 read-only summary helper. Mirrors :func:`project_per_trace_exports`'s
    event source order — the live Git event log for the matching opted-in
    project first, then the bucket's own events mirror — so the in-memory
    ``node_count`` equals the one the healed companion would carry. Never
    touches disk under the bucket root.

    The mirror fallback condition must match the writer's EXACTLY
    (``not trail_events and not context_events``, never ``not
    context_events`` alone): a trace whose live log holds trail events but
    zero context events gets an EMPTY context companion on heal, so the
    read-only count must not borrow the mirror's context events for it —
    that would break the read-digest == post-heal-digest invariant.
    """

    from .trails import read_events

    repo: Path | None = None
    try:
        for path, slug in _iter_opted_in_projects():
            if slug == project_slug:
                repo = path
                break
    except Exception:
        repo = None

    events_iter: list[Any] = []
    if repo is not None:
        try:
            # Deliberately the FULL read, not the #65 trace-scoped one: this
            # read-only helper is called in per-trace loops (doctor / bucket
            # status over ~1K traces), where the process-level read_events
            # memo amortises ONE full read across every trace. A trace-scoped
            # read per call defeats the memo and turns the loop into
            # O(traces × full-log-walk) — observed as a wedged doctor on a
            # 874K-event repo during #65 verification.
            events_iter = list(read_events(repo, verify=False))
        except Exception:
            events_iter = []
    trail_events, context_events = _events_for_trace_from_iter(
        events_iter, trace_id
    )

    if not trail_events and not context_events:
        try:
            mirror_events = list(read_events_mirror_batches())
        except (FileNotFoundError, ValueError, BucketLayoutError):
            mirror_events = []
        except Exception:
            mirror_events = []
        if mirror_events:
            _, context_events = _events_for_trace_from_iter(mirror_events, trace_id)
    return context_events


def _write_per_trace_envelope(
    project_slug: str,
    trace_id: str,
    record: TraceRecord | None,
    trail_events: list[Any],
    context_events: list[Any],
) -> dict[str, Any]:
    """File-writing tail of :func:`project_per_trace_exports` (issue #31 step B).

    Writes the four companion files + ``trace.json`` in the load-bearing order
    (companions first, ``trace.json`` LAST as the manifest consumer signal).
    All gzipped files use ``mtime=0`` (Resolution H — deterministic). Atomic
    same-bytes writers keep this idempotent.
    """

    trace_dir = traces_v1_dir(project_slug, trace_id)
    trace_dir.mkdir(parents=True, exist_ok=True)

    # 2. trail.jsonl.gz
    trail_lines = [
        _canonical_json(event.model_dump(mode="json")) for event in trail_events
    ]
    trail_body = ("\n".join(trail_lines) + "\n").encode("utf-8") if trail_lines else b""
    _atomic_write_gzip(trace_v1_trail_path(project_slug, trace_id), trail_body)

    # 3. context.jsonl.gz
    context_lines = [
        _canonical_json(event.model_dump(mode="json")) for event in context_events
    ]
    context_body = ("\n".join(context_lines) + "\n").encode("utf-8") if context_lines else b""
    _atomic_write_gzip(trace_v1_context_path(project_slug, trace_id), context_body)

    # 4. sources.jsonl.gz — placeholder empty file when no raw source refs.
    sources_path = trace_v1_sources_path(project_slug, trace_id)
    if not sources_path.exists():
        _atomic_write_gzip(sources_path, b"")

    # 5. trace.json LAST. Look up the canonical record if not passed in.
    if record is None:
        existing = read_trace_record_object(trace_record_path(project_slug, trace_id))
        record = existing.record if existing is not None else None
    if record is not None:
        payload = record.model_dump(mode="json")
        _atomic_write_json(trace_v1_json_path(project_slug, trace_id), payload)

    return {
        "schema_version": BUCKET_PER_TRACE_SCHEMA,
        "project_slug": project_slug,
        "trace_id": trace_id,
        "trace_path": trace_v1_json_path(project_slug, trace_id)
        .relative_to(paths.bucket_dir())
        .as_posix(),
        "trail_event_count": len(trail_events),
        "context_event_count": len(context_events),
        "has_trace_record": record is not None,
        "projected_at": utc_now_str(),
    }


def project_per_trace_exports(
    repo: Path | None = None,
    *,
    project_slug: str,
    trace_id: str,
    record: TraceRecord | None = None,
    events: list[Any] | None = None,
) -> dict[str, Any]:
    """Write the per-trace envelope under ``bucket/traces/v1/<proj>/<trace>/``.

    Plan 080 §9 Writer 2 contract — order is load-bearing for partial-failure
    recovery:

    1. Filter events for this trace from the canonical Git event log.
    2. Write ``trail.jsonl.gz``  (atomic).
    3. Write ``context.jsonl.gz`` (atomic).
    4. Write ``sources.jsonl.gz`` (atomic).
    5. Write ``trace.json``       LAST (the spine; manifest consumer signal).

    The manifest at ``bucket/manifest.json`` is updated separately by
    :func:`bucket_manifest`. Callers that need a consistent snapshot should
    invoke this function for every trace BEFORE calling ``bucket_manifest``.

    Issue #28 — ``repo`` is optional. When ``repo`` is ``None`` (no live
    project on this machine — the cross-machine restore shape) OR the live
    Git event log yields no events for this trace, the bucket's OWN events
    mirror (``bucket/events/v1/``) is used as the event source so the
    envelope is still written from canonical data. This makes the bucket
    self-sufficient: ``bucket repair`` / manifest rebuild no longer drop a
    trace that exists in the bucket but has no live opted-in project.

    All gzipped files use ``mtime=0`` (Resolution H — deterministic).
    """

    from .trails.event_log import read_events_for_trace

    # 1. Filter events by trace_id (sequence order preserved). Prefer the live
    # Git event log; fall back to the bucket's own events mirror when there is
    # no repo, or the live log yields nothing for this trace.
    #
    # #65: trace-scoped read — the previous full ``read_events`` here ran per
    # ingested trace per watcher tick, materialising the whole log (~872K
    # pydantic events) plus the 2GB snapshot pickle. The raw prefilter
    # over-includes; _events_for_trace_from_iter post-filters exactly.
    # Loop callers (bucket repair / manifest rebuild over ~1K traces) MUST
    # pass ``events`` (one shared full read) instead — a trace-scoped walk
    # per loop iteration is O(traces × full-log-walk).
    events_iter: list[Any] = []
    if events is not None:
        events_iter = events
    elif repo is not None:
        try:
            events_iter = read_events_for_trace(repo, trace_id)
        except Exception:
            events_iter = []
    trail_events, context_events = _events_for_trace_from_iter(events_iter, trace_id)

    if not trail_events and not context_events:
        try:
            mirror_events = list(read_events_mirror_batches())
        except (FileNotFoundError, ValueError, BucketLayoutError):
            mirror_events = []
        except Exception:
            mirror_events = []
        if mirror_events:
            trail_events, context_events = _events_for_trace_from_iter(
                mirror_events, trace_id
            )

    return _write_per_trace_envelope(
        project_slug, trace_id, record, trail_events, context_events
    )


def _per_trace_v2_summary(
    project_slug: str,
    trace_id: str,
    record: TraceRecord | None,
    *,
    assume_envelope_present: bool = False,
) -> dict[str, Any]:
    """Compute the manifest summary block for one per-trace envelope.

    Plan 080 §4 — drives the ``traces[]`` entries in ``manifest.json``.

    Issue #55 — ``assume_envelope_present`` is the read-only reconcile mode.
    When set, the summary reflects the state the per-trace envelope WOULD have
    after a self-heal, WITHOUT touching disk: :func:`_write_per_trace_envelope`
    always writes all three companion files (framed gzip, non-zero size even
    when logically empty), so ``has_trail`` / ``has_context`` / ``has_sources``
    are all ``True`` post-heal, and ``node_count`` is read from the canonical
    context events (read-only) instead of the not-yet-written companion. This
    makes the read-only digest byte-identical to the digest a subsequent
    ``bucket repair`` / ``bucket manifest --heal`` persists.
    """

    trail_path = trace_v1_trail_path(project_slug, trace_id)
    context_path = trace_v1_context_path(project_slug, trace_id)
    sources_path = trace_v1_sources_path(project_slug, trace_id)
    trace_json = trace_v1_json_path(project_slug, trace_id)

    if assume_envelope_present:
        # Heal always materializes all three framed companions (size > 0), so
        # the post-heal disk view reports True for each regardless of content.
        has_trail = True
        has_context = True
        has_sources = True
    else:
        has_trail = trail_path.exists() and trail_path.stat().st_size > 0
        has_context = context_path.exists() and context_path.stat().st_size > 0
        has_sources = sources_path.exists() and sources_path.stat().st_size > 0

    # Summary counters from TraceRecord when available.
    step_count = 0
    patch_count = 0
    anchored_count = 0
    title: str | None = None
    lifecycle: str | None = None
    capture_methods: list[str] = []
    agent_name: str | None = None
    agent_version: str | None = None
    agent_model: str | None = None
    if record is not None:
        step_count = len(record.steps or [])
        patches = record.patches or []
        patch_count = len(patches)
        anchored_count = sum(
            1 for p in patches if p.anchor is not None and p.anchor.found
        )
        title = (record.task.description if record.task else None) or None
        lifecycle = record.lifecycle
        if record.agent is not None:
            agent_name = record.agent.name
            agent_version = record.agent.version
            agent_model = record.agent.model
        # Capture methods from context_tree_summary if present.
        if isinstance(record.context_tree_summary, dict):
            methods = record.context_tree_summary.get("capture_methods")
            if isinstance(methods, list):
                capture_methods = sorted(str(m) for m in methods if m)

    # node_count: count distinct node payloads via context_node_observed
    # events. In the default (materialized) path read the per-trace
    # context.jsonl.gz (cheap — small file). In the read-only #55 path the
    # companion is not on disk yet, so count the SAME events from canonical
    # data (read-only) — what the healed companion would contain.
    from .context_tree.contract import CONTEXT_NODE_OBSERVED

    node_count = 0
    if assume_envelope_present:
        for event in _context_events_for_trace_readonly(project_slug, trace_id):
            if getattr(event, "event_type", None) == CONTEXT_NODE_OBSERVED:
                node_count += 1
    elif has_context:
        try:
            raw = _read_gzip_bytes(context_path).decode("utf-8")
            for line in raw.splitlines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    continue
                if payload.get("event_type") == CONTEXT_NODE_OBSERVED:
                    node_count += 1
        except (OSError, gzip.BadGzipFile):
            pass

    summary = {
        "step_count": step_count,
        "patch_count": patch_count,
        "anchored_count": anchored_count,
        "node_count": node_count,
        "capture_methods": capture_methods,
    }

    digest_material = {
        "project_slug": project_slug,
        "trace_id": trace_id,
        "step_count": step_count,
        "patch_count": patch_count,
        "anchored_count": anchored_count,
        "node_count": node_count,
        "capture_methods": capture_methods,
        "lifecycle": lifecycle,
        "agent_name": agent_name,
        "agent_version": agent_version,
        "agent_model": agent_model,
        "has_trail": has_trail,
        "has_context": has_context,
        "has_sources": has_sources,
    }
    return {
        "project_slug": project_slug,
        "trace_id": trace_id,
        "title": title,
        "agent_name": agent_name,
        "agent_version": agent_version,
        "agent_model": agent_model,
        "trace_path": trace_json.relative_to(paths.bucket_dir()).as_posix(),
        "lifecycle": lifecycle or "provisional",
        "summary": summary,
        "files": {
            "has_trail": has_trail,
            "has_context": has_context,
            "has_sources": has_sources,
        },
        "remote_sync_eligible": False,
        "digest": _digest_payload(digest_material),
    }


def iter_traces_v2(
    project_slug: str | None = None,
) -> list[dict[str, Any]]:
    """Yield per-trace summary rows from the v2 layout (plan 080 §4).

    Sorted by ``(project_slug, trace_id)``. Used by ``ctx list``, manifest
    projection, and ``bucket verify``.
    """

    root = traces_v1_root()
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    glob_prefix = (
        f"{_path_part(project_slug)}/*" if project_slug else "*/*"
    )
    for trace_json in sorted(root.glob(f"{glob_prefix}/trace.json")):
        proj_dir = trace_json.parent.parent
        proj_slug = unquote(proj_dir.name)
        tid = unquote(trace_json.parent.name)
        record: TraceRecord | None = None
        try:
            raw = json.loads(trace_json.read_text(encoding="utf-8"))
            record = TraceRecord.model_validate(raw)
        except (OSError, ValueError, json.JSONDecodeError, ValidationError):
            record = None
        rows.append(_per_trace_v2_summary(proj_slug, tid, record))
    rows.sort(key=lambda item: (item["project_slug"], item["trace_id"]))
    return rows


def trace_v2_summary_by_id(trace_id: str) -> dict[str, Any] | None:
    """Resolve ONE trace's v2 summary row by ``trace_id`` (no manifest read).

    Issue #54 — backs ``ctx info``'s documented ``trace.json`` fallback. When
    the manifest has no row for ``trace_id`` but the per-trace envelope exists
    on disk, this globs ``traces/v1/*/<trace_id>/trace.json`` and derives the
    summary via :func:`_per_trace_v2_summary`, so the fallback's info block is
    byte-identical to the block ``ctx info`` reads from a manifest row.
    Returns ``None`` when no envelope exists. Bounded to one trace — NOT the
    ``ctx list`` 10k-trace perf gate (a single-id ``ctx info`` lookup).
    """

    root = traces_v1_root()
    if not root.exists():
        return None
    for trace_json in sorted(root.glob(f"*/{_path_part(trace_id)}/trace.json")):
        proj_slug = unquote(trace_json.parent.parent.name)
        record: TraceRecord | None = None
        try:
            raw = json.loads(trace_json.read_text(encoding="utf-8"))
            record = TraceRecord.model_validate(raw)
        except (OSError, ValueError, json.JSONDecodeError, ValidationError):
            record = None
        return _per_trace_v2_summary(proj_slug, trace_id, record)
    return None


def _iter_opted_in_projects() -> list[tuple[Path, str]]:
    """Return ``(project_path, project_slug)`` pairs for every opted-in project.

    Helper used by :func:`bucket_repair` / :func:`rebuild_bucket_trail` /
    :func:`rebuild_bucket_traces` to walk the full registry deterministically.
    Skips projects whose on-disk path is missing.
    """

    try:
        from .config import get_project_dir, load_config, opted_in_projects
    except Exception:
        return []
    try:
        cfg = load_config()
    except Exception:
        return []
    out: list[tuple[Path, str]] = []
    for raw_path in opted_in_projects(cfg):
        project_path = Path(raw_path)
        if not project_path.exists():
            continue
        try:
            slug = get_project_dir(project_path).name
        except Exception:
            continue
        out.append((project_path, slug))
    # Deterministic ordering: by slug (matches downstream sort orders).
    out.sort(key=lambda item: item[1])
    return out


def _events_for_export_loop(repo: Path) -> list[Any]:
    """One full event read shared across a per-trace export loop (#65).

    ``read_events`` memoises per (repo, head), so repair/rebuild loops pay one
    full read instead of one per trace. Returns [] on any failure — the
    per-trace export then falls back to its own (mirror) sources.
    """

    try:
        from .trails import read_events
    except Exception:
        return []
    try:
        return list(read_events(repo, verify=False))
    except Exception:
        return []


def _trace_ids_for_project(repo: Path) -> list[str]:
    """Return distinct ``trace_id`` values present in ``repo``'s event log.

    Pulled from the canonical Git event log (the source of truth). Returns a
    sorted, deduplicated list so the projection order is deterministic.

    plan 090: the v2 anchor-search summary event carries top-level trace_id=None,
    so it contributes no id here. That is safe and intentional: a search only
    ever runs for an existing patch, so every trace_id inside a summary's
    results[] necessarily also appears on that patch's ``trace_patch_created``
    event, which IS counted. No trace is ever missed by skipping the summary.
    """

    try:
        from .trails import read_events
    except Exception:
        return []
    try:
        events = read_events(repo, verify=False)
    except Exception:
        return []
    seen: set[str] = set()
    for event in events:
        tid = event.trace_id
        if not tid and isinstance(event.payload, dict):
            tid = event.payload.get("trace_id")
        if tid:
            seen.add(str(tid))
    return sorted(seen)


def _is_legacy_read_in_place_mirror(project_slug: str, trace_id: str) -> bool:
    """True when ``trace_id``'s object-store entry is a plan-085-S5 legacy
    read-in-place mirror, i.e. it must NOT be auto-adopted into a per-trace
    v2 envelope / ``manifest.traces[]``.

    Plan 085 S5 (read-in-place): legacy ``traces/*.jsonl`` records are
    mirrored into the TraceRecord object store as a query substrate
    (:func:`sync_trace_records_from_local_stores`, run by ``trace index
    rebuild``), but they must never be auto-adopted into per-trace v2
    envelopes / ``manifest.traces[]``. Two facts must hold for a pair to be
    classified legacy:

    1. **The in-place JSONL still exists** (``~/.opentraces/projects/<slug>/
       traces/<trace_id>.jsonl`` or staging). Both the 0.3.3 and 0.4 writers
       name the file ``<trace_id>.jsonl``. When it is gone the trace is a
       restored bucket (cross-machine pull) and the auto-materialization
       passes (#28 / #31) must heal it regardless of provenance.
    2. **No capture-time raw-source link exists** for the pair
       (``bucket/objects/raw/v1/sources/<slug>/<trace_id>.json``). The link
       is written exclusively by capture-time ingest
       (:func:`write_raw_source_artifact`'s sole caller is
       ``core/ingest.py``, on BOTH the full and the ``--trace-record-only``
       paths), never by the legacy mirror bridge — and 0.3.3 had no bucket
       at all. It is therefore the per-trace provenance discriminator
       (PR #63): a record-only staged trace deliberately defers
       ``project_per_trace_exports`` at ingest ("projection deferred") and
       MUST be materialized by manifest/repair later, while a true legacy
       mirror is read in place forever. Adoption of a legacy trace remains
       reserved for genuine re-capture through ingest, which writes the
       link.
    """

    if project_slug == TRACE_RECORD_PROJECT_STAGING:
        staging_root = getattr(paths, "STAGING_DIR", None)
        if not staging_root:
            return False
        in_place = (Path(staging_root) / f"{trace_id}.jsonl").exists()
    else:
        in_place = (
            paths.PROJECTS_DIR / project_slug / "traces" / f"{trace_id}.jsonl"
        ).exists()
    if not in_place:
        return False
    capture_link = (
        raw_sources_root()
        / "sources"
        / _path_part(project_slug)
        / f"{_path_part(trace_id)}.json"
    )
    return not capture_link.exists()


def _read_jsonl_trace_records(path: Path) -> list[TraceRecord]:
    out: list[TraceRecord] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        if not line.strip():
            continue
        try:
            out.append(load_record_json(line))
        except (ValueError, json.JSONDecodeError, ValidationError):
            continue
    return out


def _normalized_record(record: TraceRecord) -> TraceRecord:
    normalized = record.model_copy(deep=True)
    normalized.content_hash = normalized.compute_content_hash()
    return normalized


def _resolve_trace_record_pointer(path: Path) -> Path:
    if path.name != "current.json":
        return path
    try:
        pointer = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return path
    if pointer.get("schema_version") != TRACE_RECORD_POINTER_SCHEMA:
        return path
    object_path = pointer.get("object_path")
    if not isinstance(object_path, str) or not object_path:
        return path
    return paths.bucket_dir() / object_path

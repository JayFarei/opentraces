"""Local bucket-shaped trace substrate.

The bucket is the local mirror of the future remote sync substrate. It stores
content-addressed trace evidence, optional raw source artifacts, portable
Trace Trail event exports, and rebuildable projections; versioned datasets
stay outside the bucket as HF-shaped repositories.

This module is the PUBLIC FACADE. The implementation is split across three
sibling modules to keep each cluster cohesive:

  _bucket_io.py            — Pure I/O utilities (atomic writes, gzip, digests)
  bucket_events.py         — Trail/events-mirror cluster (plan 080 §4)
  bucket_context_store.py  — Context Tree bucket projection (plan 079/080)

Every symbol that existed here before the split is re-exported from this
module so all ~91 existing call sites remain importable from
``opentraces.core.bucket_store`` without change.
"""

from __future__ import annotations

import fcntl
import gzip
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from opentraces.core._time import utc_now_str
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote

from pydantic import ValidationError

from opentraces_schema import TraceRecord, load_record_json

from ..security.version import SECURITY_VERSION
from ..security.privacy import (
    bucket_security_state,
    record_privacy_tier,
)
from . import paths
from .bucket_layout import (
    _digest_hex,
    _path_part,
    blobs_v1_context_path,
    blobs_v1_raw_path,
    blobs_v1_root,
    bucket_manifest_path,
    bucket_sync_state_path,
    context_tree_dir,
    context_tree_head_path,
    context_tree_nodes_path,
    context_tree_reconciliation_path,
    contexts_root,
    events_v1_batches_dir,
    events_v1_index_path,
    events_v1_root,
    legacy_trace_records_root,
    raw_sources_root,
    trace_records_root,
    trace_v1_context_path,
    trace_v1_history_dir,
    trace_v1_json_path,
    trace_v1_sources_path,
    trace_v1_trail_path,
    traces_v1_dir,
    traces_v1_root,
    trail_events_root,
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
    _atomic_write_text,
    _canonical_json,
    _digest_bytes,
    _digest_payload,
    _gzip_deterministic,
    _read_gzip_bytes,
)

# ---------------------------------------------------------------------------
# Re-export trail/events-mirror cluster from bucket_events.
# ---------------------------------------------------------------------------
from .bucket_events import (
    BUCKET_EVENTS_INDEX_SCHEMA,
    TRAIL_EVENT_SNAPSHOT_SCHEMA,
    read_events_mirror_batches,
    read_trail_event_export,
    restore_trail_events_to_repo,
    sync_events_mirror,
    sync_trail_events_from_repo,
    trail_event_snapshot,
)

# ---------------------------------------------------------------------------
# Re-export Context Tree bucket projection cluster from bucket_context_store.
# ---------------------------------------------------------------------------
from .bucket_context_store import (
    CONTEXT_LAYER_BLOB_SCHEMA,
    CONTEXT_TREE_BUCKET_SCHEMA,
    CONTEXT_TREE_REMOTE_SYNC_BLOCKER,
    CONTEXT_TREE_SNAPSHOT_SCHEMA,
    _BRANCH_TYPE_ORDINAL,
    _build_context_head,
    _build_context_layer_blob,
    _context_blob_scope,
    _head_payload_to_row,
    _iter_context_tree_head_payloads,
    compute_context_tree_status,
    context_tree_snapshot,
    iter_context_tree_traces,
    project_context_tree_to_bucket,
    read_context_tree_head,
    verify_context_tree_layer_refs,
)


# Bucket data models + schema-version constants live in the dependency-free base
# module ``bucket_models``; re-exported here so ``from ...bucket_store import
# BucketTraceRecord`` / ``BUCKET_MANIFEST_SCHEMA`` (and the rest) keep working.
# Imported eagerly: ``bucket_models`` imports nothing from this package, so there
# is no cycle, and these names are used throughout the manifest code below.
from .bucket_models import (
    BUCKET_MANIFEST_SCHEMA,
    BUCKET_PER_TRACE_SCHEMA,
    BUCKET_REMOTE_SCHEMA,
    BucketLayoutError,
    BucketSyncSummary,
    BucketTraceRecord,
    BucketTraceRecordPointer,
    RAW_SOURCE_SCHEMA,
    RAW_SOURCE_SNAPSHOT_SCHEMA,
    TRACE_RECORD_BUCKET_SCHEMA,
    TRACE_RECORD_POINTER_SCHEMA,
    TRACE_RECORD_PROJECT_STAGING,
    TRAIL_EVENT_EXPORT_SCHEMA,
)


def read_bucket_sync_state() -> dict[str, Any]:
    path = bucket_sync_state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def write_bucket_sync_state(
    *,
    provider: str,
    target: str,
    digest: str | None,
    direction: str,
    remote_digest: str | None = None,
) -> dict[str, Any]:
    state = {
        "schema_version": "opentraces.bucket.sync_state.v1",
        "provider": provider,
        "target": target,
        "last_sync_digest": digest,
        "last_remote_digest": remote_digest or digest,
        "last_direction": direction,
        "synced_at": utc_now_str(),
    }
    _atomic_write_json(bucket_sync_state_path(), state)
    return state


def classify_bucket_remote_state(
    *,
    provider: str,
    target: str,
    local_digest: str | None,
    remote_digest: str | None,
) -> dict[str, Any]:
    """Classify local/remote relation using the last successful sync point."""

    if remote_digest is None:
        return {"state": "missing", "last_sync_digest": None}
    if remote_digest == local_digest:
        return {"state": "current", "last_sync_digest": remote_digest}
    sync_state = read_bucket_sync_state()
    last = (
        sync_state.get("last_sync_digest")
        if sync_state.get("provider") == provider and sync_state.get("target") == target
        else None
    )
    if not last:
        return {"state": "different", "last_sync_digest": None}
    local_at_last = local_digest == last
    remote_at_last = remote_digest == last
    if local_at_last and not remote_at_last:
        state = "remote_ahead"
    elif remote_at_last and not local_at_last:
        state = "local_ahead"
    elif not local_at_last and not remote_at_last:
        state = "diverged"
    else:
        state = "different"
    return {"state": state, "last_sync_digest": last}


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


def _load_manifest(path: Path | None = None) -> dict[str, Any] | None:
    """Read ``bucket/manifest.json`` and enforce the v2 schema (Resolution E).

    Returns ``None`` when the manifest does not exist. Raises
    :class:`BucketLayoutError` on schema mismatch — there is no v1 reader
    on this branch.
    """

    manifest_path = path or bucket_manifest_path()
    if not manifest_path.exists():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise BucketLayoutError(
            f"bucket manifest is unreadable: {manifest_path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise BucketLayoutError(
            f"bucket manifest is not an object: {manifest_path}"
        )
    found = payload.get("schema_version")
    if found != BUCKET_MANIFEST_SCHEMA:
        raise BucketLayoutError(
            f"bucket manifest schema {found!r} incompatible with local "
            f"{BUCKET_MANIFEST_SCHEMA!r}; run 'opentraces setup bucket --migrate'"
        )
    return payload


DEFAULT_BUCKET_MANIFEST_MAX_BYTES = 16 * 1024 * 1024


def bucket_manifest_max_bytes() -> int:
    """Byte cap for cheap persisted-manifest reads (shared by doctor + gate).

    Reads ``OPENTRACES_DOCTOR_BUCKET_MANIFEST_MAX_BYTES`` (the same env knob
    doctor uses) so doctor's bucket panel and ``bucket remote status``'s
    security gate degrade on an oversized manifest at the SAME threshold.
    """

    raw = os.environ.get("OPENTRACES_DOCTOR_BUCKET_MANIFEST_MAX_BYTES")
    if raw:
        try:
            return max(1024, int(raw))
        except ValueError:
            return DEFAULT_BUCKET_MANIFEST_MAX_BYTES
    return DEFAULT_BUCKET_MANIFEST_MAX_BYTES


def read_persisted_manifest_capped(
    max_bytes: int | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Read-only, byte-capped read of the persisted ``bucket/manifest.json``.

    Returns ``(state, manifest)`` where ``state`` is one of ``"ok"`` /
    ``"absent"`` / ``"too_large"`` / ``"error"``. NEVER scans the bucket and
    NEVER writes ``manifest.json`` — it is the cheap, side-effect-free read that
    both doctor's bucket panel and the ``bucket remote status`` security gate use
    so a huge manifest degrades identically (matching doctor's ``too-large``
    behaviour) instead of stalling / blowing memory on ``read_text``.
    """

    manifest_path = bucket_manifest_path()
    if not manifest_path.exists():
        return ("absent", None)
    cap = bucket_manifest_max_bytes() if max_bytes is None else max_bytes
    try:
        if manifest_path.stat().st_size > cap:
            return ("too_large", None)
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return ("error", None)
    if not isinstance(payload, dict):
        return ("error", None)
    if payload.get("schema_version") != BUCKET_MANIFEST_SCHEMA:
        return ("error", None)
    return ("ok", payload)


def bucket_manifest(
    *,
    write: bool = False,
    heal: bool = True,
    include_objects: bool = False,
    progress=None,
) -> dict[str, Any]:
    """Return the local bucket manifest used by future remote sync.

    The manifest is intentionally transport-neutral: it summarizes the
    canonical bucket substrate and the local projections that remote sync must
    treat as derived state.

    Issue #55 — ``heal`` controls the #31 read-side reconcile's DISK side
    effects. Default ``heal=True`` keeps the materializing behavior for every
    internal caller (``bucket repair``, ``bucket_remote``, ``doctor``, ...).
    The two CLI read verbs pass ``heal=False`` (and ``write=False``) so they
    are byte-level side-effect-free: the reconcile loop still appends each
    orphan's record-derived summary row IN MEMORY (so counts + ``bucket_digest``
    stay identical to the healed state), but writes NO per-trace envelope and
    NO ``manifest.json``. The summary is record-derived in both branches, so
    the digest invariant holds (the read-only digest equals the digest a
    later ``--heal`` / ``bucket repair`` persists to disk).

    #87 — ``progress`` (a shared ``ProgressReporter`` or ``None``) labels each
    O(N) scan phase so a long read on a large bucket is heartbeat-observable
    rather than a silent multi-minute hang. ``None`` → a no-op ``NullProgress``,
    keeping every non-CLI caller byte-identical.
    """

    if progress is None:
        from .progress import NullProgress

        progress = NullProgress()

    progress.stage("scan_trace_records")
    trace_snapshot = trace_record_snapshot(include_objects=include_objects)
    objects = trace_snapshot.get("objects") or []
    # Issue #31 — iterate the TraceRecord object store ONCE; reuse the parsed
    # ``BucketTraceRecord`` objects for both the (compat) objects block and the
    # read-side self-heal of the v2 ``traces[]`` rows below.
    record_objects = iter_trace_record_objects()
    if not include_objects:
        objects = [
            {
                "privacy_tier": obj.envelope.get("security", {}).get("privacy_tier"),
                "security_version": obj.envelope.get("security", {}).get("security_version"),
                "syncable": obj.envelope.get("security", {}).get("syncable"),
                "security_stale": obj.envelope.get("security", {}).get("stale"),
                "written_at": obj.envelope.get("written_at"),
            }
            for obj in record_objects
        ]
    syncable_count = sum(1 for obj in objects if obj.get("syncable") is True)
    stale_security_count = sum(1 for obj in objects if obj.get("security_stale") is True)
    privacy_off_count = sum(1 for obj in objects if obj.get("privacy_tier") == "off")
    unfiltered_count = sum(1 for obj in objects if obj.get("syncable") is False)
    last_trace_record_write_at = max(
        (str(obj.get("written_at") or "") for obj in objects),
        default="",
    ) or None

    progress.stage("scan_raw_sources")
    raw_snapshot = raw_source_snapshot(include_objects=include_objects)
    progress.stage("scan_trail_events")
    trail_event_exports = trail_event_snapshot(include_objects=include_objects)
    progress.stage("scan_context_trees")
    context_trees_snapshot = context_tree_snapshot(include_objects=include_objects)

    progress.stage("trail_freshness")
    trail_freshness: list[dict[str, Any]] = []
    try:
        from .trace_index import default_index_path, trail_freshness_warnings

        trail_freshness = trail_freshness_warnings(
            index_path=default_index_path(),
            include_current=True,
        )
    except Exception:
        trail_freshness = []
    stale_trail_count = sum(
        1 for item in trail_freshness if item.get("severity") == "warning"
    )
    last_trail_projection_sync_at = max(
        (str(item.get("last_synced_at") or "") for item in trail_freshness),
        default="",
    ) or None

    # Plan 080 §4 — new ``traces[]`` block + ``events_v1`` index. Sorted by
    # (project_slug, trace_id) for deterministic digests.
    traces_v2_rows = iter_traces_v2()

    # Issue #31 — read-side reconcile. On a restored / cross-machine world the
    # TraceRecord object store can hold traces that have no per-trace v2
    # envelope yet (ingest used to write the envelope only on the live-project
    # hot path, and ``bucket repair`` only walked live opted-in projects). The
    # manifest-only readers (``bucket manifest`` / ``ctx list``) then reported 0
    # traces while ``bucket status`` / the index / ``ctx tree`` saw them. Heal:
    # for every (project_slug, trace_id) present in the object store but missing
    # from ``traces[]``, materialize the per-trace envelope from canonical data
    # (live event log if the project resolves, else the bucket events mirror,
    # else a degraded envelope from the TraceRecord alone) and add its summary
    # row. Materializing on disk in both write modes keeps ``bucket verify``
    # check 3 green and keeps ``bucket repair``'s write=False candidate-digest
    # comparison consistent. Atomic same-bytes writers preserve idempotency.
    progress.stage("reconcile_traces")
    _existing_pairs = {(row["project_slug"], row["trace_id"]) for row in traces_v2_rows}
    _project_paths: dict[str, Path] | None = None
    for obj in record_objects:
        pair = (obj.project_slug, obj.trace_id)
        if pair in _existing_pairs:
            continue
        # Plan 085 S5 — read-in-place. Legacy-store mirrors (no capture-time
        # raw-source link) are a query substrate, not bucket content; never
        # auto-adopt them. Record-only staged traces (PR #63) carry the link
        # and self-heal here — their deferred projection.
        if _is_legacy_read_in_place_mirror(*pair):
            continue
        if not heal:
            # Issue #55 read-only path: report the orphan in-memory WITHOUT
            # touching disk. ``assume_envelope_present`` makes the summary
            # reflect the POST-heal disk state (heal always writes all three
            # framed companions, so has_trail/has_context/has_sources are True;
            # node_count is counted from canonical context events read-only).
            # Keeps counts + bucket_digest byte-identical to the healed state
            # so the digest invariant (BKT-3/BKT-6) holds.
            traces_v2_rows.append(
                _per_trace_v2_summary(
                    obj.project_slug,
                    obj.trace_id,
                    obj.record,
                    assume_envelope_present=True,
                )
            )
            _existing_pairs.add(pair)
            continue
        try:
            if _project_paths is None:
                _project_paths = {
                    slug: path for path, slug in _iter_opted_in_projects()
                }
            repo = _project_paths.get(obj.project_slug)
            project_per_trace_exports(
                repo,
                project_slug=obj.project_slug,
                trace_id=obj.trace_id,
                record=obj.record,
            )
            traces_v2_rows.append(
                _per_trace_v2_summary(obj.project_slug, obj.trace_id, obj.record)
            )
            _existing_pairs.add(pair)
        except Exception:
            # Degraded fallback: write an envelope with empty event companions
            # straight from the TraceRecord so the trace is never dropped.
            try:
                _write_per_trace_envelope(
                    obj.project_slug, obj.trace_id, obj.record, [], []
                )
                traces_v2_rows.append(
                    _per_trace_v2_summary(obj.project_slug, obj.trace_id, obj.record)
                )
                _existing_pairs.add(pair)
            except Exception:
                pass
    traces_v2_rows.sort(key=lambda item: (item["project_slug"], item["trace_id"]))

    # ``events_v1`` mirror summary (single-repo today; multi-repo when ingest
    # writes multiple mirror trees).
    events_v1_index: dict[str, Any] = {
        "schema_version": BUCKET_EVENTS_INDEX_SCHEMA,
        "batch_count": 0,
        "last_batch_id": None,
        "latest_event_sequence": 0,
    }
    idx_path = events_v1_index_path()
    if idx_path.exists():
        try:
            raw_idx = json.loads(idx_path.read_text(encoding="utf-8"))
            if (
                isinstance(raw_idx, dict)
                and raw_idx.get("schema_version") == BUCKET_EVENTS_INDEX_SCHEMA
            ):
                events_v1_index = {
                    "schema_version": BUCKET_EVENTS_INDEX_SCHEMA,
                    "batch_count": int(raw_idx.get("batch_count") or 0),
                    "last_batch_id": raw_idx.get("last_batch_id"),
                    "latest_event_sequence": int(
                        raw_idx.get("latest_event_sequence") or 0
                    ),
                }
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    # Determine ``bucket_root`` — the project slug if exactly one project's
    # traces are present; otherwise an empty string (multi-project bucket).
    project_slugs = sorted({row["project_slug"] for row in traces_v2_rows})
    bucket_root_slug = project_slugs[0] if len(project_slugs) == 1 else ""

    manifest: dict[str, Any] = {
        "schema_version": BUCKET_MANIFEST_SCHEMA,
        "bucket_root": bucket_root_slug,
        "root": str(paths.bucket_dir()),
        "generated_at": utc_now_str(),
        "updated_at": utc_now_str(),
        "security_version": SECURITY_VERSION,
        # Plan 080 v2 fields (canonical):
        "traces": traces_v2_rows,
        "events_v1": events_v1_index,
        # Compat fields (v1 manifest substructure, retained for current callers):
        "trace_records": {
            "snapshot": trace_snapshot,
            "object_count": trace_snapshot.get("object_count", 0),
            "syncable_count": syncable_count,
            "unfiltered_count": unfiltered_count,
            "privacy_off_count": privacy_off_count,
            "security_stale_count": stale_security_count,
            "last_write_at": last_trace_record_write_at,
        },
        "trail": {
            "freshness": trail_freshness,
            "stale_count": stale_trail_count,
            "last_projection_sync_at": last_trail_projection_sync_at,
        },
        "sync": {
            "eligible": unfiltered_count == 0 and stale_security_count == 0,
            "blocked_reasons": _bucket_sync_blockers(
                unfiltered_count=unfiltered_count,
                stale_security_count=stale_security_count,
            ),
        },
        "raw_sources": raw_snapshot,
        "trail_events": trail_event_exports,
        "context_trees": context_trees_snapshot,
    }

    # Plan 080 Resolution H — ``bucket_digest`` is deterministic across
    # machines: excludes ``generated_at``/``updated_at``; sorts traces[] by
    # (project_slug, trace_id).
    # Digest material covers exactly the SYNCED bucket unit (plan 080
    # Resolution H: deterministic across machines):
    # * machine-local absolute paths ("root", "repo_path") are excluded —
    #   the same bucket restored at a different path must hash identically
    #   (issue #25 PR-B finding #2, confirmed live by the live-hf-* journeys:
    #   pull on another machine was permanently "remote_ahead").
    # * ``context_trees`` is excluded entirely: plan-079 R8 hardcodes
    #   ``remote_sync.eligible = False`` for every context-tree head, so push
    #   deliberately skips that substrate — counting it in the digest made a
    #   pulled bucket unable to ever reproduce the pushed digest. If R8 is
    #   ever lifted (or ``--unsafe-push`` grows a synced-context contract),
    #   the eligible subset must re-enter this material.
    bucket_digest_material = _machine_neutral_digest_view(
        {
            "schema_version": manifest["schema_version"],
            "bucket_root": manifest["bucket_root"],
            "security_version": manifest["security_version"],
            "traces": sorted(
                traces_v2_rows, key=lambda r: (r["project_slug"], r["trace_id"])
            ),
            "events_v1": manifest["events_v1"],
            "trace_records": manifest["trace_records"],
            "trail": manifest["trail"],
            "raw_sources": manifest["raw_sources"],
            "trail_events": manifest["trail_events"],
            "sync": manifest["sync"],
        }
    )
    bucket_digest = _digest_payload(bucket_digest_material)
    manifest["bucket_digest"] = bucket_digest
    # Retain ``digest`` as a compat alias for callers that still consume the
    # v1 field name (bucket_remote, datasets).
    manifest["digest"] = bucket_digest
    if write:
        # Write-only-on-change discipline (matches bucket_repair §5): persist
        # manifest.json only when the content differs from the on-disk
        # manifest. ``_atomic_write_json``'s same-bytes skip is not enough
        # here because ``generated_at``/``updated_at`` advance every call —
        # so byte-comparison would ALWAYS rewrite. The compare is the FULL
        # manifest minus only those volatile timestamps (NOT the Resolution-H
        # digest alone): the digest excludes machine-local keys and any field
        # outside its material, so a digest-only skip could leave a
        # stale-SHAPED manifest.json on disk after a code upgrade — bytes
        # bucket_remote would then push. Content-compare keeps idempotent
        # re-projections byte-stable (``bucket manifest --heal`` twice =
        # no-op; issue #55) while any real shape/content change still writes.
        manifest_path = bucket_manifest_path()
        existing_view: Any = None
        if manifest_path.exists():
            try:
                existing_doc = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
                if isinstance(existing_doc, dict):
                    existing_view = _manifest_change_view(existing_doc)
            except (OSError, ValueError, json.JSONDecodeError):
                existing_view = None
        if existing_view is None or existing_view != _manifest_change_view(
            manifest
        ):
            _atomic_write_json(manifest_path, manifest)
    return manifest


_MANIFEST_VOLATILE_KEYS = frozenset({"generated_at", "updated_at"})


def _manifest_change_view(doc: dict[str, Any]) -> dict[str, Any]:
    """Manifest content minus the per-call volatile timestamps.

    The write-only-on-change compare in :func:`bucket_manifest` keys on this
    view: two manifests that differ only in ``generated_at``/``updated_at``
    are the SAME content (skip the rewrite, keep bytes stable); any other
    delta — including shape changes invisible to ``bucket_digest`` — writes.
    """

    return {k: v for k, v in doc.items() if k not in _MANIFEST_VOLATILE_KEYS}


def _minimal_manifest_doc() -> dict[str, Any]:
    """A schema-valid empty ``opentraces.bucket.manifest.v2`` document.

    Issue #54 — the seed the bounded capture-time upsert writes when no
    ``manifest.json`` exists yet. It carries only the canonical v2 skeleton
    (``schema_version`` + an empty ``traces[]`` + an empty ``events_v1``
    index) plus empty compat blocks every ``.get()``-guarded consumer
    (``bucket_remote``, ``datasets``) already tolerates. A later full
    :func:`bucket_manifest` regeneration (run by ``bucket status`` / ``bucket
    remote push`` / ``bucket repair``) overwrites it with the populated
    blocks; the upsert never sweeps the object store to fill them in (that is
    the #44 latency class this seed exists to avoid).
    """

    return {
        "schema_version": BUCKET_MANIFEST_SCHEMA,
        "bucket_root": "",
        "root": str(paths.bucket_dir()),
        "generated_at": utc_now_str(),
        "updated_at": utc_now_str(),
        "security_version": SECURITY_VERSION,
        "traces": [],
        "events_v1": {
            "schema_version": BUCKET_EVENTS_INDEX_SCHEMA,
            "batch_count": 0,
            "last_batch_id": None,
            "latest_event_sequence": 0,
        },
        "trace_records": {},
        "trail": {},
        "sync": {},
        "raw_sources": {},
        "trail_events": {},
        "context_trees": {},
    }


@contextmanager
def _manifest_upsert_lock() -> Iterator[None]:
    """Bucket-level exclusive flock serializing the manifest upsert.

    Issue #54 adversary finding — ingest's flock is keyed per ``session_id``,
    so two concurrent ingests of DIFFERENT sessions could both read the same
    ``manifest.json``, each append its own row, and the later atomic replace
    would drop the earlier trace's row (lost update; the exact invisibility
    class the upsert exists to close). Blocking is correct here because the
    hold time is one read + one write of a small JSON doc (mirrors
    ``ingest._FileLock``'s rationale).
    """

    lock_path = bucket_manifest_path().with_name("manifest.json.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def upsert_manifest_trace_row(
    repo: Path | None,
    *,
    project_slug: str,
    trace_id: str,
    record: TraceRecord | None = None,
) -> dict[str, Any] | None:
    """Insert/replace one trace's ``traces[]`` row in ``bucket/manifest.json``.

    Issue #54 — capture materializes the manifest row so the manifest-only
    readers (``ctx list`` / ``ctx info``) see a freshly captured trace
    immediately, WITHOUT a ``bucket manifest`` / ``bucket repair`` heal verb.
    Called from :func:`opentraces.core.ingest.ingest_one_session` right after
    :func:`project_per_trace_exports`, inside the ``if not trace_record_only``
    guard.

    Bounded to O(one trace): the summary row is computed via
    :func:`_per_trace_v2_summary` from the per-trace envelope this ingest just
    wrote (byte-identical to the row a full ``bucket_manifest`` regeneration
    produces). It NEVER calls :func:`iter_trace_record_objects` /
    :func:`trace_record_snapshot` or regenerates the whole manifest (the #44
    post-commit latency class). When ``manifest.json`` is absent a minimal
    schema-valid v2 doc is seeded; the row is upserted (replace if the
    ``(project_slug, trace_id)`` pair already exists, else append) and
    ``traces[]`` re-sorted; ``bucket_root`` + ``bucket_digest`` are recomputed
    over the in-memory rows; the doc is written atomically with the same
    write-only-on-change discipline as :func:`bucket_manifest` (compare the
    full content minus volatile timestamps so idempotent re-captures stay
    byte-stable). Returns the upserted row (``None`` only if the row could not
    be computed — best-effort, never fatal to capture).

    A subsequent full ``bucket_manifest(write=True)`` regeneration overwrites
    the minimal compat blocks with the swept-store content; the per-trace row
    is unchanged because both paths derive it from the same envelope.
    """

    row = _per_trace_v2_summary(project_slug, trace_id, record)

    manifest_path = bucket_manifest_path()
    with _manifest_upsert_lock():
        doc: dict[str, Any] | None = None
        existing_view: Any = None
        if manifest_path.exists():
            try:
                raw_text = manifest_path.read_text(encoding="utf-8")
                loaded = json.loads(raw_text)
                if isinstance(loaded, dict):
                    # Independent parse: ``doc`` is mutated below, so the
                    # change-view compare must hold its own object graph.
                    existing_view = _manifest_change_view(json.loads(raw_text))
                    if loaded.get("schema_version") == BUCKET_MANIFEST_SCHEMA:
                        doc = loaded
            except (OSError, ValueError, json.JSONDecodeError):
                doc = None
                existing_view = None
        if doc is None:
            doc = _minimal_manifest_doc()

        traces = [r for r in (doc.get("traces") or []) if isinstance(r, dict)]
        traces = [
            r
            for r in traces
            if (r.get("project_slug"), r.get("trace_id"))
            != (project_slug, trace_id)
        ]
        traces.append(row)
        traces.sort(key=lambda item: (item["project_slug"], item["trace_id"]))
        doc["traces"] = traces

        project_slugs = sorted({r["project_slug"] for r in traces})
        doc["bucket_root"] = project_slugs[0] if len(project_slugs) == 1 else ""
        doc["updated_at"] = utc_now_str()

        # The digest covers the doc AS WRITTEN (self-consistent), not the
        # digest a full swept regeneration would produce — the compat blocks
        # stay as-loaded until heal. That transient delta is consumer-safe:
        # every top-level-digest consumer regenerates first
        # (``bucket_remote`` calls ``bucket_manifest(write=True)`` before
        # every ``.get("digest")``; ``datasets`` recomputes ``write=False``);
        # ``ctx info``'s ``digest`` is the per-trace ROW digest, which is
        # byte-identical across both paths.
        bucket_digest_material = _machine_neutral_digest_view(
            {
                "schema_version": doc["schema_version"],
                "bucket_root": doc["bucket_root"],
                "security_version": doc.get("security_version", SECURITY_VERSION),
                "traces": sorted(
                    traces, key=lambda r: (r["project_slug"], r["trace_id"])
                ),
                "events_v1": doc.get("events_v1", {}),
                "trace_records": doc.get("trace_records", {}),
                "trail": doc.get("trail", {}),
                "raw_sources": doc.get("raw_sources", {}),
                "trail_events": doc.get("trail_events", {}),
                "sync": doc.get("sync", {}),
            }
        )
        bucket_digest = _digest_payload(bucket_digest_material)
        doc["bucket_digest"] = bucket_digest
        doc["digest"] = bucket_digest

        if existing_view is None or existing_view != _manifest_change_view(doc):
            _atomic_write_json(manifest_path, doc)
    return row


_MACHINE_LOCAL_DIGEST_KEYS = frozenset({"root", "repo_path"})


def _machine_neutral_digest_view(value: Any) -> Any:
    """Strip machine-local path keys from digest material, recursively.

    The manifest keeps the absolute paths for display; only the digest
    roll-up must be invariant under bucket relocation.
    """

    if isinstance(value, dict):
        return {
            k: _machine_neutral_digest_view(v)
            for k, v in value.items()
            if k not in _MACHINE_LOCAL_DIGEST_KEYS
        }
    if isinstance(value, list):
        return [_machine_neutral_digest_view(v) for v in value]
    return value


def _bounded_status_view(manifest: dict[str, Any]) -> dict[str, Any]:
    """Project the bucket manifest into a bounded, summary-only status view.

    Issue #97 — ``bucket status --json`` must answer sync/security/event/context
    counts WITHOUT dumping every trace. The full v2 ``traces[]`` enumeration (the
    dominant O(N) size term), the per-record ``trace_records.snapshot`` block, and
    the per-projection ``trail.freshness`` array are stripped here; the full
    per-trace listing stays on ``bucket manifest --json`` (the unchanged frozen
    ``opentraces.bucket.manifest.v2`` envelope). This is a non-mutating projection
    over a shallow copy so the manifest the caller computed is never aliased.

    NOTE (honest size-vs-latency split): this bounds OUTPUT size only. The
    manifest is still computed by the O(N) object-store scan, so ``bucket status``
    remains O(N) in WALL TIME — the latency cure (persisting scalar counters) is a
    tracked follow-up, out of this change's locked scope.
    """

    view = dict(manifest)
    # Drop the full per-trace enumeration; keep a scalar count. (Agrees with
    # ``trace_records.object_count`` by construction — one v2 row per record.)
    view["trace_count"] = len(manifest.get("traces") or [])
    view.pop("traces", None)
    view["traces_omitted"] = True  # self-documenting: full list via `bucket manifest --json`

    # Drop the non-scalar ``snapshot`` block; keep the sibling scalar counters.
    trace_records = manifest.get("trace_records")
    if isinstance(trace_records, dict):
        tr = dict(trace_records)
        tr.pop("snapshot", None)
        view["trace_records"] = tr

    # Drop the per-projection ``freshness`` array; keep ``stale_count`` +
    # ``last_projection_sync_at``.
    trail = manifest.get("trail")
    if isinstance(trail, dict):
        tr = dict(trail)
        tr.pop("freshness", None)
        view["trail"] = tr

    return view


def bucket_status(
    *, write_manifest: bool = True, heal: bool = True, progress=None
) -> dict[str, Any]:
    # Issue #55 — ``heal`` mirrors :func:`bucket_manifest`. Internal callers
    # keep the materializing default; the CLI ``bucket status`` read verb passes
    # ``write_manifest=False, heal=False`` for a byte-level side-effect-free read.
    # #87 — ``progress`` (a shared ``ProgressReporter`` or ``None``) is threaded
    # into the O(N) object-store scan so a long read is observable, not silent.
    manifest = bucket_manifest(
        write=write_manifest, heal=heal, include_objects=False, progress=progress
    )
    # Issue #97 — status is summary-only: bound the payload (drop traces[],
    # trace_records.snapshot, trail.freshness). Full listing via `bucket manifest`.
    return {
        "status": "ok",
        "bucket": _bounded_status_view(manifest),
        "config": _bucket_config_payload(),
    }


def sync_trace_records_from_local_stores(
    *,
    prune: bool = True,
) -> BucketSyncSummary:
    """Mirror legacy project/staging TraceRecord stores into the local bucket.

    This is the local migration bridge. It keeps existing project/staging stores
    working while making the bucket the query substrate.
    """

    root = trace_records_root()
    written = 0
    unchanged = 0
    skipped = 0
    expected_paths: set[Path] = set()
    cfg = None
    for record, project_slug, source_layer in _iter_legacy_trace_records():
        explicit_tier = record_privacy_tier(record)
        if _needs_legacy_security_refresh(record) and explicit_tier != "off":
            if cfg is None:
                from .config import load_config

                cfg = load_config()
            try:
                from .pipeline import process_imported_trace

                record = process_imported_trace(record, cfg).record
            except Exception:
                skipped += 1
                continue
        normalized = _normalized_record(record)
        record_hash = _digest_payload(normalized.model_dump(mode="json"))
        object_path = trace_record_object_path(project_slug, record.trace_id, record_hash)
        expected_paths.add(object_path)
        expected_paths.add(trace_record_path(project_slug, record.trace_id))
        existing = read_trace_record_object(trace_record_path(project_slug, record.trace_id))
        if existing and existing.record_hash == record_hash:
            unchanged += 1
            continue
        try:
            write_trace_record(
                normalized,
                project_slug=project_slug,
                source_layer=source_layer,
                legacy_mirror=True,
                privacy_tier=explicit_tier,
            )
            written += 1
        except OSError:
            skipped += 1

    removed = 0
    if prune and root.exists():
        for obj in iter_trace_record_objects():
            if obj.path in expected_paths:
                continue
            if obj.envelope.get("legacy_mirror") is True:
                try:
                    obj.path.unlink()
                    pointer = trace_record_path(obj.project_slug, obj.trace_id)
                    if pointer.exists():
                        pointer.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
    if removed:
        try:
            from .trace_search_state import mark_search_snapshot_dirty

            mark_search_snapshot_dirty("trace_record_remove")
        except Exception:
            pass
    return BucketSyncSummary(
        root=root,
        written=written,
        unchanged=unchanged,
        removed=removed,
        skipped=skipped,
    )


def _iter_legacy_trace_records() -> list[tuple[TraceRecord, str, str]]:
    records: list[tuple[TraceRecord, str, str]] = []
    projects_root = paths.PROJECTS_DIR
    if projects_root.exists():
        for project_home in sorted(path for path in projects_root.iterdir() if path.is_dir()):
            traces_dir = project_home / "traces"
            if not traces_dir.exists():
                continue
            for trace_path in sorted(traces_dir.glob("*.jsonl")):
                for record in _read_jsonl_trace_records(trace_path):
                    records.append((record, project_home.name, "canonical"))
    staging_root = getattr(paths, "STAGING_DIR", None)
    if staging_root and staging_root.exists() and staging_root.is_dir():
        for trace_path in sorted(staging_root.glob("*.jsonl")):
            for record in _read_jsonl_trace_records(trace_path):
                records.append((record, TRACE_RECORD_PROJECT_STAGING, "staging"))
    return records


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


def _needs_legacy_security_refresh(record: TraceRecord) -> bool:
    state = bucket_security_state(record)
    return not bool(state.get("syncable"))


def _bucket_sync_blockers(
    *,
    unfiltered_count: int,
    stale_security_count: int,
) -> list[str]:
    blockers: list[str] = []
    if unfiltered_count:
        blockers.append("unfiltered_records")
    if stale_security_count:
        blockers.append("security_version_stale")
    return blockers


def _bucket_config_payload() -> dict[str, Any]:
    try:
        from .config import load_config

        return load_config().bucket.model_dump(mode="json")
    except Exception:
        return {
            "storage": "local",
            "local_cache": True,
            "remote": {
                "enabled": False,
                "provider": "huggingface",
                "url": None,
                "visibility": "private",
                "sync_policy": "daemon",
            },
        }


def _copy_bucket_tree(
    source: Path,
    destination: Path,
    *,
    skip_names: set[str] | None = None,
) -> int:
    skipped = skip_names or set()
    copied = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        if path.name in skipped:
            continue
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() == path.read_bytes():
            continue
        tmp = target.with_name(f".{target.name}.tmp")
        shutil.copy2(path, tmp)
        tmp.replace(target)
        copied += 1
    return copied


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


# ---------------------------------------------------------------------------
# Re-export the bucket maintenance cluster (repair / verify / prune / prefetch /
# rebuild_* / migrate), moved to ``bucket_maintenance`` in the god-module
# decomposition. The re-export is LAZY (PEP 562 module ``__getattr__``) on
# purpose: ``bucket_maintenance`` imports its dependencies FROM this module, so
# an eager ``from .bucket_maintenance import ...`` here would form a load-time
# import cycle (and break whenever ``bucket_maintenance`` is imported first).
# Deferring to attribute-access time means both modules are fully loaded before
# the lookup resolves. This keeps ``from ...bucket_store import bucket_repair``
# (and the other six verbs) working as part of bucket_store's established
# bucket-subsystem facade, while the implementation lives in its own module.
# ---------------------------------------------------------------------------
_MAINTENANCE_REEXPORTS = frozenset(
    {
        "bucket_repair",
        "bucket_verify",
        "bucket_prune",
        "bucket_prefetch",
        "rebuild_bucket_trail",
        "rebuild_bucket_traces",
        "migrate_bucket_to_v2",
    }
)


def __getattr__(name: str):
    if name in _MAINTENANCE_REEXPORTS:
        from . import bucket_maintenance

        return getattr(bucket_maintenance, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

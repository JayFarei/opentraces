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

import gzip
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from opentraces.core._time import utc_now_str
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlparse

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
    context_layer_blob_path,
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
    _blob_content_matches_path,
    _build_context_head,
    _build_context_layer_blob,
    _context_blob_scope,
    _hash_for_blob_path,
    _head_payload_to_row,
    _iter_context_blob_files,
    _iter_context_tree_head_payloads,
    _layer_id_refs_for_trace,
    _layer_id_refs_from_events_mirror,
    compute_context_tree_status,
    context_tree_snapshot,
    iter_context_tree_traces,
    project_context_tree_to_bucket,
    read_context_tree_head,
    verify_context_tree_layer_refs,
)


TRACE_RECORD_BUCKET_SCHEMA = "opentraces.bucket.trace_record.v1"
TRACE_RECORD_POINTER_SCHEMA = "opentraces.bucket.trace_record_pointer.v1"
TRACE_RECORD_PROJECT_STAGING = "_staging"
RAW_SOURCE_SCHEMA = "opentraces.bucket.raw_source.v1"
RAW_SOURCE_SNAPSHOT_SCHEMA = "opentraces.bucket.raw_sources_snapshot.v1"
TRAIL_EVENT_EXPORT_SCHEMA = "opentraces.bucket.trail_events_export.v1"

# Plan 080 — bucket layout v2. The schema_version on ``bucket/manifest.json`` is
# the load-bearing contract between writer (this module) and remote sync
# (``bucket_remote.py``). Mismatched versions raise ``BucketLayoutError`` on
# read; there is no v1 reader path on this branch.
BUCKET_MANIFEST_SCHEMA = "opentraces.bucket.manifest.v2"
BUCKET_PER_TRACE_SCHEMA = "opentraces.bucket.trace_envelope.v2"
BUCKET_REMOTE_SCHEMA = "opentraces.bucket.fake_remote.v1"


class BucketLayoutError(RuntimeError):
    """Raised when a bucket manifest's schema_version is incompatible.

    Plan 080 Resolution E — every manifest read path checks the
    ``schema_version`` field on ``bucket/manifest.json``. A mismatch raises
    this error with a one-line upgrade hint; there is no v1-reader path on
    this development branch.
    """


@dataclass(frozen=True)
class BucketTraceRecord:
    path: Path
    project_slug: str
    source_layer: str
    trace_id: str
    record_hash: str
    record: TraceRecord
    envelope: dict[str, Any]


@dataclass(frozen=True)
class BucketSyncSummary:
    root: Path
    written: int = 0
    unchanged: int = 0
    removed: int = 0
    skipped: int = 0


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


def project_per_trace_exports(
    repo: Path,
    *,
    project_slug: str,
    trace_id: str,
    record: TraceRecord | None = None,
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

    All gzipped files use ``mtime=0`` (Resolution H — deterministic).
    """

    from .context_tree.contract import (
        CONTEXT_COMPACTION_OBSERVED,
        CONTEXT_LAYER_CAPTURED,
        CONTEXT_NODE_OBSERVED,
        CONTEXT_TREE_RECONCILED,
    )
    from .trails import read_events

    _CONTEXT_EVENT_TYPES = {
        CONTEXT_LAYER_CAPTURED,
        CONTEXT_NODE_OBSERVED,
        CONTEXT_COMPACTION_OBSERVED,
        CONTEXT_TREE_RECONCILED,
    }

    # plan 090: a v2 anchor-search summary event has top-level trace_id=None (it
    # spans the traces it searched). The shared helper fans it into this trace's
    # companion when one of its per-patch results belongs here, so per-trace
    # consumers (which read via iter_search_records) still see their searches.
    # The whole summary event is kept verbatim (not split) so the companion
    # stays faithful to the canonical log. Legacy per-patch events keep a real
    # trace_id and route via the normal trace_id match below.
    from .trails.search_records import summary_search_touches_trace

    # 1. Filter events by trace_id (sequence order preserved).
    trail_events: list[Any] = []
    context_events: list[Any] = []
    try:
        events_iter = read_events(repo, verify=False)
    except Exception:
        events_iter = []
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


def _per_trace_v2_summary(
    project_slug: str, trace_id: str, record: TraceRecord | None
) -> dict[str, Any]:
    """Compute the manifest summary block for one per-trace envelope.

    Plan 080 §4 — drives the ``traces[]`` entries in ``manifest.json``.
    """

    trail_path = trace_v1_trail_path(project_slug, trace_id)
    context_path = trace_v1_context_path(project_slug, trace_id)
    sources_path = trace_v1_sources_path(project_slug, trace_id)
    trace_json = trace_v1_json_path(project_slug, trace_id)

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

    # node_count from per-trace context.jsonl.gz (count distinct node payloads
    # via context_node_observed events). Cheap because file is small.
    node_count = 0
    if has_context:
        try:
            from .context_tree.contract import CONTEXT_NODE_OBSERVED

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


def bucket_repair(*, dry_run: bool = False) -> dict[str, Any]:
    """Full rebuild from canonical (event log + blob store).

    Plan 080 §9 / Resolution G — the documented crash-recovery primitive.
    Walks every opted-in project, re-runs :func:`sync_events_mirror` to
    rebuild the events mirror, re-projects every trace via
    :func:`project_per_trace_exports`, and regenerates the manifest with
    :func:`bucket_manifest`.

    Idempotent: a second invocation on the same canonical state produces
    byte-identical on-disk output (proof: every writer uses
    :func:`_atomic_write_*` helpers that skip same-bytes writes; the
    manifest digest excludes the volatile ``generated_at`` / ``updated_at``
    fields per Resolution H).
    """

    errors: list[dict[str, Any]] = []
    traces_projected = 0
    events_mirrored = 0
    manifest_regenerated = False

    projects = _iter_opted_in_projects()

    for project_path, project_slug in projects:
        # 1. Events mirror rebuild — required before per-trace projection.
        if not dry_run:
            try:
                sync_events_mirror(project_path, repo_id=project_slug)
            except Exception as exc:
                errors.append(
                    {
                        "kind": "events_mirror",
                        "project_slug": project_slug,
                        "detail": str(exc),
                    }
                )

        # 2. Per-trace envelopes for every trace_id seen in the event log.
        trace_ids = _trace_ids_for_project(project_path)
        for trace_id in trace_ids:
            traces_projected += 1
            if dry_run:
                continue
            try:
                project_per_trace_exports(
                    project_path,
                    project_slug=project_slug,
                    trace_id=trace_id,
                )
            except Exception as exc:
                errors.append(
                    {
                        "kind": "per_trace_export",
                        "project_slug": project_slug,
                        "trace_id": trace_id,
                        "detail": str(exc),
                    }
                )

        # 3. Context-tree projection (idempotent; rebuilds head/nodes/blobs).
        if not dry_run:
            try:
                project_context_tree_to_bucket(
                    project_path, project_slug=project_slug
                )
            except Exception as exc:
                errors.append(
                    {
                        "kind": "context_tree_projection",
                        "project_slug": project_slug,
                        "detail": str(exc),
                    }
                )

    # 4. events_mirrored count from the events mirror index (set by the
    # last sync_events_mirror call across all projects). When there's only
    # a single project this is exact; for multi-project buckets the index
    # holds the most recently synced project's batch count.
    idx_path = events_v1_index_path()
    if idx_path.exists():
        try:
            raw_idx = json.loads(idx_path.read_text(encoding="utf-8"))
            if isinstance(raw_idx, dict):
                events_mirrored = int(raw_idx.get("batch_count") or 0)
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    # 5. Regenerate manifest. Write only when not in dry-run mode AND
    # when the content has actually changed — compare ``bucket_digest``
    # (Resolution H: excludes volatile generated_at/updated_at fields).
    # This makes ``bucket_repair`` byte-identical-idempotent: a second run
    # against unchanged canonical state leaves ``manifest.json`` untouched
    # on disk so its raw bytes (including the embedded ``generated_at``
    # timestamp) stay stable.
    if not dry_run:
        try:
            candidate = bucket_manifest(write=False, include_objects=False)
            existing_doc = None
            manifest_path = bucket_manifest_path()
            if manifest_path.exists():
                try:
                    existing_doc = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                except (OSError, ValueError, json.JSONDecodeError):
                    existing_doc = None
            existing_digest = (
                existing_doc.get("bucket_digest")
                if isinstance(existing_doc, dict)
                else None
            )
            if candidate.get("bucket_digest") != existing_digest:
                bucket_manifest(write=True, include_objects=False)
            manifest_regenerated = True
        except Exception as exc:
            errors.append(
                {"kind": "manifest", "project_slug": None, "detail": str(exc)}
            )

    status = "ok" if not errors else "partial"
    return {
        "schema_version": BUCKET_MANIFEST_SCHEMA,
        "status": status,
        "dry_run": dry_run,
        "traces_projected": traces_projected,
        "events_mirrored": events_mirrored,
        "manifest_regenerated": manifest_regenerated,
        "projects_walked": len(projects),
        "errors": errors,
    }


def bucket_verify(
    *,
    sample: int = 100,
    full: bool = False,
) -> dict[str, Any]:
    """Blob content integrity + dangling-ref detection.

    Plan 080 §7 / §20 Resolution G. Three checks:

    1. **Blob content integrity.** For each context blob, recompute the
       hash encoded in the filename and assert the blob's ``layer_id``
       field echoes the same value. ``sample`` (default 100) bounds the
       fast path; ``full=True`` walks every blob.
    2. **Dangling reference detection.** Every ``layer_id`` referenced
       by a per-trace ``context.jsonl.gz`` and the events mirror must
       resolve to an on-disk blob.
    3. **Manifest consistency.** Each trace listed in ``manifest.json``
       must have ``traces/v1/<proj>/<trace>/trace.json`` on disk.

    Returns ``{ok, sampled, errors: [{kind, path, detail}], full}``.
    Never mutates bucket state.
    """

    import random

    errors: list[dict[str, Any]] = []

    # --- 1. Blob content integrity --------------------------------------
    blob_files = list(_iter_context_blob_files())
    if full or sample <= 0:
        sampled_blobs = blob_files
    else:
        if len(blob_files) <= sample:
            sampled_blobs = blob_files
        else:
            # Deterministic sample for the same input set — seed by the
            # sorted filename list so repeated calls return the same N.
            rng = random.Random(",".join(p.name for p in blob_files))
            sampled_blobs = rng.sample(blob_files, sample)
            sampled_blobs.sort()
    for blob_path in sampled_blobs:
        ok, detail = _blob_content_matches_path(blob_path)
        if ok:
            continue
        try:
            rel = blob_path.relative_to(paths.bucket_dir()).as_posix()
        except ValueError:
            rel = str(blob_path)
        errors.append(
            {"kind": "blob_content", "path": rel, "detail": detail or "mismatch"}
        )

    # --- 2. Dangling reference detection --------------------------------
    referenced_layer_ids: set[str] = _layer_id_refs_from_events_mirror()

    # Walk per-trace context.jsonl.gz under traces/v1/.
    traces_root = traces_v1_root()
    if traces_root.exists():
        for trace_json in sorted(traces_root.glob("*/*/trace.json")):
            try:
                proj_slug = unquote(trace_json.parent.parent.name)
                tid = unquote(trace_json.parent.name)
            except Exception:
                continue
            trace_refs = _layer_id_refs_for_trace(proj_slug, tid)
            referenced_layer_ids.update(trace_refs)
            # Per-trace dangling check: each referenced layer_id must have
            # a blob on disk under the project's namespace (or _shared).
            for layer_id in trace_refs:
                proj_path = blobs_v1_context_path(proj_slug, layer_id)
                shared_path = context_layer_blob_path(
                    proj_slug, layer_id, scope="global"
                )
                if proj_path.exists() or shared_path.exists():
                    continue
                try:
                    rel = proj_path.relative_to(paths.bucket_dir()).as_posix()
                except ValueError:
                    rel = str(proj_path)
                errors.append(
                    {
                        "kind": "dangling_blob",
                        "path": rel,
                        "detail": (
                            f"trace {tid!r} references layer_id "
                            f"{layer_id!r} but no blob exists"
                        ),
                    }
                )

    # Events-mirror dangling check: walk every layer_id seen in mirror and
    # verify *some* project has a blob for it (we don't know the project
    # context from the mirror alone, so we look across the blob root).
    if referenced_layer_ids:
        blob_root = blobs_v1_root()
        # Build a fast lookup of all blob filenames -> hash strings.
        on_disk_hashes: set[str] = set()
        if blob_root.exists():
            for blob in blob_root.glob("*/context/*/*.json.gz"):
                expected = _hash_for_blob_path(blob)
                if expected:
                    on_disk_hashes.add(expected)
        for layer_id in sorted(referenced_layer_ids):
            if layer_id in on_disk_hashes:
                continue
            # Already reported per-trace above; report once for events-only refs.
            already_reported = any(
                err.get("kind") == "dangling_blob"
                and layer_id in (err.get("detail") or "")
                for err in errors
            )
            if already_reported:
                continue
            errors.append(
                {
                    "kind": "dangling_blob_events",
                    "path": layer_id,
                    "detail": "events mirror references layer_id with no blob",
                }
            )

    # --- 3. Manifest consistency ----------------------------------------
    manifest_path = bucket_manifest_path()
    if manifest_path.exists():
        try:
            manifest_doc = _load_manifest()
        except BucketLayoutError as exc:
            errors.append(
                {
                    "kind": "manifest_schema",
                    "path": str(manifest_path),
                    "detail": str(exc),
                }
            )
            manifest_doc = None
        if manifest_doc is not None:
            for row in manifest_doc.get("traces") or []:
                if not isinstance(row, dict):
                    continue
                rel_trace_path = row.get("trace_path")
                if not isinstance(rel_trace_path, str) or not rel_trace_path:
                    continue
                abs_path = paths.bucket_dir() / rel_trace_path
                if abs_path.exists():
                    continue
                errors.append(
                    {
                        "kind": "manifest_missing_trace",
                        "path": rel_trace_path,
                        "detail": (
                            f"manifest lists trace {row.get('trace_id')!r} but "
                            f"trace.json is missing"
                        ),
                    }
                )

    return {
        "ok": not errors,
        "sampled": len(sampled_blobs),
        "errors": errors,
        "full": bool(full),
        # Compat keys that older callers (and Section G test) still inspect.
        "schema_version": BUCKET_MANIFEST_SCHEMA,
        "blobs_checked": len(sampled_blobs),
        "dangling_count": sum(
            1 for err in errors if err["kind"].startswith("dangling_blob")
        ),
    }


def bucket_prune(*, dry_run: bool = False) -> dict[str, Any]:
    """Reachability-based orphan-blob cleanup.

    Plan 080 §9 Resolution G — NEVER touches events or ``trace.json``.

    Two passes:

    1. **Reachable blob set.** For every trace listed in ``manifest.json``
       (or, when absent, every trace under ``traces/v1/``), collect every
       layer_id referenced by its ``context.jsonl.gz``. Add every layer_id
       referenced by the events mirror.
    2. **Sweep.** Any context blob whose path-encoded hash is not in the
       reachable set is an orphan. ``.tmp`` files older than 1 hour
       anywhere under ``blobs/v1/`` or ``traces/v1/`` are also removed.

    Returns ``{would_delete, deleted, blobs: [...], tempfiles: [...]}``.
    """

    import time

    deleted_blobs: list[str] = []
    deleted_tempfiles: list[str] = []
    would_delete = 0
    deleted = 0

    # --- 1. Reachable layer set -----------------------------------------
    reachable: set[str] = set()
    reachable.update(_layer_id_refs_from_events_mirror())

    # Pick the trace list from manifest if available; otherwise scan disk.
    manifest_doc: dict[str, Any] | None = None
    manifest_path = bucket_manifest_path()
    if manifest_path.exists():
        try:
            manifest_doc = _load_manifest()
        except BucketLayoutError:
            manifest_doc = None

    if manifest_doc is not None and manifest_doc.get("traces"):
        for row in manifest_doc.get("traces") or []:
            if not isinstance(row, dict):
                continue
            proj_slug = row.get("project_slug")
            tid = row.get("trace_id")
            if not isinstance(proj_slug, str) or not isinstance(tid, str):
                continue
            reachable.update(_layer_id_refs_for_trace(proj_slug, tid))
    else:
        traces_root = traces_v1_root()
        if traces_root.exists():
            for trace_json in sorted(traces_root.glob("*/*/trace.json")):
                proj_slug = unquote(trace_json.parent.parent.name)
                tid = unquote(trace_json.parent.name)
                reachable.update(_layer_id_refs_for_trace(proj_slug, tid))

    # --- 2a. Orphan blob sweep ------------------------------------------
    for blob_path in _iter_context_blob_files():
        hash_str = _hash_for_blob_path(blob_path)
        if hash_str is None:
            # Unknown shape — be conservative; leave alone.
            continue
        if hash_str in reachable:
            continue
        try:
            rel = blob_path.relative_to(paths.bucket_dir()).as_posix()
        except ValueError:
            rel = str(blob_path)
        would_delete += 1
        deleted_blobs.append(rel)
        if dry_run:
            continue
        try:
            blob_path.unlink()
            deleted += 1
        except FileNotFoundError:
            pass

    # --- 2b. Tempfile sweep ---------------------------------------------
    now = time.time()
    one_hour = 60 * 60
    sweep_roots = [blobs_v1_root(), traces_v1_root()]
    for root in sweep_roots:
        if not root.exists():
            continue
        for tmp_path in sorted(root.rglob("*.tmp")):
            if not tmp_path.is_file():
                continue
            # Match the writer's tempfile convention: .{name}.{rand}.tmp .
            if not tmp_path.name.startswith("."):
                continue
            try:
                age = now - tmp_path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age < one_hour:
                continue
            try:
                rel = tmp_path.relative_to(paths.bucket_dir()).as_posix()
            except ValueError:
                rel = str(tmp_path)
            would_delete += 1
            deleted_tempfiles.append(rel)
            if dry_run:
                continue
            try:
                tmp_path.unlink()
                deleted += 1
            except FileNotFoundError:
                pass

    return {
        "schema_version": BUCKET_MANIFEST_SCHEMA,
        "status": "ok",
        "dry_run": dry_run,
        "would_delete": would_delete,
        "deleted": deleted,
        "blobs": deleted_blobs,
        "tempfiles": deleted_tempfiles,
        # Compat keys for existing callers.
        "orphans_removed": len(deleted_blobs) if not dry_run else 0,
        "tempfiles_removed": len(deleted_tempfiles) if not dry_run else 0,
    }


def bucket_prefetch(
    trace_id: str,
    *,
    remote: str | None = None,
    project_slug: str | None = None,
) -> dict[str, Any]:
    """Eager-pull one trace's envelope + blobs from a remote HF bucket.

    Plan 080 §20 Resolution N: writes into the LOCAL bucket directly
    (``bucket/blobs/v1/<project>/context/`` for layer blobs;
    ``bucket/traces/v1/<project>/<trace>/`` for trace.json + companion
    JSONL). Mental model: "warm my bucket from remote." Use before
    ``ctx show`` on a cold cache to avoid per-blob HTTP round-trips.

    Steps:

    1. Resolve target remote repo_id (explicit ``remote`` arg or
       configured ``cfg.bucket.remote.url``).
    2. Resolve target ``project_slug`` (explicit arg or remote manifest
       lookup by ``trace_id``).
    3. Fetch the 4 per-trace envelope files (``trace.json``,
       ``trail.jsonl.gz``, ``context.jsonl.gz``, ``sources.jsonl.gz``).
    4. Walk ``context.jsonl.gz`` for every referenced layer_id, fetch
       each blob into ``bucket/blobs/v1/<project>/context/<hh>/<hash>.json.gz``.
    5. Return ``{trace_id, project_slug, files_fetched, blobs_fetched,
       bytes, status}``.

    Idempotent: re-running on an already-warm cache fetches what's
    missing and is a no-op for files that match the remote.
    """

    from .bucket_remote import BucketRemoteError, _hf_api, _hf_repo_id
    from .config import load_config

    cfg = load_config()
    repo_url = remote if remote is not None else (
        cfg.bucket.remote.url if cfg.bucket.remote and cfg.bucket.remote.enabled else None
    )
    if not repo_url:
        raise BucketRemoteError(
            "no remote configured; pass --remote <hf-repo> or run "
            "'opentraces setup bucket' to configure a remote"
        )
    repo_id = _hf_repo_id(repo_url)
    api = _hf_api(cfg.hf_token)

    # --- 1. Resolve project_slug (via remote manifest if not provided) ---
    resolved_slug: str | None = project_slug
    if resolved_slug is None:
        try:
            manifest_path = Path(
                api.hf_hub_download(
                    repo_id=repo_id,
                    filename="manifest.json",
                    repo_type="dataset",
                )
            )
            manifest_doc = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise BucketRemoteError(
                f"unable to read remote bucket manifest for {repo_id}: {exc}"
            ) from exc
        for row in manifest_doc.get("traces") or []:
            if isinstance(row, dict) and row.get("trace_id") == trace_id:
                resolved_slug = row.get("project_slug")
                break
        if resolved_slug is None:
            raise ValueError(
                f"trace {trace_id!r} not found in remote bucket manifest at {repo_id}"
            )

    # --- 2. Fetch the 4 per-trace envelope files -----------------------
    files_fetched: list[str] = []
    bytes_fetched = 0

    def _fetch_to_local(remote_path: str, local_path: Path) -> int:
        """Download ``remote_path`` and write atomically to ``local_path``.

        Returns the byte count of the downloaded file, or 0 if the
        remote file does not exist.
        """
        try:
            downloaded = Path(
                api.hf_hub_download(
                    repo_id=repo_id,
                    filename=remote_path,
                    repo_type="dataset",
                )
            )
        except Exception:
            return 0
        data = downloaded.read_bytes()
        # Idempotent: if local matches, skip the rewrite.
        if local_path.exists() and local_path.read_bytes() == data:
            return len(data)
        _atomic_write_bytes(local_path, data)
        return len(data)

    proj_slug = resolved_slug
    # Path quoting must match LocalBucketBackend / RemoteHubBackend
    # (safe="-._~" — RFC 3986 unreserved set). Mismatched safe sets
    # produced divergent remote paths for slugs containing -, ., _, ~.
    from .bucket_backend import _hf_layer_blob_path, _hf_traces_path

    envelope_files = [
        (_hf_traces_path(proj_slug, trace_id, "trace.json"), trace_v1_json_path(proj_slug, trace_id)),
        (_hf_traces_path(proj_slug, trace_id, "trail.jsonl.gz"), trace_v1_trail_path(proj_slug, trace_id)),
        (_hf_traces_path(proj_slug, trace_id, "context.jsonl.gz"), trace_v1_context_path(proj_slug, trace_id)),
        (_hf_traces_path(proj_slug, trace_id, "sources.jsonl.gz"), trace_v1_sources_path(proj_slug, trace_id)),
    ]
    for remote_path, local_path in envelope_files:
        n = _fetch_to_local(remote_path, local_path)
        if n > 0:
            files_fetched.append(remote_path)
            bytes_fetched += n

    if not files_fetched:
        raise ValueError(
            f"trace {trace_id!r} (project={proj_slug!r}) has no envelope "
            f"files on remote {repo_id}"
        )

    # --- 3. Walk context.jsonl.gz for layer_id refs --------------------
    layer_ids: set[str] = _layer_id_refs_for_trace(proj_slug, trace_id)

    # --- 4. Fetch each referenced layer blob ---------------------------
    blobs_fetched_count = 0
    for lid in sorted(layer_ids):
        local_blob = blobs_v1_context_path(proj_slug, lid)
        if local_blob.exists():
            # Already warm.
            continue
        remote_blob_path = _hf_layer_blob_path(proj_slug, lid)
        n = _fetch_to_local(remote_blob_path, local_blob)
        if n > 0:
            blobs_fetched_count += 1
            bytes_fetched += n

    return {
        "schema_version": BUCKET_MANIFEST_SCHEMA,
        "status": "ok",
        "trace_id": trace_id,
        "project_slug": proj_slug,
        "repo_id": repo_id,
        "files_fetched": len(files_fetched),
        "blobs_fetched": blobs_fetched_count,
        "bytes": bytes_fetched,
        "layer_refs_seen": len(layer_ids),
    }


def rebuild_bucket_trail() -> dict[str, Any]:
    """Rebuild the events mirror substrate from each opted-in project.

    Plan 080 §7 ``bucket rebuild --substrate trail`` body. Calls
    :func:`sync_events_mirror` once per opted-in project and aggregates the
    per-project envelopes into a single envelope so the CLI can present a
    single ``trail`` block under ``rebuild.per_substrate``.
    """

    projects = _iter_opted_in_projects()
    per_project: list[dict[str, Any]] = []
    events_mirrored = 0
    last_batch_id: str | None = None
    latest_event_sequence = 0
    for project_path, project_slug in projects:
        try:
            index = sync_events_mirror(project_path, repo_id=project_slug)
        except Exception as exc:
            per_project.append(
                {
                    "project_slug": project_slug,
                    "project": str(project_path),
                    "error": str(exc),
                }
            )
            continue
        per_project.append(
            {
                "project_slug": project_slug,
                "project": str(project_path),
                "batch_count": int(index.get("batch_count") or 0),
                "batches_written": int(index.get("batches_written") or 0),
                "latest_event_sequence": int(
                    index.get("latest_event_sequence") or 0
                ),
                "last_batch_id": index.get("last_batch_id"),
                "state": index.get("state"),
            }
        )
        events_mirrored += int(index.get("batch_count") or 0)
        seq = int(index.get("latest_event_sequence") or 0)
        if seq > latest_event_sequence:
            latest_event_sequence = seq
        if index.get("last_batch_id"):
            last_batch_id = index.get("last_batch_id")

    return {
        "schema_version": BUCKET_EVENTS_INDEX_SCHEMA,
        "substrate": "trail",
        "projects_walked": len(projects),
        "events_mirrored": events_mirrored,
        "latest_event_sequence": latest_event_sequence,
        "last_batch_id": last_batch_id,
        "per_project": per_project,
        "idempotent_noop": all(
            int(row.get("batches_written") or 0) == 0 for row in per_project
        ),
    }


def rebuild_bucket_traces() -> dict[str, Any]:
    """Rebuild every per-trace envelope from each opted-in project.

    Plan 080 §7 ``bucket rebuild --substrate traces`` body. Walks each
    project's event log, collects trace_ids, and calls
    :func:`project_per_trace_exports` for each. Idempotent: byte-identical
    output on a second call (atomic-write helpers skip same-bytes writes).
    """

    projects = _iter_opted_in_projects()
    per_project: list[dict[str, Any]] = []
    envelopes_written = 0
    for project_path, project_slug in projects:
        trace_ids = _trace_ids_for_project(project_path)
        project_envelopes = 0
        errors: list[dict[str, Any]] = []
        for tid in trace_ids:
            try:
                project_per_trace_exports(
                    project_path,
                    project_slug=project_slug,
                    trace_id=tid,
                )
                project_envelopes += 1
            except Exception as exc:
                errors.append({"trace_id": tid, "detail": str(exc)})
        envelopes_written += project_envelopes
        per_project.append(
            {
                "project_slug": project_slug,
                "project": str(project_path),
                "trace_count": len(trace_ids),
                "envelopes_written": project_envelopes,
                "errors": errors,
            }
        )

    return {
        "schema_version": BUCKET_PER_TRACE_SCHEMA,
        "substrate": "traces",
        "projects_walked": len(projects),
        "envelopes_written": envelopes_written,
        "per_project": per_project,
        "idempotent_noop": envelopes_written == 0,
    }


def migrate_bucket_to_v2(
    *,
    bucket_root: Path,
    bucket_v2_path: Path,
    from_layout: str,
) -> dict[str, Any]:
    """Migrate an existing legacy bucket into the plan-080 v2 layout.

    Plan 080 §15(a) — write-new-and-swap. The legacy bucket at
    ``bucket_root`` is left intact while a fresh v2 layout is written
    under ``bucket_v2_path``; after a consistency check the new directory
    is atomically swapped in (the legacy tree is renamed aside with a
    ``.legacy-<timestamp>`` suffix so it can be removed manually).

    ``from_layout`` is one of:

    - ``"v1_plan79"``  — has ``bucket/contexts/v1/`` (plan 079 layout).
    - ``"v1_pre79"``   — has ``bucket/events/trail/v1/`` or
                          ``bucket/objects/traces/v1/`` (pre-plan-079).

    The reconstruction is canonical-driven: per-project Git event logs are
    the source of truth, so the new bucket is rebuilt the same way
    :func:`bucket_repair` would build it from scratch. Legacy files (raw
    sources, TraceRecord envelopes) are copied verbatim when present.
    Returns ``{traces_migrated, blobs_migrated, status, from_layout,
    to_layout}``.
    """

    if from_layout not in {"v1_plan79", "v1_pre79"}:
        # Empty / already-v2 buckets short-circuit in the CLI layer; this
        # function should never be called for them.
        return {
            "status": "noop",
            "from_layout": from_layout,
            "to_layout": "v2",
            "traces_migrated": 0,
            "blobs_migrated": 0,
            "detail": f"unsupported from_layout {from_layout!r}",
        }

    bucket_root = Path(bucket_root)
    bucket_v2_path = Path(bucket_v2_path)

    # Stage area: build the v2 layout in a sibling directory using the
    # standard bucket writers. We temporarily redirect ``paths.bucket_dir``
    # by writing into ``bucket_v2_path`` directly via a swap.
    if bucket_v2_path.exists():
        shutil.rmtree(bucket_v2_path)
    bucket_v2_path.mkdir(parents=True, exist_ok=True)

    # Copy legacy artifacts that are still meaningful in the v2 layout
    # before we run the rebuilders. Two categories:
    #   - ``objects/traces/v1/`` (legacy TraceRecord envelopes) — the v2
    #     spine lives under ``traces/v1/`` but the legacy mirror is
    #     preserved at the same relative path for compat reads.
    #   - ``objects/raw/v1/`` (raw source artifacts).
    blobs_migrated = 0
    legacy_objects_dir = bucket_root / "objects"
    if legacy_objects_dir.exists():
        blobs_migrated += _copy_bucket_tree(
            legacy_objects_dir,
            bucket_v2_path / "objects",
        )

    # Copy ``contexts/v1/`` (plan 079 projected nodes/heads) verbatim so
    # the bucket retains its plan-079 read paths. The v2 writer will also
    # emit fresh files under ``traces/v1/`` and ``blobs/v1/`` from the
    # canonical event log.
    if (bucket_root / "contexts").exists():
        blobs_migrated += _copy_bucket_tree(
            bucket_root / "contexts",
            bucket_v2_path / "contexts",
        )

    # Atomic swap: rename legacy root aside, move new root into place.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    legacy_aside = bucket_root.with_name(
        f"{bucket_root.name}.legacy-{timestamp}"
    )
    if bucket_root.exists():
        bucket_root.rename(legacy_aside)
    bucket_v2_path.rename(bucket_root)

    # Now that ``bucket_dir()`` points at the new content, rebuild
    # canonical-driven artifacts (events mirror + per-trace envelopes +
    # manifest) by running ``bucket_repair`` end-to-end. This is the same
    # body the user could run manually post-migration.
    repair = bucket_repair(dry_run=False)
    traces_migrated = int(repair.get("traces_projected") or 0)

    status = "ok" if not repair.get("errors") else "partial"

    return {
        "status": status,
        "from_layout": from_layout,
        "to_layout": "v2",
        "traces_migrated": traces_migrated,
        "blobs_migrated": blobs_migrated,
        "legacy_aside": str(legacy_aside),
        "errors": repair.get("errors") or [],
    }


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


def bucket_manifest(
    *,
    write: bool = False,
    include_objects: bool = False,
) -> dict[str, Any]:
    """Return the local bucket manifest used by future remote sync.

    The manifest is intentionally transport-neutral: it summarizes the
    canonical bucket substrate and the local projections that remote sync must
    treat as derived state.
    """

    trace_snapshot = trace_record_snapshot(include_objects=include_objects)
    objects = trace_snapshot.get("objects") or []
    if not include_objects:
        objects = [
            {
                "privacy_tier": obj.envelope.get("security", {}).get("privacy_tier"),
                "security_version": obj.envelope.get("security", {}).get("security_version"),
                "syncable": obj.envelope.get("security", {}).get("syncable"),
                "security_stale": obj.envelope.get("security", {}).get("stale"),
                "written_at": obj.envelope.get("written_at"),
            }
            for obj in iter_trace_record_objects()
        ]
    syncable_count = sum(1 for obj in objects if obj.get("syncable") is True)
    stale_security_count = sum(1 for obj in objects if obj.get("security_stale") is True)
    privacy_off_count = sum(1 for obj in objects if obj.get("privacy_tier") == "off")
    unfiltered_count = sum(1 for obj in objects if obj.get("syncable") is False)
    last_trace_record_write_at = max(
        (str(obj.get("written_at") or "") for obj in objects),
        default="",
    ) or None

    raw_snapshot = raw_source_snapshot(include_objects=include_objects)
    trail_event_exports = trail_event_snapshot(include_objects=include_objects)
    context_trees_snapshot = context_tree_snapshot(include_objects=include_objects)

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
    bucket_digest_material = {
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
        "context_trees": manifest["context_trees"],
        "sync": manifest["sync"],
    }
    bucket_digest = _digest_payload(bucket_digest_material)
    manifest["bucket_digest"] = bucket_digest
    # Retain ``digest`` as a compat alias for callers that still consume the
    # v1 field name (bucket_remote, datasets).
    manifest["digest"] = bucket_digest
    if write:
        _atomic_write_json(bucket_manifest_path(), manifest)
    return manifest


def bucket_status(*, write_manifest: bool = True) -> dict[str, Any]:
    manifest = bucket_manifest(write=write_manifest, include_objects=False)
    return {
        "status": "ok",
        "bucket": manifest,
        "config": _bucket_config_payload(),
    }


def fake_remote_root() -> Path | None:
    raw = os.environ.get("OPENTRACES_FAKE_BUCKET_REMOTE_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    try:
        from .config import load_config

        remote = load_config().bucket.remote
    except Exception:
        return None
    if not remote.enabled or remote.provider != "fake" or not remote.url:
        return None
    parsed = urlparse(remote.url)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path)).expanduser().resolve()
    return Path(remote.url).expanduser().resolve()


def fake_remote_status(remote_root: Path | None = None) -> dict[str, Any]:
    root = remote_root or fake_remote_root()
    if root is None:
        return {
            "schema_version": BUCKET_REMOTE_SCHEMA,
            "state": "unconfigured",
            "advice": "set OPENTRACES_FAKE_BUCKET_REMOTE_ROOT",
        }
    manifest_path = root / "manifest.json"
    local = bucket_manifest(write=True, include_objects=False)
    if not manifest_path.exists():
        return {
            "schema_version": BUCKET_REMOTE_SCHEMA,
            "state": "missing",
            "remote_root": str(root),
            "local_digest": local.get("digest"),
            "remote_digest": None,
        }
    try:
        remote = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {
            "schema_version": BUCKET_REMOTE_SCHEMA,
            "state": "error",
            "remote_root": str(root),
            "error": str(exc),
            "local_digest": local.get("digest"),
            "remote_digest": None,
        }
    remote_digest = remote.get("digest")
    relation = classify_bucket_remote_state(
        provider="fake",
        target=str(root),
        local_digest=local.get("digest"),
        remote_digest=remote_digest,
    )
    return {
        "schema_version": BUCKET_REMOTE_SCHEMA,
        "state": relation["state"],
        "remote_root": str(root),
        "local_digest": local.get("digest"),
        "remote_digest": remote_digest,
        "last_sync_digest": relation.get("last_sync_digest"),
        "remote_updated_at": remote.get("updated_at"),
    }


def fake_remote_diff(remote_root: Path | None = None) -> dict[str, Any]:
    status = fake_remote_status(remote_root)
    return {
        **status,
        "different": status.get("state")
        in {"missing", "different", "local_ahead", "remote_ahead", "diverged", "error"},
    }


def fake_remote_push(remote_root: Path | None = None, *, force: bool = False) -> dict[str, Any]:
    root = remote_root or fake_remote_root()
    if root is None:
        raise ValueError("set OPENTRACES_FAKE_BUCKET_REMOTE_ROOT")
    local_bucket = paths.bucket_dir()
    root.mkdir(parents=True, exist_ok=True)
    manifest = bucket_manifest(write=True, include_objects=False)
    sync = manifest.get("sync") or {}
    if sync.get("eligible") is not True:
        reasons = ", ".join(str(reason) for reason in sync.get("blocked_reasons") or [])
        raise ValueError(
            "bucket is not eligible for remote sync"
            + (f": {reasons}" if reasons else "")
        )
    status = fake_remote_status(root)
    if status.get("state") in {"remote_ahead", "diverged"} and not force:
        raise ValueError(
            "remote bucket has changes that are not in the local bucket; "
            "pull first or pass --force to overwrite"
        )
    copied = _copy_bucket_tree(local_bucket, root)
    _atomic_write_json(root / "manifest.json", manifest)
    write_bucket_sync_state(
        provider="fake",
        target=str(root),
        digest=manifest.get("digest"),
        remote_digest=manifest.get("digest"),
        direction="push",
    )
    return {
        "schema_version": BUCKET_REMOTE_SCHEMA,
        "state": "pushed",
        "remote_root": str(root),
        "digest": manifest.get("digest"),
        "files_copied": copied,
    }


def fake_remote_pull(remote_root: Path | None = None, *, force: bool = False) -> dict[str, Any]:
    root = remote_root or fake_remote_root()
    if root is None:
        raise ValueError("set OPENTRACES_FAKE_BUCKET_REMOTE_ROOT")
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"fake bucket remote is missing: {root}")
    local_bucket = paths.bucket_dir()
    if root.resolve() == local_bucket.resolve():
        raise ValueError("fake remote root must be separate from the local bucket")
    status = fake_remote_status(root)
    if status.get("state") in {"local_ahead", "diverged"} and not force:
        raise ValueError(
            "local bucket has changes that are not in the remote bucket; "
            "push first or pass --force to overwrite"
        )
    if local_bucket.exists():
        shutil.rmtree(local_bucket)
    local_bucket.mkdir(parents=True, exist_ok=True)
    copied = _copy_bucket_tree(root, local_bucket, skip_names={"manifest.json"})
    manifest = bucket_manifest(write=True, include_objects=False)
    write_bucket_sync_state(
        provider="fake",
        target=str(root),
        digest=manifest.get("digest"),
        remote_digest=manifest.get("digest"),
        direction="pull",
    )
    return {
        "schema_version": BUCKET_REMOTE_SCHEMA,
        "state": "pulled",
        "remote_root": str(root),
        "digest": manifest.get("digest"),
        "files_copied": copied,
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

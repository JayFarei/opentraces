"""Local bucket-shaped trace substrate.

The bucket is the local mirror of the future remote sync substrate. It stores
content-addressed trace evidence, optional raw source artifacts, portable
Trace Trail event exports, and rebuildable projections; versioned datasets
stay outside the bucket as HF-shaped repositories.
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
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


TRACE_RECORD_BUCKET_SCHEMA = "opentraces.bucket.trace_record.v1"
TRACE_RECORD_POINTER_SCHEMA = "opentraces.bucket.trace_record_pointer.v1"
TRACE_RECORD_PROJECT_STAGING = "_staging"
RAW_SOURCE_SCHEMA = "opentraces.bucket.raw_source.v1"
RAW_SOURCE_SNAPSHOT_SCHEMA = "opentraces.bucket.raw_sources_snapshot.v1"
TRAIL_EVENT_EXPORT_SCHEMA = "opentraces.bucket.trail_events_export.v1"
TRAIL_EVENT_SNAPSHOT_SCHEMA = "opentraces.bucket.trail_events_snapshot.v1"

# Plan 080 — bucket layout v2. The schema_version on ``bucket/manifest.json`` is
# the load-bearing contract between writer (this module) and remote sync
# (``bucket_remote.py``). Mismatched versions raise ``BucketLayoutError`` on
# read; there is no v1 reader path on this branch.
BUCKET_MANIFEST_SCHEMA = "opentraces.bucket.manifest.v2"
BUCKET_EVENTS_INDEX_SCHEMA = "opentraces.bucket.events.v2"
BUCKET_PER_TRACE_SCHEMA = "opentraces.bucket.trace_envelope.v2"
BUCKET_REMOTE_SCHEMA = "opentraces.bucket.fake_remote.v1"

# Plan 079 — first-class Context Tree bucket projection (compat aliases).
CONTEXT_TREE_BUCKET_SCHEMA = "opentraces.bucket.context_tree.v1"
CONTEXT_LAYER_BLOB_SCHEMA = "opentraces.bucket.context_layer_blob.v1"
CONTEXT_TREE_SNAPSHOT_SCHEMA = "opentraces.bucket.context_trees_snapshot.v1"
CONTEXT_TREE_REMOTE_SYNC_BLOCKER = "context_tree_unfiltered_layers"


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
        "synced_at": _utc_now(),
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
        "written_at": _utc_now(),
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
        "written_at": _utc_now(),
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


def sync_events_mirror(
    repo: Path,
    *,
    repo_id: str,
) -> dict[str, Any]:
    """Mirror the repo-local TrailEvent Git ref into ``bucket/events/v1/`` (plan 080 §4).

    Per Resolution B, this hard-cuts the legacy ``bucket/events/trail/v1/``
    layout. Each Git batch becomes one ``batches/<seq>-<batch-id>.jsonl.gz``
    file (gzip-deterministic) and the ``index.json`` head records the
    aggregate counters consumed by remote sync and ``bucket repair``.

    Idempotent: a tick that finds no new batches writes nothing (and skips
    rewriting index.json when the counters are unchanged).
    """

    from .trails import EVENT_LOG_REF, event_log_status, read_events

    status = event_log_status(repo)
    if status.get("state") == "missing":
        index = {
            "schema_version": BUCKET_EVENTS_INDEX_SCHEMA,
            "repo_id": repo_id,
            "event_log_ref": EVENT_LOG_REF,
            "event_log_head": None,
            "batch_count": 0,
            "last_batch_id": None,
            "latest_event_sequence": 0,
            "state": "missing",
            "updated_at": _utc_now(),
        }
        _atomic_write_json(events_v1_index_path(), index)
        return index

    from .trails.event_log import _is_ancestor

    events = sorted(read_events(repo, verify=False), key=lambda e: e.event_sequence)

    # Incremental fast-path: existing batch files are immutable (one batch per
    # append, content-addressed), so when the prior index head is an ancestor
    # of the current head we only group + write batches for events appended
    # since. New batches always sort after existing ones, so their ordinals
    # (and therefore filenames + contents) match a full rebuild byte-for-byte
    # — keeping the replay-equals-git invariant.
    index_path = events_v1_index_path()
    prior_index: dict[str, Any] | None = None
    if index_path.exists():
        try:
            prior_index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            prior_index = None
    prior_head = prior_index.get("event_log_head") if prior_index else None
    prior_seq = int(prior_index.get("latest_event_sequence", 0)) if prior_index else 0
    prior_batch_count = int(prior_index.get("batch_count", 0)) if prior_index else 0
    incremental = (
        prior_index is not None
        and isinstance(prior_head, str)
        and events_v1_batches_dir().exists()
        and _is_ancestor(repo, prior_head, status.get("head") or prior_head)
    )

    if incremental and prior_head == status.get("head"):
        # Nothing new since the last mirror; leave the bucket untouched.
        return prior_index

    work_events = (
        [e for e in events if e.event_sequence > prior_seq]
        if incremental else events
    )
    seq_offset = prior_batch_count if incremental else 0

    # Group the events we need to write by batch_id, in sequence order.
    batch_order: list[str] = []
    by_batch: dict[str, list[Any]] = {}
    for event in work_events:
        bid = event.batch_id
        if bid not in by_batch:
            by_batch[bid] = []
            batch_order.append(bid)
        by_batch[bid].append(event)

    batches_dir = events_v1_batches_dir()
    batches_dir.mkdir(parents=True, exist_ok=True)

    batches_written = 0
    last_batch_id: str | None = (
        prior_index.get("last_batch_id") if incremental else None
    )
    latest_event_sequence = prior_seq if incremental else 0
    for offset, bid in enumerate(batch_order, start=1):
        seq = seq_offset + offset
        batch_events = by_batch[bid]
        # Filename: <seq>-<batch-id>.jsonl.gz; seq is zero-padded for sort.
        safe_bid = _path_part(bid)
        filename = f"{seq:012d}-{safe_bid}.jsonl.gz"
        path = batches_dir / filename
        lines = [
            _canonical_json(event.model_dump(mode="json"))
            for event in sorted(batch_events, key=lambda e: e.event_sequence)
        ]
        body = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
        compressed = _gzip_deterministic(body)
        if not (path.exists() and path.read_bytes() == compressed):
            _atomic_write_bytes(path, compressed)
            batches_written += 1
        last_batch_id = bid
        for ev in batch_events:
            if ev.event_sequence > latest_event_sequence:
                latest_event_sequence = ev.event_sequence

    index = {
        "schema_version": BUCKET_EVENTS_INDEX_SCHEMA,
        "repo_id": repo_id,
        "event_log_ref": EVENT_LOG_REF,
        "event_log_head": status.get("head"),
        "batch_count": seq_offset + len(batch_order),
        "last_batch_id": last_batch_id,
        "latest_event_sequence": latest_event_sequence,
        "state": status.get("state"),
        "updated_at": _utc_now(),
        "verification": {
            "batch_count": status.get("batch_count"),
            "batch_parents_linear": status.get("batch_parents_linear"),
            "content_hashes_valid": status.get("content_hashes_valid"),
            "event_chain_valid": status.get("event_chain_valid"),
        },
        "batches_written": batches_written,
    }
    _atomic_write_json(events_v1_index_path(), index)
    return index


# Compat alias for existing call sites (ingest.py / watcher/daemon.py).
# These will be retired when those tracks merge.
def sync_trail_events_from_repo(repo: Path, *, repo_id: str) -> dict[str, Any]:
    """Deprecated alias for :func:`sync_events_mirror`. Plan 080 Resolution B."""

    return sync_events_mirror(repo, repo_id=repo_id)


def read_events_mirror_batches() -> Iterator[Any]:
    """Yield decompressed ``TrailEvent`` instances from the v2 event-log mirror.

    Walks ``bucket/events/v1/batches/*.jsonl.gz`` in sequence-prefix order,
    decompressing each batch and yielding events in original order. Raises
    ``FileNotFoundError`` if the mirror is missing entirely.
    """

    from .trails import TrailEvent

    index_path = events_v1_index_path()
    if not index_path.exists():
        raise FileNotFoundError(
            f"no v2 events mirror found at {index_path}; run "
            f"'opentraces setup watcher tick' or 'opentraces bucket repair'"
        )
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable events mirror index: {exc}") from exc
    if index.get("schema_version") != BUCKET_EVENTS_INDEX_SCHEMA:
        raise BucketLayoutError(
            f"events mirror schema {index.get('schema_version')!r} incompatible with "
            f"local {BUCKET_EVENTS_INDEX_SCHEMA!r}; run "
            f"'opentraces bucket repair'"
        )
    batches_dir = events_v1_batches_dir()
    if not batches_dir.exists():
        return
    for batch_path in sorted(batches_dir.glob("*.jsonl.gz")):
        try:
            raw = _read_gzip_bytes(batch_path).decode("utf-8")
        except (OSError, gzip.BadGzipFile) as exc:
            raise ValueError(f"unreadable events mirror batch {batch_path}: {exc}") from exc
        for line in raw.splitlines():
            if not line.strip():
                continue
            yield TrailEvent.model_validate_json(line)


# Compat alias for callers still on the v1 export reader signature. Returns
# (head, events) like the old function. The head shape uses the v2 fields so
# downstream code can dispatch on schema_version.
def read_trail_event_export(repo_id: str | None = None) -> tuple[dict[str, Any], list[Any]]:
    """Deprecated alias for :func:`read_events_mirror_batches`.

    Returns the v2 index head plus a list of decompressed ``TrailEvent``
    instances, matching the legacy ``(head, events)`` tuple shape used by
    ``bucket replay`` callers.
    """

    index_path = events_v1_index_path()
    if not index_path.exists():
        raise FileNotFoundError(
            f"no v2 events mirror found for repo_id={repo_id!r}; run "
            f"'opentraces setup watcher tick' or 'opentraces bucket repair'"
        )
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if index.get("schema_version") != BUCKET_EVENTS_INDEX_SCHEMA:
        raise BucketLayoutError(
            f"events mirror schema {index.get('schema_version')!r} incompatible with "
            f"local {BUCKET_EVENTS_INDEX_SCHEMA!r}; run "
            f"'opentraces bucket repair'"
        )
    events = list(read_events_mirror_batches())
    return index, events


def restore_trail_events_to_repo(
    repo: Path,
    *,
    repo_id: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Restore bucket-exported Trace Trails into a supplied Git repository.

    Plan 080: reads from the v2 events mirror (``bucket/events/v1/``).
    """

    from .trails import import_event_log

    head, events = read_trail_event_export(repo_id)
    if not events:
        return {
            "state": "empty",
            "repo": str(repo),
            "repo_id": head.get("repo_id"),
            "event_count": 0,
            "events_imported": 0,
        }
    imported = import_event_log(repo, events, writer="bucket-restore", force=force)
    return {
        **imported,
        "repo": str(repo),
        "repo_id": head.get("repo_id"),
        "export_event_log_head": head.get("event_log_head"),
    }


def trail_event_snapshot(*, include_objects: bool = False) -> dict[str, Any]:
    """Return a deterministic snapshot of the v2 events mirror (plan 080).

    Reads ``bucket/events/v1/index.json``. The legacy
    ``bucket/events/trail/v1`` layout is retired; this snapshot is kept
    only as a manifest contributor for compat callers.
    """

    index_path = events_v1_index_path()
    head: dict[str, Any] | None = None
    if index_path.exists():
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            if (
                isinstance(payload, dict)
                and payload.get("schema_version") == BUCKET_EVENTS_INDEX_SCHEMA
            ):
                head = payload
        except (OSError, ValueError, json.JSONDecodeError):
            head = None

    heads_rows: list[dict[str, Any]] = []
    if head is not None:
        heads_rows.append(
            {
                "path": "events/v1/index.json",
                "repo_id": head.get("repo_id"),
                "event_log_head": head.get("event_log_head"),
                "event_count": int(head.get("latest_event_sequence") or 0),
                "batch_count": int(head.get("batch_count") or 0),
                "state": head.get("state"),
                "updated_at": head.get("updated_at"),
                **({"object": head} if include_objects else {}),
            }
        )

    digest_material = [
        (
            f"{item.get('path')} {item.get('event_log_head')} "
            f"{item.get('event_count')} {item.get('batch_count')} {item.get('state')}"
        )
        for item in heads_rows
    ]
    snapshot: dict[str, Any] = {
        "schema_version": TRAIL_EVENT_SNAPSHOT_SCHEMA,
        "root": str(events_v1_root()),
        "repository_count": len(heads_rows),
        "event_count": sum(int(item.get("event_count") or 0) for item in heads_rows),
        "batch_count": sum(int(item.get("batch_count") or 0) for item in heads_rows),
        "digest": _digest_payload(digest_material),
    }
    if include_objects:
        snapshot["objects"] = heads_rows
    return snapshot


# --------------------------------------------------------------------------- #
# Plan 079 — Context Tree bucket projection (writer + reader API)
# --------------------------------------------------------------------------- #
#
# This section owns the single writer for ``bucket/contexts/v1/``. The
# canonical event log under ``refs/opentraces/local/events/v1`` is the
# source of truth; this projection rebuilds the bucket layout from those
# events on every call. Failure semantics: bucket write fails => event
# log unaffected => next call rebuilds. Per plan 079, Context Tree blobs
# are not remote-syncable by default (closed gate). Per the adversarial
# review's Condition 1, layer blobs are scoped per-project unless the
# user opts in to ``layer_blob_scope="global"``.

# Branch types ordered for deterministic on-disk nodes.jsonl sort.
_BRANCH_TYPE_ORDINAL: dict[str, int] = {
    "root": 0,
    "linear": 1,
    "compaction_fork": 2,
    "subagent_fork": 3,
    "rewind_branch": 4,
    "manual_branch": 5,
}


def _context_blob_scope() -> str:
    """Resolve the active layer blob scope from config, with a safe default."""

    try:
        from .config import load_config

        return load_config().bucket.contexts.layer_blob_scope
    except Exception:
        return "project"


def _build_context_layer_blob(layer: Any) -> dict[str, Any]:
    """Wrap a ContextLayer into the bucket-shaped blob envelope.

    Plan 080 Resolution H: ``written_at`` is dropped from the blob payload —
    layer blobs are pure content (``{layer_id, layer_type, capture_method,
    completeness, content}``). Provenance lives in the event log.
    """

    return {
        "schema_version": CONTEXT_LAYER_BLOB_SCHEMA,
        "layer_id": layer.layer_id,
        "layer_type": layer.layer_type,
        "capture_method": layer.capture_method,
        "completeness": layer.completeness,
        "content": layer.content,
    }


def _build_context_head(
    *,
    project_slug: str,
    trace_id: str,
    node_ids: list[str],
    layer_refs: list[str],
    capture_methods: list[str],
    active_leaf_node_id: str | None,
    subagent_session_ids: list[str],
    capture_limitations: list[str],
    blob_scope: str,
    event_log_head: str | None,
    events_processed_through_sequence: int,
) -> dict[str, Any]:
    """Assemble the head.json envelope and compute its self-digest."""

    payload = {
        "schema_version": CONTEXT_TREE_BUCKET_SCHEMA,
        "project_slug": project_slug,
        "trace_id": trace_id,
        "node_count": len(node_ids),
        "layer_count": len(layer_refs),
        "layer_refs": layer_refs,
        "capture_methods": capture_methods,
        "active_leaf_node_id": active_leaf_node_id,
        "subagent_session_ids": subagent_session_ids,
        "capture_limitations": capture_limitations,
        "remote_sync": {
            "eligible": False,
            "scope": "private_bucket_only",
            "publishable": False,
            "blocked_reasons": [CONTEXT_TREE_REMOTE_SYNC_BLOCKER],
        },
        "blob_scope": blob_scope,
        "last_projection_at": _utc_now(),
        "event_log_head": event_log_head,
        "events_processed_through_sequence": events_processed_through_sequence,
    }
    payload["digest"] = _digest_payload(payload)
    return payload


def project_context_tree_to_bucket(
    repo: Path,
    *,
    project_slug: str,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Project Context Tree events from the canonical event log into the bucket.

    Reads ``read_events(repo)`` through ``build_context_tree_projection``.
    For each trace (or just ``trace_id`` if given) writes layer blobs,
    nodes.jsonl, optional reconciliation.json, then atomically writes
    head.json LAST so a partial run never points at missing blobs.

    See plan 079 §"Writer" for the full contract.
    """

    from .context_tree.contract import CONTEXT_TREE_RECONCILED
    from .context_tree.query import build_context_tree_projection
    from .trails import event_log_status, read_events

    # When a single trace is targeted (the per-session ingest path), build
    # only that trace's projection instead of re-walking the whole history.
    projection = build_context_tree_projection(repo, trace_id=trace_id)
    status = event_log_status(repo)
    event_log_head = status.get("head")
    blob_scope = _context_blob_scope()

    if trace_id is not None:
        target_trace_ids = [trace_id] if trace_id in projection.nodes_by_trace else []
    else:
        target_trace_ids = sorted(projection.nodes_by_trace.keys())

    # Precompute reconciled payload + max event sequence per trace.
    # NOTE: ``build_context_tree_projection`` above already called
    # ``read_events(repo)`` with the default ``verify=True``. We share that
    # cache key here to avoid a second full event-log walk per projection.
    reconciled_by_trace: dict[str, dict[str, Any]] = {}
    max_seq_by_trace: dict[str, int] = {tid: 0 for tid in target_trace_ids}
    target_set = set(target_trace_ids)
    for event in read_events(repo):
        ev_trace_id = event.trace_id or (event.payload.get("trace_id") if isinstance(event.payload, dict) else None)
        if not ev_trace_id or ev_trace_id not in target_set:
            continue
        if event.event_sequence > max_seq_by_trace.get(ev_trace_id, 0):
            max_seq_by_trace[ev_trace_id] = event.event_sequence
        if event.event_type == CONTEXT_TREE_RECONCILED:
            reconciled_by_trace[ev_trace_id] = dict(event.payload or {})

    blobs_written = 0
    blobs_unchanged = 0
    heads_written = 0
    heads_unchanged = 0

    for tid in target_trace_ids:
        node_ids = list(projection.nodes_by_trace.get(tid, []))
        nodes = [projection.nodes_by_id[nid] for nid in node_ids if nid in projection.nodes_by_id]
        if not nodes:
            continue

        # Collect referenced layer ids (sorted dedup).
        ref_set: set[str] = set()
        for n in nodes:
            ref_set.update({
                n.system_layer_id,
                n.messages_layer_id,
                n.tool_registry_layer_id,
                n.runtime_state_layer_id,
            })
        layer_refs = sorted(ref_set)

        # Per-layer capture_methods (sorted dedup across all referenced layers).
        capture_methods_set: set[str] = set()
        for lid in layer_refs:
            layer = projection.layers_by_id.get(lid)
            if layer is None:
                continue
            capture_methods_set.add(layer.capture_method)
        capture_methods = sorted(capture_methods_set)

        # 1. Write layer blobs first (content-addressed; no-op when present).
        for lid in layer_refs:
            layer = projection.layers_by_id.get(lid)
            if layer is None:
                continue
            blob_path = context_layer_blob_path(project_slug, lid, scope=blob_scope)
            blob_payload = _build_context_layer_blob(layer)
            blob_bytes = _canonical_json(blob_payload, pretty=True).encode("utf-8")
            if blob_path.exists():
                try:
                    existing = json.loads(_read_gzip_bytes(blob_path).decode("utf-8"))
                except (OSError, ValueError, gzip.BadGzipFile, json.JSONDecodeError):
                    existing = None
                if isinstance(existing, dict) and existing == blob_payload:
                    blobs_unchanged += 1
                    continue
            _atomic_write_gzip(blob_path, blob_bytes)
            blobs_written += 1

        # 2. nodes.jsonl — full deterministic rewrite, sorted.
        sorted_nodes = sorted(
            nodes,
            key=lambda n: (
                _BRANCH_TYPE_ORDINAL.get(n.branch_type, 99),
                n.step_index if n.step_index is not None else 1_000_000_000,
                n.node_id,
            ),
        )
        nodes_jsonl_text = "\n".join(
            _canonical_json(n.model_dump(mode="json")) for n in sorted_nodes
        )
        if nodes_jsonl_text:
            nodes_jsonl_text += "\n"
        _atomic_write_text(
            context_tree_nodes_path(project_slug, tid),
            nodes_jsonl_text,
        )

        # 3. reconciliation.json (only when a reconciled event exists).
        reconciled = reconciled_by_trace.get(tid)
        recon_path = context_tree_reconciliation_path(project_slug, tid)
        if reconciled is not None:
            reconciliation_payload = {
                "schema_version": CONTEXT_TREE_BUCKET_SCHEMA,
                "trace_id": tid,
                **reconciled,
            }
            _atomic_write_json(recon_path, reconciliation_payload)

        # 4. Determine head + active leaf metadata for the head.json envelope.
        active_leaf_uuid = projection.active_leaves_by_trace.get(tid)
        active_leaf_node_id: str | None = None
        if active_leaf_uuid:
            leaf_node = projection.node_for_transcript_uuid(tid, active_leaf_uuid)
            if leaf_node is not None:
                active_leaf_node_id = leaf_node.node_id
        if active_leaf_node_id is None and reconciled and reconciled.get("active_path_leaf_id"):
            active_leaf_node_id = reconciled.get("active_path_leaf_id")

        subagent_session_ids = sorted(set(
            projection.subagent_session_ids_by_trace.get(tid, []) or []
        ))
        capture_limitations = sorted(set(
            projection.capture_limitations_by_trace.get(tid, []) or []
        ))

        head_payload = _build_context_head(
            project_slug=project_slug,
            trace_id=tid,
            node_ids=node_ids,
            layer_refs=layer_refs,
            capture_methods=capture_methods,
            active_leaf_node_id=active_leaf_node_id,
            subagent_session_ids=subagent_session_ids,
            capture_limitations=capture_limitations,
            blob_scope=blob_scope,
            event_log_head=event_log_head,
            events_processed_through_sequence=int(max_seq_by_trace.get(tid, 0)),
        )

        # 5. head.json LAST. Detect byte-identical (ignoring volatile fields).
        head_path = context_tree_head_path(project_slug, tid)
        existing_head = read_context_tree_head(project_slug, tid)
        unchanged = False
        if existing_head is not None:
            volatile = {"last_projection_at", "digest", "event_log_head"}
            stable_existing = {k: v for k, v in existing_head.items() if k not in volatile}
            stable_new = {k: v for k, v in head_payload.items() if k not in volatile}
            unchanged = stable_existing == stable_new
        if unchanged:
            heads_unchanged += 1
        else:
            _atomic_write_json(head_path, head_payload)
            heads_written += 1

    return {
        "schema_version": CONTEXT_TREE_BUCKET_SCHEMA,
        "substrate": "context-tree",
        "traces_projected": len(target_trace_ids),
        "blobs_written": blobs_written,
        "blobs_unchanged": blobs_unchanged,
        "heads_written": heads_written,
        "heads_unchanged": heads_unchanged,
        "idempotent_noop": blobs_written == 0 and heads_written == 0,
        "event_log_head": event_log_head,
        "blob_scope": blob_scope,
        "projected_at": _utc_now(),
    }


def read_context_tree_head(project_slug: str, trace_id: str) -> dict[str, Any] | None:
    """Return the per-trace head envelope (raw dict), or None if missing."""

    path = context_tree_head_path(project_slug, trace_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != CONTEXT_TREE_BUCKET_SCHEMA:
        return None
    return payload


def _iter_context_tree_head_payloads(
    project_slug: str | None = None,
) -> list[tuple[str, str, dict[str, Any]]]:
    """Walk every projected head.json once; return ``(slug, tid, payload)``.

    Single source of disk I/O for ``iter_context_tree_traces``,
    ``verify_context_tree_layer_refs``, and ``context_tree_snapshot`` so a
    status / manifest pass reads each head exactly once. Sorted by
    ``(project_slug, trace_id)``.
    """

    root = contexts_root()
    out: list[tuple[str, str, dict[str, Any]]] = []
    if not root.exists():
        return out
    project_pattern = _path_part(project_slug) if project_slug else "*"
    for head_path in sorted(root.glob(f"{project_pattern}/*/head.json")):
        try:
            payload = json.loads(head_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("schema_version") != CONTEXT_TREE_BUCKET_SCHEMA:
            continue
        proj_slug = str(payload.get("project_slug") or head_path.parent.parent.name)
        tid = str(payload.get("trace_id") or head_path.parent.name)
        if project_slug is not None and proj_slug != project_slug:
            continue
        out.append((proj_slug, tid, payload))
    out.sort(key=lambda item: (item[0], item[1]))
    return out


def _head_payload_to_row(
    proj_slug: str, tid: str, payload: dict[str, Any]
) -> dict[str, Any]:
    sync = payload.get("remote_sync") or {}
    return {
        "project_slug": proj_slug,
        "trace_id": tid,
        "node_count": int(payload.get("node_count") or 0),
        "layer_count": int(payload.get("layer_count") or 0),
        "capture_methods": list(payload.get("capture_methods") or []),
        "blob_scope": payload.get("blob_scope") or "project",
        "last_projection_at": payload.get("last_projection_at"),
        "remote_sync_eligible": bool(sync.get("eligible")),
        "events_processed_through_sequence": int(
            payload.get("events_processed_through_sequence") or 0
        ),
    }


def iter_context_tree_traces(
    project_slug: str | None = None,
) -> list[dict[str, Any]]:
    """Return one summary row per projected trace.

    Sorted deterministically by ``(project_slug, trace_id)``.
    """

    return [
        _head_payload_to_row(slug, tid, payload)
        for slug, tid, payload in _iter_context_tree_head_payloads(project_slug)
    ]


def verify_context_tree_layer_refs(
    project_slug: str | None = None,
    *,
    _heads: list[tuple[str, str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Walk every projected head and confirm referenced layer_ids resolve to blobs.

    ``_heads`` is an internal optimisation knob used by aggregators that
    have already walked the heads (e.g. ``compute_context_tree_status``);
    when supplied the helper skips its own disk walk. Pass-through only,
    not part of the public contract.
    """

    if _heads is None:
        head_triples = _iter_context_tree_head_payloads(project_slug=project_slug)
    else:
        if project_slug is None:
            head_triples = list(_heads)
        else:
            head_triples = [item for item in _heads if item[0] == project_slug]
    dangling: list[dict[str, Any]] = []
    for slug, tid, head in head_triples:
        scope = head.get("blob_scope") or "project"
        for layer_id in head.get("layer_refs") or []:
            expected_path = context_layer_blob_path(
                slug, str(layer_id), scope=str(scope)
            )
            if not expected_path.exists():
                try:
                    rel = expected_path.relative_to(paths.bucket_dir()).as_posix()
                except ValueError:
                    rel = str(expected_path)
                dangling.append(
                    {
                        "project_slug": slug,
                        "trace_id": tid,
                        "missing_layer_id": layer_id,
                        "expected_blob_path": rel,
                    }
                )
    return {
        "state": "ok" if not dangling else "dangling",
        "trace_count": len(head_triples),
        "dangling_layer_refs_count": len(dangling),
        "dangling": dangling,
    }


def compute_context_tree_status() -> dict[str, Any]:
    """Aggregate snapshot + freshness + integrity for plan 079 status surfaces.

    Single source of truth shared by ``opentraces bucket context-tree status``
    and the doctor ``context_tree`` panel so the two surfaces never drift.
    Pure read-only aggregator over ``context_tree_snapshot()`` (dedup
    metrics), per-project event log status (catch-up metrics), and
    ``verify_context_tree_layer_refs`` (integrity).
    """

    from .config import get_project_dir, load_config, opted_in_projects
    from .trails import event_log_status, read_events

    # Walk every head.json exactly once and feed the three aggregators
    # via internal ``_heads`` kwargs so they never re-read from disk.
    # Without this, an interactive ``bucket context-tree status`` on a
    # registry with N traces does 3*N head reads.
    head_triples = _iter_context_tree_head_payloads()
    snapshot = context_tree_snapshot(include_objects=False, _heads=head_triples)
    verify = verify_context_tree_layer_refs(_heads=head_triples)

    # Aggregate per-project max processed sequence + latest projection
    # timestamp in the same pass; rows list is unused beyond this loop.
    max_processed_by_project: dict[str, int] = {}
    last_projection_at: str | None = None
    for slug, _tid, payload in head_triples:
        processed = int(payload.get("events_processed_through_sequence") or 0)
        if processed > max_processed_by_project.get(slug, 0):
            max_processed_by_project[slug] = processed
        ts = payload.get("last_projection_at")
        if ts and (last_projection_at is None or ts > last_projection_at):
            last_projection_at = ts

    cfg = load_config()
    project_paths = [Path(path) for path in opted_in_projects(cfg)]
    events_behind = 0
    oldest_unprojected_event_time: str | None = None
    event_log_head: str | None = None
    for project_path in project_paths:
        if not project_path.exists():
            continue
        try:
            project_slug = get_project_dir(project_path).name
            status = event_log_status(project_path)
        except Exception:
            continue
        if status.get("state") in {"missing", "error"}:
            continue
        current_count = int(status.get("event_count", 0) or 0)
        if event_log_head is None:
            event_log_head = status.get("head")
        max_processed = max_processed_by_project.get(project_slug, 0)
        delta = max(0, current_count - max_processed)
        events_behind += delta
        if delta > 0:
            try:
                events = read_events(project_path, verify=False)
            except Exception:
                events = []
            for event in sorted(events, key=lambda e: e.event_sequence):
                if event.event_sequence <= max_processed:
                    continue
                ts = getattr(event, "created_at", None)
                if ts is not None:
                    ts_str = ts if isinstance(ts, str) else ts.isoformat()
                    if (
                        oldest_unprojected_event_time is None
                        or ts_str < oldest_unprojected_event_time
                    ):
                        oldest_unprojected_event_time = ts_str
                break

    return {
        "schema_version": snapshot.get("schema_version"),
        "root": snapshot.get("root"),
        "trace_count": snapshot.get("trace_count", 0),
        "unique_layer_blob_count": snapshot.get("unique_layer_blob_count", 0),
        "sum_layer_refs_count": snapshot.get("sum_layer_refs_count", 0),
        "dedup_hits": snapshot.get("dedup_hits", 0),
        "global_shared_blob_count": snapshot.get("global_shared_blob_count", 0),
        "digest": snapshot.get("digest"),
        "last_projection_at": last_projection_at,
        "events_since_last_projection": events_behind,
        "oldest_unprojected_event_time": oldest_unprojected_event_time,
        "event_log_head": event_log_head,
        "dangling_layer_refs_count": int(
            verify.get("dangling_layer_refs_count", 0) or 0
        ),
    }


def context_tree_snapshot(
    *,
    include_objects: bool = False,
    _heads: list[tuple[str, str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Deterministic snapshot for bucket manifest digest contribution.

    ``_heads`` is an internal optimisation knob — see
    ``verify_context_tree_layer_refs`` for the same pattern.
    """

    root = contexts_root()
    head_triples = (
        _iter_context_tree_head_payloads() if _heads is None else list(_heads)
    )
    head_payloads: list[dict[str, Any]] = [payload for _, _, payload in head_triples]
    unique_blob_ids: set[str] = set()
    sum_layer_refs = 0
    for head in head_payloads:
        for lid in head.get("layer_refs") or []:
            unique_blob_ids.add(str(lid))
        sum_layer_refs += int(head.get("layer_count") or 0)

    # Count blobs physically present under _shared/ (global scope).
    # Plan 080: global-scope blobs now live at
    # ``bucket/blobs/v1/_shared/context/<hh>/<hash>.json.gz``.
    global_shared_blob_count = 0
    shared_dir = blobs_v1_root() / "_shared" / "context"
    if shared_dir.exists():
        for blob_path in shared_dir.rglob("*.json.gz"):
            if blob_path.is_file():
                global_shared_blob_count += 1

    digest_material = {
        "schema_version": CONTEXT_TREE_SNAPSHOT_SCHEMA,
        "trace_count": len(head_payloads),
        "heads": sorted(
            [
                {
                    "project_slug": h.get("project_slug"),
                    "trace_id": h.get("trace_id"),
                    "digest": h.get("digest"),
                }
                for h in head_payloads
            ],
            key=lambda item: (str(item.get("project_slug")), str(item.get("trace_id"))),
        ),
        "unique_layer_blob_ids": sorted(unique_blob_ids),
        "global_shared_blob_count": global_shared_blob_count,
    }
    snapshot: dict[str, Any] = {
        "schema_version": CONTEXT_TREE_SNAPSHOT_SCHEMA,
        "root": str(root),
        "trace_count": len(head_payloads),
        "unique_layer_blob_count": len(unique_blob_ids),
        "sum_layer_refs_count": sum_layer_refs,
        "dedup_hits": sum_layer_refs - len(unique_blob_ids),
        "global_shared_blob_count": global_shared_blob_count,
        "digest": _digest_payload(digest_material),
    }
    if include_objects:
        snapshot["objects"] = head_payloads
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
        "projected_at": _utc_now(),
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


def _layer_id_refs_for_trace(
    project_slug: str, trace_id: str
) -> set[str]:
    """Collect every layer_id referenced by one per-trace ``context.jsonl.gz``.

    A layer_id appears either at the top of a ``context_layer_captured``
    payload (``layer_id`` / ``payload.layer_id``) or as one of the four
    ``*_layer_id`` slots on a ``context_node_observed`` payload. Both shapes
    are walked; missing files yield an empty set.
    """

    ctx_path = trace_v1_context_path(project_slug, trace_id)
    if not ctx_path.exists():
        return set()
    try:
        raw = _read_gzip_bytes(ctx_path).decode("utf-8")
    except (OSError, gzip.BadGzipFile):
        return set()
    refs: set[str] = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            continue
        payload = obj.get("payload") if isinstance(obj, dict) else None
        if not isinstance(payload, dict):
            continue
        lid = payload.get("layer_id")
        if isinstance(lid, str) and lid:
            refs.add(lid)
        for key in (
            "system_layer_id",
            "messages_layer_id",
            "tool_registry_layer_id",
            "runtime_state_layer_id",
        ):
            value = payload.get(key)
            if isinstance(value, str) and value:
                refs.add(value)
    return refs


def _layer_id_refs_from_events_mirror() -> set[str]:
    """Collect layer_ids referenced by the events mirror (``bucket/events/v1/``).

    Walks ``bucket/events/v1/batches/*.jsonl.gz`` directly so we never touch
    the canonical Git ref. Empty if the mirror does not exist.
    """

    refs: set[str] = set()
    batches_dir = events_v1_batches_dir()
    if not batches_dir.exists():
        return refs
    for batch_path in sorted(batches_dir.glob("*.jsonl.gz")):
        try:
            raw = _read_gzip_bytes(batch_path).decode("utf-8")
        except (OSError, gzip.BadGzipFile):
            continue
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (ValueError, json.JSONDecodeError):
                continue
            payload = obj.get("payload") if isinstance(obj, dict) else None
            if not isinstance(payload, dict):
                continue
            lid = payload.get("layer_id")
            if isinstance(lid, str) and lid:
                refs.add(lid)
            for key in (
                "system_layer_id",
                "messages_layer_id",
                "tool_registry_layer_id",
                "runtime_state_layer_id",
            ):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    refs.add(value)
    return refs


def _iter_context_blob_files() -> Iterator[Path]:
    """Yield every existing context blob file under ``bucket/blobs/v1/.../context/``.

    Includes both per-project (``<project>/context/<hh>/<hash>.json.gz``)
    and globally-shared (``_shared/context/...``) layouts. Sorted for
    deterministic prune output.
    """

    root = blobs_v1_root()
    if not root.exists():
        return
    for blob in sorted(root.glob("*/context/*/*.json.gz")):
        if blob.is_file():
            yield blob


def _hash_for_blob_path(path: Path) -> str | None:
    """Return the ``sha256:<hex>`` value encoded in ``<hh>/<hash>.json.gz``.

    Returns ``None`` if the path does not match the expected shape — the
    caller treats those as orphans worth a log line but never deletes them.
    """

    if path.suffix != ".gz":
        return None
    stem = path.stem  # "<hash>.json" once gzip suffix is dropped
    if not stem.endswith(".json"):
        return None
    digest_hex = stem[: -len(".json")]
    if not digest_hex or len(digest_hex) < 4:
        return None
    return f"sha256:{digest_hex}"


def _blob_content_matches_path(path: Path) -> tuple[bool, str | None]:
    """Recompute the content hash of one context blob and compare to its path.

    The canonical form is the JSON the writer fed through
    :func:`_canonical_json` in :func:`project_context_tree_to_bucket`: it
    contains ``layer_id`` / ``layer_type`` / ``capture_method`` /
    ``completeness`` / ``content`` (plus the ``schema_version`` envelope).
    The recomputed hash matches the path-encoded hash IFF the writer stored
    a hash of the layer content matching the file location.

    For plan 080's verify primitive we treat the *layer_id* itself as the
    truth: blobs are content-addressed by the layer_id encoded in the path,
    and the blob payload must echo that same layer_id (otherwise it is
    corrupted). This avoids re-deriving the layer_id hashing rules (those
    live in :mod:`context_tree.models`) and instead asserts: "the path's
    hash equals the blob payload's layer_id".

    Returns ``(ok, detail)`` where ``ok`` is True on a clean match and
    ``detail`` is a short failure reason when False.
    """

    expected = _hash_for_blob_path(path)
    if expected is None:
        return False, "unexpected filename shape"
    try:
        raw = _read_gzip_bytes(path).decode("utf-8")
        payload = json.loads(raw)
    except (OSError, gzip.BadGzipFile, ValueError, json.JSONDecodeError) as exc:
        return False, f"unreadable blob: {exc}"
    if not isinstance(payload, dict):
        return False, "blob payload is not an object"
    lid = payload.get("layer_id")
    if not isinstance(lid, str) or not lid:
        return False, "blob missing layer_id"
    if lid != expected:
        return False, f"layer_id {lid!r} does not match path hash {expected!r}"
    return True, None


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
        "generated_at": _utc_now(),
        "updated_at": _utc_now(),
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


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _canonical_json(payload, pretty=True)
    _atomic_write_text(path, text)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if current == text:
        return
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        Path(tmp_name).replace(path)
    finally:
        tmp_path = Path(tmp_name)
        if tmp_path.exists():
            tmp_path.unlink()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == data:
        return
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        Path(tmp_name).replace(path)
    finally:
        tmp_path = Path(tmp_name)
        if tmp_path.exists():
            tmp_path.unlink()


def _gzip_deterministic(data: bytes) -> bytes:
    """Deterministic gzip with ``mtime=0`` per plan 080 Resolution H.

    All gzipped surfaces (layer blobs, per-trace JSONL, event-log mirror)
    use this helper so two machines projecting the same content produce
    byte-identical output.
    """

    return gzip.compress(data, mtime=0, compresslevel=6)


def _atomic_write_gzip(path: Path, data: bytes) -> None:
    """Atomic write of ``data`` gzipped with deterministic settings."""

    _atomic_write_bytes(path, _gzip_deterministic(data))


def _read_gzip_bytes(path: Path) -> bytes:
    return gzip.decompress(path.read_bytes())


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


def _digest_payload(payload: Any) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(_canonical_json(payload).encode('utf-8')).hexdigest()}"


def _digest_bytes(payload: bytes) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _canonical_json(payload: Any, *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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

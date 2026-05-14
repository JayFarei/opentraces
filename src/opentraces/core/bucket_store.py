"""Local bucket-shaped trace substrate.

The bucket is the local mirror of the future remote sync substrate. It stores
content-addressed trace evidence, optional raw source artifacts, portable
Trace Trail event exports, and rebuildable projections; versioned datasets
stay outside the bucket as HF-shaped repositories.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from pydantic import ValidationError

from opentraces_schema import TraceRecord

from ..security.version import SECURITY_VERSION
from ..security.privacy import (
    DEFAULT_PRIVACY_TIER,
    bucket_security_state,
    record_privacy_tier,
)
from . import paths


TRACE_RECORD_BUCKET_SCHEMA = "opentraces.bucket.trace_record.v1"
TRACE_RECORD_POINTER_SCHEMA = "opentraces.bucket.trace_record_pointer.v1"
TRACE_RECORD_PROJECT_STAGING = "_staging"
RAW_SOURCE_SCHEMA = "opentraces.bucket.raw_source.v1"
RAW_SOURCE_SNAPSHOT_SCHEMA = "opentraces.bucket.raw_sources_snapshot.v1"
TRAIL_EVENT_EXPORT_SCHEMA = "opentraces.bucket.trail_events_export.v1"
TRAIL_EVENT_SNAPSHOT_SCHEMA = "opentraces.bucket.trail_events_snapshot.v1"
BUCKET_MANIFEST_SCHEMA = "opentraces.bucket.manifest.v1"
BUCKET_REMOTE_SCHEMA = "opentraces.bucket.fake_remote.v1"


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


def trace_records_root() -> Path:
    """Return the local bucket root for normalized TraceRecord envelopes."""

    return paths.bucket_dir() / "objects" / "traces" / "v1"


def legacy_trace_records_root() -> Path:
    """Return the pre-v2 trace-record root kept for one-way compatibility."""

    return paths.bucket_dir() / "trace-records"


def raw_sources_root() -> Path:
    """Return the local bucket root for optional raw source artifacts."""

    return paths.bucket_dir() / "objects" / "raw" / "v1"


def trail_events_root() -> Path:
    """Return the local bucket root for portable Trace Trail event exports."""

    return paths.bucket_dir() / "events" / "trail" / "v1"


def bucket_manifest_path() -> Path:
    return paths.bucket_dir() / "manifest.json"


def bucket_sync_state_path() -> Path:
    return paths.bucket_dir() / "sync_state.json"


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


def sync_trail_events_from_repo(
    repo: Path,
    *,
    repo_id: str,
) -> dict[str, Any]:
    """Export the repo-local TrailEvent Git ref into the portable bucket.

    The Git ref remains the local append path; this export gives remote/local
    bucket consumers a file-shaped canonical event stream to copy or restore.
    """

    from .trails import EVENT_LOG_REF, event_log_status, read_events

    status = event_log_status(repo)
    root = trail_events_root() / "repositories" / _path_part(repo_id)
    if status.get("state") == "missing":
        head = {
            "schema_version": TRAIL_EVENT_EXPORT_SCHEMA,
            "repo_id": repo_id,
            "event_log_ref": EVENT_LOG_REF,
            "event_log_head": None,
            "event_count": 0,
            "segments": [],
            "updated_at": _utc_now(),
            "state": "missing",
        }
        _atomic_write_json(root / "head.json", head)
        return head
    events = read_events(repo, verify=False)
    lines = [
        _canonical_json(event.model_dump(mode="json"))
        for event in sorted(events, key=lambda item: item.event_sequence)
    ]
    event_count = len(lines)
    digest = _digest_payload(lines)
    segment_name = f"000000000001-{event_count:012d}-{_digest_hex(digest)[:16]}.jsonl"
    segment_path = root / "segments" / segment_name
    _atomic_write_text(segment_path, ("\n".join(lines) + "\n") if lines else "")
    head = {
        "schema_version": TRAIL_EVENT_EXPORT_SCHEMA,
        "repo_id": repo_id,
        "event_log_ref": EVENT_LOG_REF,
        "event_log_head": status.get("head"),
        "event_count": event_count,
        "segments": [
            {
                "path": segment_path.relative_to(paths.bucket_dir()).as_posix(),
                "event_start": 1 if event_count else 0,
                "event_end": event_count,
                "event_count": event_count,
                "digest": digest,
            }
        ],
        "updated_at": _utc_now(),
        "state": status.get("state"),
        "verification": {
            "batch_count": status.get("batch_count"),
            "batch_parents_linear": status.get("batch_parents_linear"),
            "content_hashes_valid": status.get("content_hashes_valid"),
            "event_chain_valid": status.get("event_chain_valid"),
        },
    }
    _atomic_write_json(root / "head.json", head)
    return head


def read_trail_event_export(
    repo_id: str | None = None,
) -> tuple[dict[str, Any], list[Any]]:
    """Read a bucket-exported TrailEvent stream.

    When ``repo_id`` is omitted, exactly one repository export must exist.
    """

    from .trails import TrailEvent

    root = trail_events_root() / "repositories"
    if repo_id:
        head_paths = [root / _path_part(repo_id) / "head.json"]
    else:
        head_paths = sorted(root.glob("*/head.json")) if root.exists() else []
    head_paths = [path for path in head_paths if path.exists()]
    if not head_paths:
        raise FileNotFoundError(
            f"no bucket TrailEvent export found for repo_id={repo_id!r}"
        )
    if len(head_paths) > 1:
        repo_ids = []
        for path in head_paths:
            try:
                repo_ids.append(json.loads(path.read_text(encoding="utf-8")).get("repo_id"))
            except (OSError, ValueError, json.JSONDecodeError):
                repo_ids.append(path.parent.name)
        raise ValueError(
            "multiple bucket TrailEvent exports found; pass --repo-id "
            + ", ".join(str(item) for item in repo_ids)
        )
    head = json.loads(head_paths[0].read_text(encoding="utf-8"))
    if head.get("schema_version") != TRAIL_EVENT_EXPORT_SCHEMA:
        raise ValueError(f"unsupported TrailEvent export schema: {head.get('schema_version')}")
    events = []
    segment_lines: list[str] = []
    for segment in head.get("segments") or []:
        segment_path = paths.bucket_dir() / str(segment.get("path") or "")
        if not segment_path.exists():
            raise FileNotFoundError(f"missing TrailEvent export segment: {segment_path}")
        lines = [
            line
            for line in segment_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        expected_digest = segment.get("digest")
        actual_digest = _digest_payload(lines)
        if expected_digest and actual_digest != expected_digest:
            raise ValueError(
                f"TrailEvent export segment digest mismatch: {segment.get('path')}"
            )
        segment_lines.extend(lines)
    for line in segment_lines:
        events.append(TrailEvent.model_validate_json(line))
    events.sort(key=lambda event: event.event_sequence)
    if int(head.get("event_count") or 0) != len(events):
        raise ValueError(
            f"TrailEvent export count mismatch: head={head.get('event_count')} "
            f"segments={len(events)}"
        )
    return head, events


def restore_trail_events_to_repo(
    repo: Path,
    *,
    repo_id: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Restore bucket-exported Trace Trails into a supplied Git repository."""

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
    """Return a deterministic snapshot of portable trail event exports."""

    root = trail_events_root()
    heads: list[dict[str, Any]] = []
    if root.exists():
        for path in sorted(root.glob("repositories/*/head.json")):
            try:
                head = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if head.get("schema_version") != TRAIL_EVENT_EXPORT_SCHEMA:
                continue
            heads.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "repo_id": head.get("repo_id"),
                    "event_log_head": head.get("event_log_head"),
                    "event_count": head.get("event_count", 0),
                    "state": head.get("state"),
                    "updated_at": head.get("updated_at"),
                    **({"object": head} if include_objects else {}),
                }
            )
    digest_material = [
        (
            f"{item.get('path')} {item.get('event_log_head')} "
            f"{item.get('event_count')} {item.get('state')}"
        )
        for item in sorted(heads, key=lambda item: str(item.get("path")))
    ]
    snapshot: dict[str, Any] = {
        "schema_version": TRAIL_EVENT_SNAPSHOT_SCHEMA,
        "root": str(root),
        "repository_count": len(heads),
        "event_count": sum(int(item.get("event_count") or 0) for item in heads),
        "digest": _digest_payload(digest_material),
    }
    if include_objects:
        snapshot["objects"] = heads
    return snapshot


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

    manifest: dict[str, Any] = {
        "schema_version": BUCKET_MANIFEST_SCHEMA,
        "root": str(paths.bucket_dir()),
        "updated_at": _utc_now(),
        "security_version": SECURITY_VERSION,
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
    }
    manifest["digest"] = _digest_payload(
        {
            "schema_version": manifest["schema_version"],
            "security_version": manifest["security_version"],
            "trace_records": manifest["trace_records"],
            "trail": manifest["trail"],
            "raw_sources": manifest["raw_sources"],
            "trail_events": manifest["trail_events"],
            "sync": manifest["sync"],
        }
    )
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
            out.append(TraceRecord.model_validate_json(line))
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


def _digest_hex(digest: str) -> str:
    return digest.split(":", 1)[1] if ":" in digest else digest


def _canonical_json(payload: Any, *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _path_part(value: str) -> str:
    return quote(str(value), safe="-._~")


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

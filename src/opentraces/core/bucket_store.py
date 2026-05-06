"""Local bucket-shaped trace substrate.

The bucket is the local mirror of the future remote sync substrate. It stores
normalized trace evidence and rebuildable projections; versioned datasets stay
outside the bucket as HF-shaped repositories.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import ValidationError

from opentraces_schema import TraceRecord

from . import paths


TRACE_RECORD_BUCKET_SCHEMA = "opentraces.bucket.trace_record.v1"
TRACE_RECORD_PROJECT_STAGING = "_staging"


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

    return paths.bucket_dir() / "trace-records"


def trace_record_path(
    project_slug: str,
    trace_id: str,
) -> Path:
    """Return the bucket object path for a normalized TraceRecord."""

    return trace_records_root() / _path_part(project_slug) / f"{_path_part(trace_id)}.json"


def write_trace_record(
    record: TraceRecord,
    *,
    project_slug: str,
    source_layer: str,
    legacy_mirror: bool = True,
) -> BucketTraceRecord:
    """Write ``record`` to the local bucket as an immutable-style envelope."""

    normalized = _normalized_record(record)
    record_payload = normalized.model_dump(mode="json")
    record_hash = _digest_payload(record_payload)
    object_path = trace_record_path(project_slug, normalized.trace_id)
    envelope = {
        "schema_version": TRACE_RECORD_BUCKET_SCHEMA,
        "project_slug": project_slug,
        "source_layer": source_layer,
        "record_hash": record_hash,
        "legacy_mirror": legacy_mirror,
        "record": record_payload,
    }
    _atomic_write_json(object_path, envelope)
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


def iter_trace_record_objects() -> list[BucketTraceRecord]:
    """Return every valid TraceRecord envelope in the local bucket."""

    root = trace_records_root()
    if not root.exists():
        return []
    out: list[BucketTraceRecord] = []
    for path in sorted(root.glob("*/*.json")):
        obj = read_trace_record_object(path)
        if obj is not None:
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
        objects.append(
            {
                "path": obj.path.relative_to(root).as_posix(),
                "trace_id": obj.trace_id,
                "project_slug": obj.project_slug,
                "source_layer": obj.source_layer,
                "record_hash": obj.record_hash,
            }
        )
    digest_material = [
        f"{item['path']} {item['record_hash']}" for item in sorted(objects, key=lambda item: item["path"])
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
    for record, project_slug, source_layer in _iter_legacy_trace_records():
        object_path = trace_record_path(project_slug, record.trace_id)
        expected_paths.add(object_path)
        existing = read_trace_record_object(object_path) if object_path.exists() else None
        normalized = _normalized_record(record)
        record_hash = _digest_payload(normalized.model_dump(mode="json"))
        if existing and existing.record_hash == record_hash:
            unchanged += 1
            continue
        try:
            write_trace_record(
                normalized,
                project_slug=project_slug,
                source_layer=source_layer,
                legacy_mirror=True,
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


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _canonical_json(payload, pretty=True)
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


def _digest_payload(payload: Any) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(_canonical_json(payload).encode('utf-8')).hexdigest()}"


def _canonical_json(payload: Any, *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _path_part(value: str) -> str:
    return quote(str(value), safe="-._~")

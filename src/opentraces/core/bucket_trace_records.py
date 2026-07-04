"""Bucket TraceRecord object I/O.

This module owns the v2 TraceRecord object store: current pointers,
content-addressed record envelopes, pointer-only iteration, and legacy JSONL
hydration. It is deliberately below ``bucket_envelope`` and ``bucket_store`` so
per-trace projection and manifest code can share one record boundary without
turning ``bucket_store`` back into the implementation hub.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from opentraces.core._time import utc_now_str
from opentraces_schema import TraceRecord, load_record_json
from pydantic import ValidationError

from ..security.privacy import bucket_security_state
from . import paths
from ._bucket_io import _atomic_write_json, _digest_payload
from .bucket_layout import (
    _digest_hex,
    _path_part,
    legacy_trace_records_root,
    trace_records_root,
)
from .bucket_models import (
    BucketTraceRecord,
    BucketTraceRecordPointer,
    TRACE_RECORD_BUCKET_SCHEMA,
    TRACE_RECORD_POINTER_SCHEMA,
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

    When ``project_slug`` is given, only that project's records are globbed and
    parsed, avoiding reads of every other project's JSON on a shared bucket.
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


def iter_corpus_trace_records(
    project_slug: str | None = None,
) -> list[BucketTraceRecord]:
    """Return the winning ``BucketTraceRecord`` per trace across the FULL corpus.

    ``iter_trace_record_objects`` only globs the bucket tier (v2 object + plan-079
    legacy mirror). It never looks at the two project-local JSONL layers that
    :mod:`trace_corpus` treats as first-class corpus members: project JSONL
    (``projects/<slug>/traces/<id>.jsonl``, ``source_layer="canonical"``) and
    staging JSONL. A project-local-only trace arises because ingest writes the
    JSONL before the bucket object as separate non-atomic writes -- a
    ``write_trace_record`` failure can leave JSONL without a bucket object -- and
    for pre-v2 corpora not yet swept by ``sync_trace_records_from_local_stores``.

    This sibling reader routes through :func:`trace_corpus.iter_sources` (the
    same pointer/stat-cheap, deduped-by-trace_id, freshest-mtime-wins union that
    ``trace get``/``trace query`` already resolve through), then hydrates each
    winning source with :func:`trace_corpus.load_record`. Consumers that must
    see every durable trace -- dataset row provenance, skill-invocation
    projection, verifier mining, the bundled skill-opt/pr-intent workflows --
    should use this instead of the bucket-tier-only enumerator (issue #211).

    Deliberately does NOT replace ``iter_trace_record_objects`` itself: the
    manifest/digest/prune/security-sweep callers must stay bucket-object-only or
    the digest invariant and prune semantics break.
    """

    from .trace_corpus import iter_sources, load_record

    out: list[BucketTraceRecord] = []
    for source in iter_sources():
        if project_slug is not None and source.project_slug != project_slug:
            continue
        obj = load_record(source)
        if obj is not None:
            out.append(obj)
    return out


def read_bucket_record_for_trace(trace_id: str) -> BucketTraceRecord | None:
    """Resolve one trace by trace_id from the durable read sources, or ``None``."""

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
    """Hydrate the latest record matching ``trace_id`` from one JSONL shard."""

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

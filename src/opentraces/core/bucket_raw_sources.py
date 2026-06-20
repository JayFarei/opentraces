"""Bucket raw-source artifact I/O."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from opentraces.core._time import utc_now_str

from . import paths
from ._bucket_io import (
    _atomic_write_bytes,
    _atomic_write_json,
    _digest_bytes,
    _digest_payload,
)
from .bucket_layout import _digest_hex, _path_part, raw_sources_root
from .bucket_models import RAW_SOURCE_SCHEMA, RAW_SOURCE_SNAPSHOT_SCHEMA


def write_raw_source_artifact(
    source_path: Path,
    *,
    trace_id: str,
    project_slug: str,
    source_kind: str,
    parser: str,
) -> dict[str, Any]:
    """Store an optional raw source artifact in the portable bucket."""

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

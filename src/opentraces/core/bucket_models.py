"""Bucket data models + schema-version constants (the lowest bucket layer).

The frozen dataclasses and ``opentraces.bucket.*`` schema-version string
constants the rest of the bucket subsystem is built on. Extracted from
``bucket_store`` (god-module decomposition) as the dependency-free BASE layer:
this module imports only stdlib + the schema package — nothing from
``bucket_store`` or its siblings — so any of them can import it without a cycle.
``bucket_store`` re-exports every name here, so existing
``from ...bucket_store import BucketTraceRecord`` / ``BUCKET_MANIFEST_SCHEMA``
call sites keep working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentraces_schema import TraceRecord

TRACE_RECORD_BUCKET_SCHEMA = "opentraces.bucket.trace_record.v1"
TRACE_RECORD_POINTER_SCHEMA = "opentraces.bucket.trace_record_pointer.v1"
TRACE_RECORD_PROJECT_STAGING = "_staging"
RAW_SOURCE_SCHEMA = "opentraces.bucket.raw_source.v1"
RAW_SOURCE_SNAPSHOT_SCHEMA = "opentraces.bucket.raw_sources_snapshot.v1"
TRAIL_EVENT_EXPORT_SCHEMA = "opentraces.bucket.trail_events_export.v1"

# Plan 080 — bucket layout v2. The schema_version on ``bucket/manifest.json`` is
# the load-bearing contract between writer (``bucket_store``) and remote sync
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
class BucketTraceRecordPointer:
    path: Path
    project_slug: str
    source_layer: str
    trace_id: str
    record_hash: str


@dataclass(frozen=True)
class BucketSyncSummary:
    root: Path
    written: int = 0
    unchanged: int = 0
    removed: int = 0
    skipped: int = 0

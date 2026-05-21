"""Path helpers for the local bucket layout."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from . import paths


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


def contexts_root() -> Path:
    """Return the local bucket root for first-class Context Tree projections."""

    return paths.bucket_dir() / "contexts" / "v1"


def traces_v1_root() -> Path:
    """Return ``bucket/traces/v1`` — per-trace envelope root."""

    return paths.bucket_dir() / "traces" / "v1"


def traces_v1_dir(project_slug: str, trace_id: str) -> Path:
    """Per-trace envelope directory holding trace.json + companion JSONL.gz."""

    return traces_v1_root() / _path_part(project_slug) / _path_part(trace_id)


def trace_v1_json_path(project_slug: str, trace_id: str) -> Path:
    return traces_v1_dir(project_slug, trace_id) / "trace.json"


def trace_v1_trail_path(project_slug: str, trace_id: str) -> Path:
    return traces_v1_dir(project_slug, trace_id) / "trail.jsonl.gz"


def trace_v1_context_path(project_slug: str, trace_id: str) -> Path:
    return traces_v1_dir(project_slug, trace_id) / "context.jsonl.gz"


def trace_v1_sources_path(project_slug: str, trace_id: str) -> Path:
    return traces_v1_dir(project_slug, trace_id) / "sources.jsonl.gz"


def trace_v1_history_dir(project_slug: str, trace_id: str) -> Path:
    return traces_v1_dir(project_slug, trace_id) / "trace_history"


def blobs_v1_root() -> Path:
    """Return ``bucket/blobs/v1`` — content-addressed blob root."""

    return paths.bucket_dir() / "blobs" / "v1"


def blobs_v1_context_path(project_slug: str, layer_id: str) -> Path:
    """Per-project context layer blob path."""

    digest_hex = _digest_hex(layer_id)
    return (
        blobs_v1_root()
        / _path_part(project_slug)
        / "context"
        / digest_hex[:2]
        / f"{digest_hex}.json.gz"
    )


def blobs_v1_raw_path(
    project_slug: str, content_digest: str, *, suffix: str = ".blob"
) -> Path:
    """Per-project raw source blob path."""

    digest_hex = _digest_hex(content_digest)
    return (
        blobs_v1_root()
        / _path_part(project_slug)
        / "raw"
        / digest_hex[:2]
        / f"{digest_hex}{suffix}"
    )


def events_v1_root() -> Path:
    """Return ``bucket/events/v1`` — canonical event log mirror root."""

    return paths.bucket_dir() / "events" / "v1"


def events_v1_batches_dir() -> Path:
    return events_v1_root() / "batches"


def events_v1_index_path() -> Path:
    return events_v1_root() / "index.json"


def context_tree_dir(project_slug: str, trace_id: str) -> Path:
    """Return the per-trace Context Tree dir holding head/nodes/reconciliation."""

    return contexts_root() / _path_part(project_slug) / _path_part(trace_id)


def context_tree_head_path(project_slug: str, trace_id: str) -> Path:
    return context_tree_dir(project_slug, trace_id) / "head.json"


def context_tree_nodes_path(project_slug: str, trace_id: str) -> Path:
    return context_tree_dir(project_slug, trace_id) / "nodes.jsonl"


def context_tree_reconciliation_path(project_slug: str, trace_id: str) -> Path:
    return context_tree_dir(project_slug, trace_id) / "reconciliation.json"


def context_layer_blob_path(
    project_slug: str, layer_id: str, *, scope: str = "project"
) -> Path:
    """Resolve the per-layer blob path under the current namespace scope."""

    digest_hex = _digest_hex(layer_id)
    if scope == "global":
        return (
            blobs_v1_root()
            / "_shared"
            / "context"
            / digest_hex[:2]
            / f"{digest_hex}.json.gz"
        )
    return blobs_v1_context_path(project_slug, layer_id)


def bucket_manifest_path() -> Path:
    return paths.bucket_dir() / "manifest.json"


def bucket_sync_state_path() -> Path:
    return paths.bucket_dir() / "sync_state.json"


def _digest_hex(digest: str) -> str:
    return digest.split(":", 1)[1] if ":" in digest else digest


def _path_part(value: str) -> str:
    return quote(str(value), safe="-._~")

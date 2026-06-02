"""Resolve a context resume packet from the trace's OWN bucket companion.

The live event-log projection (``context_resume_packet(repo, node_id)``) only
carries whichever trace's context events are currently in the canonical ref.
Older captured traces keep their context in their per-trace bucket companion
(``bucket/contexts/v1/<slug>/<trace>/nodes.jsonl`` + per-layer blobs). Sourcing
the resume packet from the companion makes the capsule SELF-SUFFICIENT (plan 082
R2) and yields a full closure for any captured trace, not only the live one.

The packet shape mirrors ``context_tree.resume.context_resume_packet`` so the
capsule envelope is identical regardless of which source resolved it.
"""

from __future__ import annotations

import gzip
import json
from typing import Any

from ..bucket_layout import (
    context_layer_blob_path,
    context_tree_head_path,
    context_tree_nodes_path,
)

CONTEXT_RESUME_SCHEMA_VERSION = "opentraces.context_resume.v1"

_LAYER_FIELDS = (
    ("system_layer", "system_layer_id"),
    ("messages_layer", "messages_layer_id"),
    ("tool_registry_layer", "tool_registry_layer_id"),
    ("runtime_state_layer", "runtime_state_layer_id"),
)


def _read_nodes(slug: str, trace_id: str) -> list[dict[str, Any]]:
    path = context_tree_nodes_path(slug, trace_id)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _read_head(slug: str, trace_id: str) -> dict[str, Any]:
    path = context_tree_head_path(slug, trace_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def node_id_for_step_from_bucket(slug: str, trace_id: str, step_index: int) -> str | None:
    """Resolve the context node id for a step from the trace's companion."""

    for node in _read_nodes(slug, trace_id):
        if node.get("step_index") == step_index:
            return node.get("node_id")
    return None


def _read_layer_blob(slug: str, layer_id: str | None, scope: str) -> dict[str, Any] | None:
    if not layer_id:
        return None
    scopes = [scope] if scope in ("project", "global") else ["project", "global"]
    for sc in scopes:
        path = context_layer_blob_path(slug, layer_id, scope=sc)
        if path.exists():
            try:
                data = json.loads(gzip.open(path, "rb").read())
            except Exception:  # pragma: no cover - corrupt blob
                return None
            return {
                "layer_id": data.get("layer_id"),
                "layer_type": data.get("layer_type"),
                "completeness": data.get("completeness"),
                "capture_method": data.get("capture_method"),
                "content": data.get("content"),
            }
    return None


def resume_packet_from_bucket(slug: str, trace_id: str, node_id: str | None) -> dict[str, Any]:
    """Build a ``context_resume`` packet from the trace's bucket companion.

    Returns the same envelope shape as the live ``context_resume_packet``;
    on a miss returns ``{"error": ..., "limitations": [...]}`` (never raises).
    """

    nodes = _read_nodes(slug, trace_id)
    node = next((n for n in nodes if n.get("node_id") == node_id), None)
    if node is None:
        return {
            "schema_version": CONTEXT_RESUME_SCHEMA_VERSION,
            "node_id": node_id,
            "error": "node not found in bucket companion",
            "limitations": ["context_layer_unavailable"],
            "source": "bucket_companion",
        }

    head = _read_head(slug, trace_id)
    scope = str(head.get("blob_scope") or "project")
    limitations: list[str] = list(head.get("capture_limitations") or [])

    packet: dict[str, Any] = {
        "schema_version": CONTEXT_RESUME_SCHEMA_VERSION,
        "node_id": node_id,
        "trace_id": node.get("trace_id") or trace_id,
        "step_index": node.get("step_index"),
        "branch_type": node.get("branch_type"),
        "transcript_uuid": node.get("transcript_uuid"),
        "transcript_offset": node.get("transcript_offset"),
        "capture_completeness": node.get("capture_completeness"),
        "trail_anchor_hint": node.get("trail_anchor_hint"),
        "source": "bucket_companion",
    }
    for key, field in _LAYER_FIELDS:
        blob = _read_layer_blob(slug, node.get(field), scope)
        if blob is None:
            packet[key] = None
            if "context_layer_unavailable" not in limitations:
                limitations.append("context_layer_unavailable")
        else:
            packet[key] = blob
    packet["limitations"] = limitations
    return packet


__all__ = [
    "node_id_for_step_from_bucket",
    "resume_packet_from_bucket",
]

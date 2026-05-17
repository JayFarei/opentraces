"""Context Tree resume packet.

Parallel to Trail's ``snapshot_resume_packet`` (which describes the
working tree at a step) and complementary to it: this packet describes
what the *model saw* at a specific ContextNode. Combined with the Trail
snapshot, the two packets fully describe the state needed to fork or
replay an agent session at a chosen point.

The packet is the v1 primitive; the actual ``claude --fork-session``
orchestration is a one-screen tool function built on top, not a CLI yet
(see ``kb/plans/077-context-tree-substrate.md`` §"Resume").

Schema-version-stamped JSON envelope (``opentraces.context_resume.v1``)
so downstream consumers (replay, RL, viewer, dashboards) can drive the
substrate through a stable contract.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .contract import CONTEXT_RESUME_SCHEMA_VERSION
from .query import build_context_tree_projection


def context_resume_packet(repo: Path, node_id: str) -> dict[str, Any]:
    """Return a structured packet describing the model's view at ``node_id``.

    Inlines all four ContextLayer contents (system, messages,
    tool_registry, runtime_state) plus the ContextNode's
    ``trail_anchor_hint`` pass-through and the trace's accumulated
    capture limitations. When the node cannot be resolved (unknown id,
    no Context Tree projected for the repo, or layer contents missing),
    returns ``{"error": "node not found", "limitations": [...]}`` rather
    than raising so CLI callers can render an empty-state envelope.

    Combines with ``snapshot_resume_packet(commit_id_from_node.trail_anchor_hint)``
    to fully describe the state for resume.
    """
    projection = build_context_tree_projection(repo)
    node = projection.nodes_by_id.get(node_id)
    if node is None:
        return {
            "schema_version": CONTEXT_RESUME_SCHEMA_VERSION,
            "node_id": node_id,
            "error": "node not found",
            "limitations": ["context_layer_unavailable"],
        }

    # Resolve layer contents; missing layers are surfaced as limitations
    # rather than raising. Each layer is referenced by hash on the node;
    # the projection's ``layers_by_id`` carries the canonical content.
    limitations: list[str] = list(
        projection.capture_limitations_by_trace.get(node.trace_id, [])
    )
    layer_pairs = (
        ("system_layer", node.system_layer_id),
        ("messages_layer", node.messages_layer_id),
        ("tool_registry_layer", node.tool_registry_layer_id),
        ("runtime_state_layer", node.runtime_state_layer_id),
    )
    layer_contents: dict[str, Any] = {}
    for key, layer_id in layer_pairs:
        layer = projection.layers_by_id.get(layer_id)
        if layer is None:
            layer_contents[key] = None
            if "context_layer_unavailable" not in limitations:
                limitations.append("context_layer_unavailable")
        else:
            layer_contents[key] = {
                "layer_id": layer.layer_id,
                "layer_type": layer.layer_type,
                "completeness": layer.completeness,
                "capture_method": layer.capture_method,
                "content": layer.content,
            }

    messages_layer = layer_contents.get("messages_layer") or {}
    messages_content = (messages_layer.get("content") or {}) if messages_layer else {}
    runtime_layer = layer_contents.get("runtime_state_layer") or {}
    runtime_content = (runtime_layer.get("content") or {}) if runtime_layer else {}

    return {
        "schema_version": CONTEXT_RESUME_SCHEMA_VERSION,
        "node_id": node.node_id,
        "trace_id": node.trace_id,
        "step_index": node.step_index,
        "branch_type": node.branch_type,
        "transcript_uuid": node.transcript_uuid,
        "transcript_offset": node.transcript_offset,
        "capture_completeness": node.capture_completeness,
        # Inlined layer contents under stable keys
        "system_layer": layer_contents.get("system_layer"),
        "messages": messages_content.get("messages", []),
        "messages_layer": layer_contents.get("messages_layer"),
        "tool_registry": layer_contents.get("tool_registry_layer"),
        "env": {
            "cwd": runtime_content.get("cwd"),
            "permission_mode": runtime_content.get("permission_mode"),
            "model": runtime_content.get("model"),
            "effort_level": runtime_content.get("effort_level"),
        },
        "mcp_state": runtime_content.get("mcp_servers", {}),
        "runtime_state": layer_contents.get("runtime_state_layer"),
        # Cross-substrate hint: feed into snapshot_resume_packet
        "trail_anchor_hint": node.trail_anchor_hint,
        "capture_limitations": sorted(set(limitations)),
    }

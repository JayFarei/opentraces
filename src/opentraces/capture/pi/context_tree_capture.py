"""Context Tree capture for Pi sessions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentraces_schema import TraceRecord

from ...core.context_tree import (
    CONTEXT_COMPACTION_OBSERVED,
    CONTEXT_LAYER_CAPTURED,
    CONTEXT_NODE_OBSERVED,
    CONTEXT_TREE_RECONCILED,
    ContextLayer,
    ContextNode,
    build_layer,
    build_node,
)
from ...core.context_tree.contract import CAPTURE_METHOD_LIVE, CAPTURE_METHOD_TRANSCRIPT
from ...core.trails.models import TrailEventDraft

LIMITATION_TRANSCRIPT_FALLBACK = "pi_context_tree_transcript_fallback"
LIMITATION_ACTIVE_BRANCH_ONLY = "pi_active_branch_only_v1"
EVENT_CAPTURE_METHOD = ["pi_extension", "context_tree_capture"]


@dataclass(frozen=True)
class PiContextTreeProjection:
    layers: list[ContextLayer]
    nodes: list[ContextNode]
    drafts: list[TrailEventDraft]
    summary: dict[str, Any]


def build_context_tree_projection_from_record(record: TraceRecord) -> PiContextTreeProjection:
    pi_meta = _pi_meta(record)
    provider_contexts = _provider_contexts(record)
    live = bool(provider_contexts)
    capture_method = CAPTURE_METHOD_LIVE if live else CAPTURE_METHOD_TRANSCRIPT
    completeness = "full" if live else "approximated"
    limitations = _limitations(record, live=live)
    layers = _build_layers(record, pi_meta, provider_contexts, limitations)
    by_type = {layer.layer_type: layer for layer in layers}
    nodes = _build_nodes(record, by_type, completeness)
    drafts = _build_event_drafts(record, layers, nodes, limitations, capture_method)
    step_node_id_map = {
        node.step_index: node.node_id
        for node in nodes
        if node.step_index is not None
    }
    summary = {
        "trace_id": record.trace_id,
        "node_count": len(nodes),
        "layer_count": len({layer.layer_id for layer in layers}),
        "active_path_leaf_id": nodes[-1].node_id if nodes else None,
        "limitations": limitations,
        "capture_limitations": limitations,
        "capture_method": capture_method,
        "capture_methods": sorted({layer.capture_method for layer in layers}),
        "step_node_id_map": step_node_id_map,
        "compaction_count": len(_compaction_events(record)),
        "source": "pi_context_tree_capture",
    }
    return PiContextTreeProjection(layers=layers, nodes=nodes, drafts=drafts, summary=summary)


def emit_context_tree_events_from_record(
    *,
    project_dir: Path,
    final_record: TraceRecord,
    transcript_path: Path | None = None,
) -> dict[str, Any]:
    del transcript_path
    projection = build_context_tree_projection_from_record(final_record)
    if projection.drafts:
        from ...core.trails.event_log import append_event_batch

        append_event_batch(
            Path(project_dir),
            projection.drafts,
            writer="pi-context-tree-capture",
        )
    return projection.summary


def _build_layers(
    record: TraceRecord,
    pi_meta: dict[str, Any],
    provider_contexts: list[dict[str, Any]],
    limitations: list[str],
) -> list[ContextLayer]:
    newest_context = provider_contexts[-1] if provider_contexts else {}
    runtime_state = pi_meta.get("runtime_state") if isinstance(pi_meta.get("runtime_state"), dict) else {}
    tool_registry = (
        newest_context.get("tool_registry")
        or pi_meta.get("tool_registry")
        or _tool_inventory(record)
    )

    def _fidelity(live_content_present: Any) -> tuple[str, str]:
        # A layer is only "full"/live_capture when THIS layer's content came off
        # the provider wire. Otherwise it is a transcript-reconstructed
        # approximation. The decision is per-layer, never a session-wide boolean,
        # so e.g. a session with a provider context but no captured system prompt
        # never claims completeness=full for an empty system layer.
        live = bool(provider_contexts) and bool(live_content_present)
        return (
            "full" if live else "approximated",
            CAPTURE_METHOD_LIVE if live else CAPTURE_METHOD_TRANSCRIPT,
        )

    system_completeness, system_method = _fidelity(newest_context.get("system"))
    messages_completeness, messages_method = _fidelity(newest_context.get("messages"))
    tools_completeness, tools_method = _fidelity(newest_context.get("tool_registry"))
    runtime_completeness, runtime_method = _fidelity(newest_context.get("runtime_state"))
    return [
        build_layer(
            layer_type="system",
            content={
                "agent": "pi",
                "model": record.agent.model,
                "system": newest_context.get("system"),
                "capture_source": "pi_provider_context" if newest_context.get("system") else "pi_transcript",
                "limitations": limitations,
            },
            completeness=system_completeness,
            capture_method=system_method,
        ),
        build_layer(
            layer_type="messages",
            content={
                "scope": "active_branch",
                "provider_messages": newest_context.get("messages"),
                "steps": [_step_message(step) for step in record.steps],
                "active_leaf_id": pi_meta.get("active_leaf_id"),
                "active_leaf_source": pi_meta.get("active_leaf_source"),
                "branch_count": pi_meta.get("branch_count"),
                "tree_events": pi_meta.get("tree_events") if isinstance(pi_meta.get("tree_events"), list) else [],
                "branch_summaries": pi_meta.get("branch_summaries") if isinstance(pi_meta.get("branch_summaries"), list) else [],
                "limitations": limitations,
            },
            completeness=messages_completeness,
            capture_method=messages_method,
        ),
        build_layer(
            layer_type="tool_registry",
            content={
                "tools": tool_registry if isinstance(tool_registry, list) else [],
                "schemas_available": bool(newest_context.get("tool_registry")),
                "capture_source": "pi_provider_context" if newest_context.get("tool_registry") else "parsed_trace_record",
            },
            completeness=tools_completeness,
            capture_method=tools_method,
        ),
        build_layer(
            layer_type="runtime_state",
            content={
                "cwd": pi_meta.get("cwd"),
                "session_file": pi_meta.get("session_file"),
                "session_version": pi_meta.get("version"),
                "session_name": runtime_state.get("session_name"),
                "thinking_level": runtime_state.get("thinking_level"),
                "model": runtime_state.get("model") or record.agent.model,
                "git": _git_runtime(record),
                "raw_provider_bodies_default": "off",
                "raw_provider_body_refs": newest_context.get("raw_provider_body_refs"),
            },
            completeness=runtime_completeness,
            capture_method=runtime_method,
        ),
    ]


def _build_nodes(
    record: TraceRecord,
    layer_by_type: dict[str, ContextLayer],
    completeness: str,
) -> list[ContextNode]:
    nodes: list[ContextNode] = []
    parent: str | None = None
    for index, step in enumerate(record.steps):
        step_index = step.step_index
        node = build_node(
            parent_node_id=parent,
            branch_type="root" if parent is None else "linear",
            trace_id=record.trace_id,
            step_index=step_index,
            transcript_uuid=_step_transcript_uuid(record, step_index),
            transcript_parent_uuid=(
                _step_transcript_uuid(record, record.steps[index - 1].step_index)
                if index > 0
                else None
            ),
            transcript_offset=max((step_index or 1) - 1, 0),
            system_layer_id=layer_by_type["system"].layer_id,
            messages_layer_id=layer_by_type["messages"].layer_id,
            tool_registry_layer_id=layer_by_type["tool_registry"].layer_id,
            runtime_state_layer_id=layer_by_type["runtime_state"].layer_id,
            trail_anchor_hint={
                "source": "pi_active_branch",
                "session_id": record.session_id,
                "step_index": step_index,
            },
            capture_completeness=completeness,
        )
        step.context_node_id = node.node_id
        nodes.append(node)
        parent = node.node_id
    return nodes


def _build_event_drafts(
    record: TraceRecord,
    layers: list[ContextLayer],
    nodes: list[ContextNode],
    limitations: list[str],
    capture_method: str,
) -> list[TrailEventDraft]:
    capture_methods = [*EVENT_CAPTURE_METHOD, capture_method]
    drafts: list[TrailEventDraft] = []
    seen_layers: set[str] = set()
    for layer in layers:
        if layer.layer_id in seen_layers:
            continue
        seen_layers.add(layer.layer_id)
        drafts.append(TrailEventDraft(
            event_type=CONTEXT_LAYER_CAPTURED,
            payload=layer.model_dump(mode="json"),
            trace_id=record.trace_id,
            capture_method=capture_methods,
        ))
    for node in nodes:
        drafts.append(TrailEventDraft(
            event_type=CONTEXT_NODE_OBSERVED,
            payload=node.model_dump(mode="json"),
            trace_id=record.trace_id,
            step_index=node.step_index,
            capture_method=capture_methods,
        ))
    step_map = {node.step_index: node.node_id for node in nodes}
    for compaction in _compaction_events(record):
        step_index = _compaction_step_index(compaction)
        pre_node_id, post_node_id = _compaction_boundary(step_map, step_index)
        drafts.append(TrailEventDraft(
            event_type=CONTEXT_COMPACTION_OBSERVED,
            payload={
                "trace_id": record.trace_id,
                "source": compaction.get("source") or "pi_metadata",
                "event": compaction.get("event") or "Compaction",
                "trigger": compaction.get("trigger"),
                "timestamp": compaction.get("timestamp"),
                "pre_node_id": pre_node_id,
                "post_node_id": post_node_id,
                "metadata": {k: v for k, v in compaction.items() if k not in {"source", "event", "trigger", "timestamp"}},
            },
            trace_id=record.trace_id,
            step_index=step_index,
            capture_method=capture_methods,
        ))
    pi_meta = _pi_meta(record)
    drafts.append(TrailEventDraft(
        event_type=CONTEXT_TREE_RECONCILED,
        payload={
            "trace_id": record.trace_id,
            "node_count": len(nodes),
            "layer_count": len(seen_layers),
            "active_path_leaf_id": nodes[-1].node_id if nodes else None,
            "orphan_branch_roots": _orphan_branch_roots(pi_meta),
            "subagent_session_ids": [],
            "limitations": limitations,
            "context_capture_limitations": limitations,
            "source": "pi_context_tree_capture",
            "pi_active_leaf_id": pi_meta.get("active_leaf_id"),
            "pi_active_leaf_source": pi_meta.get("active_leaf_source"),
            "pi_tree_events": pi_meta.get("tree_events") if isinstance(pi_meta.get("tree_events"), list) else [],
        },
        trace_id=record.trace_id,
        capture_method=capture_methods,
    ))
    return drafts


def _pi_meta(record: TraceRecord) -> dict[str, Any]:
    meta = record.metadata if isinstance(record.metadata, dict) else {}
    pi = meta.get("pi") if isinstance(meta.get("pi"), dict) else {}
    return pi


def _provider_contexts(record: TraceRecord) -> list[dict[str, Any]]:
    value = _pi_meta(record).get("provider_contexts") or []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _orphan_branch_roots(pi_meta: dict[str, Any]) -> list[str]:
    """Preserve Pi branch/tree transitions without exporting each leaf in v1."""

    active = pi_meta.get("active_leaf_id")
    roots: list[str] = []
    tree_events = pi_meta.get("tree_events") if isinstance(pi_meta.get("tree_events"), list) else []
    for event in tree_events:
        if not isinstance(event, dict):
            continue
        old_leaf = event.get("old_leaf_id")
        new_leaf = event.get("new_leaf_id")
        for value in (old_leaf, new_leaf):
            if isinstance(value, str) and value and value != active and value not in roots:
                roots.append(value)
    return roots


def _limitations(record: TraceRecord, *, live: bool) -> list[str]:
    limitations = [LIMITATION_ACTIVE_BRANCH_ONLY]
    if not live:
        limitations.append(LIMITATION_TRANSCRIPT_FALLBACK)
    meta_limits = record.metadata.get("capture_limitations") if isinstance(record.metadata, dict) else []
    if isinstance(meta_limits, list):
        for item in meta_limits:
            code = item.get("code") if isinstance(item, dict) else None
            if isinstance(code, str) and code.startswith("pi_"):
                limitations.append(code)
    return sorted(set(limitations))


def _step_message(step: Any) -> dict[str, Any]:
    return {
        "step_index": step.step_index,
        "role": step.role,
        "content": step.content,
        "reasoning_present": bool(step.reasoning_content),
        "tool_calls": [
            {
                "tool_call_id": call.tool_call_id,
                "tool_name": call.tool_name,
                "input_keys": sorted(call.input.keys()) if isinstance(call.input, dict) else [],
            }
            for call in step.tool_calls
        ],
        "observations": [
            {
                "source_call_id": obs.source_call_id,
                "output_summary": obs.output_summary,
                "error": obs.error,
            }
            for obs in step.observations
        ],
        "timestamp": step.timestamp,
    }


def _tool_inventory(record: TraceRecord) -> list[dict[str, Any]]:
    names: set[str] = set()
    for step in record.steps:
        names.update(step.tools_available)
        names.update(call.tool_name for call in step.tool_calls)
    return [{"name": name, "input_schema": None, "schema_capture": "parsed_trace_record"} for name in sorted(names)]


def _git_runtime(record: TraceRecord) -> dict[str, Any]:
    vcs = record.environment.vcs
    return {"type": vcs.type, "branch": vcs.branch, "base_commit": vcs.base_commit}


def _step_transcript_uuid(record: TraceRecord, step_index: int | None) -> str:
    return f"pi:{record.session_id}:step:{step_index or 0}"


def _compaction_events(record: TraceRecord) -> list[dict[str, Any]]:
    value = record.metadata.get("compaction_events") if isinstance(record.metadata, dict) else []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _compaction_step_index(compaction: dict[str, Any]) -> int | None:
    for key in ("step_index", "pre_step_index", "post_step_index"):
        value = compaction.get(key)
        if isinstance(value, int):
            return value
    return None


def _nearest_node_id(step_node_id_map: dict[int | None, str], step_index: int | None) -> str | None:
    numbered = {k: v for k, v in step_node_id_map.items() if isinstance(k, int)}
    if not numbered:
        return None
    if step_index is None:
        return numbered[max(numbered)]
    candidates = [idx for idx in numbered if idx <= step_index]
    if not candidates:
        candidates = list(numbered)
    return numbered[max(candidates)]


def _compaction_boundary(
    step_node_id_map: dict[int | None, str], step_index: int | None
) -> tuple[str | None, str | None]:
    """Resolve a compaction's (pre, post) boundary node ids as a real transition.

    A compaction sits between the node observed just before it and the node that
    first runs against the compacted context, so pre and post must be distinct
    whenever the active path has a step on each side (matching the claude_code /
    codex boundaries instead of emitting a zero-width pre==post edge).
    """
    numbered = sorted(idx for idx in step_node_id_map if isinstance(idx, int))
    if not numbered:
        return None, None
    if step_index is None:
        if len(numbered) >= 2:
            return step_node_id_map[numbered[-2]], step_node_id_map[numbered[-1]]
        return step_node_id_map[numbered[-1]], step_node_id_map[numbered[-1]]
    pre = [idx for idx in numbered if idx < step_index]
    post = [idx for idx in numbered if idx >= step_index]
    pre_id = step_node_id_map[pre[-1]] if pre else None
    post_id = step_node_id_map[post[0]] if post else step_node_id_map[numbered[-1]]
    return (pre_id if pre_id is not None else post_id), post_id

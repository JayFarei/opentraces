"""Context Tree capture for Codex CLI rollout traces.

Codex rollout JSONL does not currently preserve enough wire-level detail
to reconstruct the exact per-step prompt. This module therefore builds an
honest parity slice: four session-level ContextLayers are reconstructed from
the parsed ``TraceRecord`` plus Codex metadata, then each Step receives a
stable ContextNode pointing at those shared layers.
"""

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
from ...core.trails.models import TrailEventDraft

CAPTURE_METHOD = "transcript_reconstruction"
EVENT_CAPTURE_METHOD = ["context_tree_capture", "transcript_reconstruction"]

LIMITATION_SESSION_LEVEL_LAYERS = "codex_cli_session_level_context_layers"
LIMITATION_TOOL_SCHEMAS_UNAVAILABLE = "codex_cli_tool_schemas_unavailable"
LIMITATION_SYSTEM_PROMPT_BODY_UNAVAILABLE = "codex_cli_system_prompt_body_unavailable"


@dataclass(frozen=True)
class CodexContextTreeProjection:
    """In-memory Context Tree projection for one Codex TraceRecord."""

    layers: list[ContextLayer]
    nodes: list[ContextNode]
    drafts: list[TrailEventDraft]
    summary: dict[str, Any]


def build_context_tree_projection_from_record(
    record: TraceRecord,
) -> CodexContextTreeProjection:
    """Build Context Tree layers, nodes, and TrailEvent drafts for Codex.

    The returned drafts are valid append inputs for the canonical Trail event
    log. The caller may inspect them directly in tests or append them with
    ``emit_context_tree_events_from_record``.
    """

    codex_meta = _codex_meta(record)
    limitations = _limitations(record)
    layers = _build_session_layers(record, codex_meta, limitations)
    layer_by_type = {layer.layer_type: layer for layer in layers}
    nodes = _build_step_nodes(record, layer_by_type)
    drafts = _build_event_drafts(record, layers, nodes, limitations)
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
        "capture_method": CAPTURE_METHOD,
        "step_node_id_map": step_node_id_map,
        "compaction_count": len(_compaction_events(record)),
        "source": "codex_cli_context_tree_capture",
    }
    return CodexContextTreeProjection(
        layers=layers,
        nodes=nodes,
        drafts=drafts,
        summary=summary,
    )


def emit_context_tree_events_from_record(
    *,
    project_dir: Path,
    final_record: TraceRecord,
    transcript_path: Path | None = None,
) -> dict[str, Any]:
    """Append Codex Context Tree events and return the ingest summary.

    ``transcript_path`` is accepted for parity with the Claude builder and the
    ingest hook, but this slice intentionally relies on the parsed TraceRecord.

    Issue #210 (seal-family W2, stamp-after-persist): ``_build_step_nodes``
    (called by :func:`build_context_tree_projection_from_record`) stamps
    ``final_record.steps[*].context_node_id`` as a side effect of building the
    projection -- a contract locked in by
    ``tests/capture/test_codex_context_tree_capture.py`` for direct callers of
    the builder. But at THIS orchestrator boundary (the one ``core/ingest.py``
    and the W2 backfill actually call) those stamps must not survive an
    ``append_event_batch`` failure: if the backing ``context_node_observed``
    events never made it into the canonical event log, the stamped ids would
    dangle -- resolving to nothing in the trace's own bucket companion. So the
    pre-call ids are snapshotted and restored on failure before the exception
    propagates.
    """

    del transcript_path
    pre_stamp_ids = [step.context_node_id for step in final_record.steps]
    projection = build_context_tree_projection_from_record(final_record)
    if projection.drafts:
        from ...core.trails.event_log import append_event_batch

        try:
            append_event_batch(
                Path(project_dir),
                projection.drafts,
                writer="codex-context-tree-capture",
            )
        except Exception:
            for step, original_id in zip(final_record.steps, pre_stamp_ids):
                step.context_node_id = original_id
            raise
    return projection.summary


def _build_session_layers(
    record: TraceRecord,
    codex_meta: dict[str, Any],
    limitations: list[str],
) -> list[ContextLayer]:
    return [
        build_layer(
            layer_type="system",
            content={
                "agent": "codex-cli",
                "model": record.agent.model,
                "base_instructions": codex_meta.get("base_instructions"),
                "turn_user_instructions": codex_meta.get("turn_user_instructions"),
                "metadata_only": True,
                "limitations": limitations,
            },
            completeness="approximated",
            capture_method=CAPTURE_METHOD,
        ),
        build_layer(
            layer_type="messages",
            content={
                "scope": "session",
                "steps": [_step_message(step) for step in record.steps],
                "metadata": {
                    "reconstruction": "from_parsed_trace_record",
                    "per_step_prompt_windows_available": False,
                },
                "limitations": [LIMITATION_SESSION_LEVEL_LAYERS],
            },
            completeness="approximated",
            capture_method=CAPTURE_METHOD,
        ),
        build_layer(
            layer_type="tool_registry",
            content={
                "tools": _tool_inventory(record),
                "schemas_available": False,
                "function_names": _function_names(record),
                "limitations": [LIMITATION_TOOL_SCHEMAS_UNAVAILABLE],
            },
            completeness="approximated",
            capture_method=CAPTURE_METHOD,
        ),
        build_layer(
            layer_type="runtime_state",
            content={
                "cwd": codex_meta.get("cwd"),
                "approval_policy": codex_meta.get("approval_policy"),
                "sandbox_policy": codex_meta.get("sandbox_policy"),
                "permission_profile": codex_meta.get("permission_profile"),
                "permission_mode": record.metadata.get("permission_mode"),
                "git": _git_runtime(record),
                "hook_sidecar_present": bool(record.metadata.get("codex_cli_hook_sidecar")),
                "permission_request_count": len(record.metadata.get("permission_requests") or []),
                "compaction_count": len(_compaction_events(record)),
            },
            completeness="approximated",
            capture_method=CAPTURE_METHOD,
        ),
    ]


def _build_step_nodes(
    record: TraceRecord,
    layer_by_type: dict[str, ContextLayer],
) -> list[ContextNode]:
    nodes: list[ContextNode] = []
    parent_node_id: str | None = None
    commit_id = _trace_commit_id(record)
    for index, step in enumerate(record.steps):
        branch_type = "root" if parent_node_id is None else "linear"
        step_index = step.step_index
        node = build_node(
            parent_node_id=parent_node_id,
            branch_type=branch_type,
            trace_id=record.trace_id,
            step_index=step_index,
            transcript_uuid=_step_transcript_uuid(record, step_index),
            transcript_parent_uuid=(
                _step_transcript_uuid(record, record.steps[index - 1].step_index)
                if index > 0 else None
            ),
            transcript_offset=max(step_index - 1, 0),
            system_layer_id=layer_by_type["system"].layer_id,
            messages_layer_id=layer_by_type["messages"].layer_id,
            tool_registry_layer_id=layer_by_type["tool_registry"].layer_id,
            runtime_state_layer_id=layer_by_type["runtime_state"].layer_id,
            trail_anchor_hint=_trail_anchor_hint(
                record,
                step_index=step_index,
                commit_id=commit_id,
            ),
            capture_completeness="approximated",
        )
        step.context_node_id = node.node_id
        nodes.append(node)
        parent_node_id = node.node_id
    return nodes


def _build_event_drafts(
    record: TraceRecord,
    layers: list[ContextLayer],
    nodes: list[ContextNode],
    limitations: list[str],
) -> list[TrailEventDraft]:
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
            capture_method=EVENT_CAPTURE_METHOD,
        ))

    for node in nodes:
        drafts.append(TrailEventDraft(
            event_type=CONTEXT_NODE_OBSERVED,
            payload=node.model_dump(mode="json"),
            trace_id=record.trace_id,
            step_index=node.step_index,
            capture_method=EVENT_CAPTURE_METHOD,
        ))

    step_node_id_map = {node.step_index: node.node_id for node in nodes}
    for compaction in _compaction_events(record):
        step_index = _compaction_step_index(compaction)
        drafts.append(TrailEventDraft(
            event_type=CONTEXT_COMPACTION_OBSERVED,
            payload={
                "trace_id": record.trace_id,
                "source": compaction.get("source") or "codex_cli_metadata",
                "event": compaction.get("event"),
                "trigger": compaction.get("trigger"),
                "timestamp": compaction.get("timestamp"),
                "pre_node_id": _nearest_node_id(step_node_id_map, step_index, before=True),
                "post_node_id": _nearest_node_id(step_node_id_map, step_index, before=False),
                "metadata": {
                    k: v for k, v in compaction.items()
                    if k not in {"source", "event", "trigger", "timestamp"}
                },
            },
            trace_id=record.trace_id,
            step_index=step_index,
            capture_method=EVENT_CAPTURE_METHOD,
        ))

    drafts.append(TrailEventDraft(
        event_type=CONTEXT_TREE_RECONCILED,
        payload={
            "trace_id": record.trace_id,
            "node_count": len(nodes),
            "layer_count": len(seen_layers),
            "active_path_leaf_id": nodes[-1].node_id if nodes else None,
            "orphan_branch_roots": [],
            "subagent_session_ids": [],
            "limitations": limitations,
            "context_capture_limitations": limitations,
            "source": "codex_cli_context_tree_capture",
        },
        trace_id=record.trace_id,
        capture_method=EVENT_CAPTURE_METHOD,
    ))
    return drafts


def _codex_meta(record: TraceRecord) -> dict[str, Any]:
    value = record.metadata.get("codex_cli") if isinstance(record.metadata, dict) else {}
    return value if isinstance(value, dict) else {}


def _limitations(record: TraceRecord) -> list[str]:
    limitations = [
        LIMITATION_SESSION_LEVEL_LAYERS,
        LIMITATION_TOOL_SCHEMAS_UNAVAILABLE,
    ]
    codex_meta = _codex_meta(record)
    if not codex_meta.get("base_instructions"):
        limitations.append(LIMITATION_SYSTEM_PROMPT_BODY_UNAVAILABLE)
    return limitations


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
    names.update(_function_names(record))
    return [
        {
            "name": name,
            "input_schema": None,
            "schema_capture": "unavailable_in_codex_rollout",
        }
        for name in sorted(name for name in names if name)
    ]


def _function_names(record: TraceRecord) -> list[str]:
    counts = record.metadata.get("function_names") or {}
    if not isinstance(counts, dict):
        return []
    return sorted(str(name) for name in counts)


def _git_runtime(record: TraceRecord) -> dict[str, Any]:
    vcs = record.environment.vcs
    return {
        "type": vcs.type,
        "branch": vcs.branch,
        "base_commit": vcs.base_commit,
    }


def _trail_anchor_hint(
    record: TraceRecord,
    *,
    step_index: int,
    commit_id: str | None,
) -> dict[str, Any]:
    hint: dict[str, Any] = {
        "source": "parsed_trace_record",
        "session_id": record.session_id,
        "step_index": step_index,
    }
    if commit_id:
        hint["commit_id"] = commit_id
    return hint


def _trace_commit_id(record: TraceRecord) -> str | None:
    outcome_sha = _clean_sha(getattr(record.outcome, "commit_sha", None))
    if outcome_sha:
        return outcome_sha

    patches = getattr(record, "patches", None) or []
    for patch in reversed(patches):
        anchor = getattr(patch, "anchor", None)
        anchor_sha = _clean_sha(getattr(anchor, "commit_sha", None))
        if anchor_sha:
            return anchor_sha

    vcs = getattr(getattr(record, "environment", None), "vcs", None)
    return _clean_sha(getattr(vcs, "base_commit", None))


def _clean_sha(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _step_transcript_uuid(record: TraceRecord, step_index: int) -> str:
    return f"codex:{record.session_id}:step:{step_index}"


def _compaction_events(record: TraceRecord) -> list[dict[str, Any]]:
    value = record.metadata.get("compaction_events") or []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _compaction_step_index(compaction: dict[str, Any]) -> int | None:
    for key in ("step_index", "pre_step_index", "post_step_index"):
        value = compaction.get(key)
        if isinstance(value, int):
            return value
    return None


def _nearest_node_id(
    step_node_id_map: dict[int | None, str],
    step_index: int | None,
    *,
    before: bool,
) -> str | None:
    numbered = {k: v for k, v in step_node_id_map.items() if isinstance(k, int)}
    if not numbered:
        return None
    if step_index is None:
        key = max(numbered) if before else min(numbered)
        return numbered[key]
    candidates = [idx for idx in numbered if idx <= step_index] if before else [
        idx for idx in numbered if idx >= step_index
    ]
    if not candidates:
        return None
    return numbered[max(candidates) if before else min(candidates)]

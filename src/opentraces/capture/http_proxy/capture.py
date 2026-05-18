"""Emit Context Tree events from the proxy capture log.

Reads the JSONL file written by ``proxy.run_proxy`` (one record per
intercepted ``/v1/messages`` POST) and projects it into Context Tree
TrailEvents. Mirrors the shape of
``capture/claude_code/context_tree_capture.emit_context_tree_events_from_record``,
but every layer it emits is tagged ``capture_method: proxy`` and
``completeness: full`` because the proxy sees the actual bytes the
model was sent.

Layer contract:

| layer_type      | source from proxy log                                  | completeness |
|-----------------|--------------------------------------------------------|--------------|
| system          | ``request.system`` (string or block list)              | full         |
| messages        | ``request.messages`` (full active-path message list)   | full         |
| tool_registry   | ``request.tools`` (name + description + input_schema)  | full         |
| runtime_state   | ``request.model``, ``ot_properties.*``, request meta   | full         |

Per the prototype scope, this module:
- Does NOT integrate with ``core/ingest.py``; it's a standalone entry
  point used by the comparison script and the
  ``context-tree-via-proxy`` fixture.
- Does NOT attempt to reconcile against the JSONL pipeline's
  ContextNodes; the comparison script joins by ``step_index`` after
  both pipelines run independently.
- Does NOT emit ``context_compaction_observed`` events. The proxy
  never sees a compact_boundary; compaction is a Claude-Code-side
  concept on the JSONL. The JSONL pipeline is authoritative for that.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable

from ...core.context_tree import (
    CONTEXT_LAYER_CAPTURED,
    CONTEXT_NODE_OBSERVED,
    CONTEXT_TREE_RECONCILED,
    ContextLayer,
    ContextNode,
    build_layer,
    build_node,
)

logger = logging.getLogger(__name__)

HTTP_LOG_FILENAME = "http_capture.jsonl"


# --------------------------------------------------------------------------- #
# Log reading
# --------------------------------------------------------------------------- #


def read_http_capture_log(log_path: Path) -> list[dict[str, Any]]:
    """Read the proxy capture log into a list of records, in capture order."""
    records: list[dict[str, Any]] = []
    if not log_path.exists():
        return records
    with log_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            text = raw_line.strip()
            if not text:
                continue
            try:
                records.append(json.loads(text))
            except json.JSONDecodeError:
                logger.debug("skipping unparseable capture line: %r", text[:120])
    records.sort(key=lambda r: r.get("sequence", 0))
    return records


# --------------------------------------------------------------------------- #
# Layer builders (proxy-side, fed by request body bytes)
# --------------------------------------------------------------------------- #


def build_system_layer_from_request(request_body: dict[str, Any]) -> ContextLayer:
    """Build the ``system`` layer from the request body's ``system`` field.

    The Anthropic API accepts ``system`` as either a string or a list of
    content blocks (each typically ``{type: "text", text: "..."}``). We
    canonicalise both into a single text blob plus a per-block array so
    consumers can see both the assembled-prompt bytes and the structure.
    """
    raw_system = request_body.get("system")
    if isinstance(raw_system, str):
        blocks = [{"type": "text", "text": raw_system}]
        assembled_text = raw_system
    elif isinstance(raw_system, list):
        blocks = [b for b in raw_system if isinstance(b, dict)]
        assembled_text = "\n".join(
            b.get("text", "") for b in blocks
            if b.get("type") == "text" and isinstance(b.get("text"), str)
        )
    else:
        blocks = []
        assembled_text = ""

    return build_layer(
        layer_type="system",
        content={
            "blocks": blocks,
            "assembled_text": assembled_text,
            "assembled_text_bytes": len(assembled_text.encode("utf-8")),
            "block_count": len(blocks),
        },
        completeness="full",
        capture_method="proxy",
    )


def build_messages_layer_from_request(request_body: dict[str, Any]) -> ContextLayer:
    """Build the ``messages`` layer from the request body's ``messages``.

    The proxy sees the exact message list shipped on the wire, so the
    layer is the bytes themselves. We preserve every field, then
    summarise span/length so consumers can use the layer like the
    JSONL-side ``messages`` layer without re-parsing the content.
    """
    messages = request_body.get("messages") or []
    if not isinstance(messages, list):
        messages = []
    serialised = [m if isinstance(m, dict) else {} for m in messages]
    span_first_idx = 0
    span_last_idx = max(0, len(serialised) - 1)
    total_text_chars = 0
    for msg in serialised:
        content = msg.get("content")
        if isinstance(content, str):
            total_text_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text")
                    if isinstance(text, str):
                        total_text_chars += len(text)

    return build_layer(
        layer_type="messages",
        content={
            "messages": serialised,
            "message_count": len(serialised),
            "span_first_index": span_first_idx,
            "span_last_index": span_last_idx,
            "total_text_chars": total_text_chars,
            "total_token_estimate": total_text_chars // 4,
            "is_summary": False,
            "summary_of_span": None,
        },
        completeness="full",
        capture_method="proxy",
    )


def build_tool_registry_layer_from_request(
    request_body: dict[str, Any],
) -> ContextLayer:
    """Build the ``tool_registry`` layer from the request body's ``tools``.

    Unlike the JSONL-side layer (which lacks ``input_schema`` for
    built-in tools), the proxy sees the full tool definition the
    client sent, including parameter schemas. We preserve every
    field verbatim.
    """
    tools_raw = request_body.get("tools") or []
    tools: list[dict[str, Any]] = []
    if isinstance(tools_raw, list):
        for tool in tools_raw:
            if not isinstance(tool, dict):
                continue
            tools.append({
                "name": str(tool.get("name", "")),
                "description": str(tool.get("description", "") or ""),
                # Note: the wire format calls this ``input_schema`` (Anthropic),
                # which is the same key the plan 077 layer content uses.
                "input_schema": tool.get("input_schema"),
                # ``source`` cannot be inferred from the wire alone; the
                # proxy doesn't know if a tool is built-in vs MCP vs skill.
                # We tag every proxy-observed tool ``wire`` and leave
                # downstream consumers to enrich via JSONL or registry
                # lookups when they care.
                "source": "wire",
                "source_path": None,
            })
    # ``tool_choice`` shapes what the model is *expected* to do this turn;
    # capture it so the layer reflects per-turn constraints, not just the
    # registry. Two turns with identical tools but different tool_choice
    # are intentionally different layers.
    tool_choice = request_body.get("tool_choice")
    return build_layer(
        layer_type="tool_registry",
        content={
            "tools": tools,
            "tool_count": len(tools),
            "tool_choice": tool_choice,
            "deferred_tools": [],
        },
        completeness="full",
        capture_method="proxy",
    )


def build_runtime_state_layer_from_request(
    *,
    request_body: dict[str, Any],
    ot_properties: dict[str, str],
    captured_at: str,
    request_headers: dict[str, str],
) -> ContextLayer:
    """Build the ``runtime_state`` layer from request metadata + OT-Property-* headers.

    The proxy doesn't have direct access to the client process's cwd /
    permission_mode the way the JSONL pipeline does (those live in the
    Claude Code session metadata, not the wire). The convention for
    surfacing them is the Helicone-style ``OT-Property-*`` header set
    (plan 078 brief). When the client doesn't set them, the
    corresponding fields are null in this layer.
    """
    cwd = ot_properties.get("cwd")
    permission_mode = ot_properties.get("permission-mode")
    effort_level = ot_properties.get("effort-level")
    project_slug = ot_properties.get("project-slug")
    session_id = ot_properties.get("session-id")
    step_index_str = ot_properties.get("step-index")
    step_index: int | None
    try:
        step_index = int(step_index_str) if step_index_str is not None else None
    except (TypeError, ValueError):
        step_index = None

    return build_layer(
        layer_type="runtime_state",
        content={
            "cwd": cwd,
            "permission_mode": permission_mode,
            "model": request_body.get("model"),
            "max_tokens": request_body.get("max_tokens"),
            "temperature": request_body.get("temperature"),
            "top_p": request_body.get("top_p"),
            "top_k": request_body.get("top_k"),
            "stop_sequences": request_body.get("stop_sequences"),
            "stream": bool(request_body.get("stream", False)),
            "effort_level": effort_level,
            "project_slug": project_slug,
            "session_id": session_id,
            "wire_user_agent": request_headers.get("User-Agent"),
            "anthropic_version": request_headers.get("Anthropic-Version"),
            # MCP / env state are NOT on the wire; the proxy cannot
            # reconstruct them without cooperation from the client.
            "mcp_servers": {},
            "allowlisted_env": {},
            # Capture timestamp so two turns at the same step but
            # different wall-clock times are distinguishable for replay.
            "captured_at": captured_at,
            "captured_step_index": step_index,
        },
        completeness="full",
        capture_method="proxy",
    )


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #


def emit_context_tree_events_from_http_log(
    *,
    project_dir: Path,
    trace_id: str,
    log_path: Path,
    writer: str = "context-tree-http-proxy",
) -> dict[str, Any]:
    """Append Context Tree events to the canonical log from a proxy log.

    Returns a summary dict mirroring the JSONL-side orchestrator's
    return value:
        {trace_id, node_count, layer_count, active_path_leaf_id,
         capture_limitations, step_node_id_map}

    Each captured POST becomes one ``context_node_observed`` event with
    ``step_index`` taken from ``ot_properties.step-index`` when present,
    otherwise from the capture sequence number.
    """
    from ...core.trails.event_log import append_event_batch
    from ...core.trails.models import TrailEventDraft

    summary: dict[str, Any] = {
        "trace_id": trace_id,
        "node_count": 0,
        "layer_count": 0,
        "active_path_leaf_id": None,
        "capture_limitations": [],
        "step_node_id_map": {},
    }

    records = read_http_capture_log(log_path)
    if not records:
        summary["capture_limitations"].append("context_tree_not_captured")
        return summary

    drafts: list[TrailEventDraft] = []
    seen_layer_ids: set[str] = set()
    nodes: list[ContextNode] = []
    parent_node_id: str | None = None

    for idx, record in enumerate(records):
        request_body = record.get("request_body") or {}
        if not isinstance(request_body, dict):
            request_body = {}
        ot_properties = record.get("ot_properties") or {}
        captured_at = record.get("captured_at") or ""
        request_headers = record.get("request_headers") or {}

        system_layer = build_system_layer_from_request(request_body)
        messages_layer = build_messages_layer_from_request(request_body)
        tools_layer = build_tool_registry_layer_from_request(request_body)
        runtime_layer = build_runtime_state_layer_from_request(
            request_body=request_body,
            ot_properties=ot_properties,
            captured_at=captured_at,
            request_headers=request_headers,
        )

        for layer in (system_layer, messages_layer, tools_layer, runtime_layer):
            if layer.layer_id in seen_layer_ids:
                continue
            seen_layer_ids.add(layer.layer_id)
            drafts.append(TrailEventDraft(
                event_type=CONTEXT_LAYER_CAPTURED,
                payload=layer.model_dump(mode="json"),
                trace_id=trace_id,
                capture_method=["http_proxy_capture"],
            ))

        step_index_str = ot_properties.get("step-index")
        try:
            step_index = int(step_index_str) if step_index_str is not None else idx
        except (TypeError, ValueError):
            step_index = idx

        transcript_uuid = (
            ot_properties.get("transcript-uuid")
            or f"http-proxy-seq-{record.get('sequence', idx)}"
        )
        transcript_parent_uuid = ot_properties.get("transcript-parent-uuid") or None
        branch_type = "root" if parent_node_id is None else "linear"

        node = build_node(
            parent_node_id=parent_node_id,
            branch_type=branch_type,
            trace_id=trace_id,
            transcript_uuid=transcript_uuid,
            transcript_parent_uuid=transcript_parent_uuid,
            transcript_offset=int(record.get("sequence", idx)),
            step_index=step_index,
            system_layer_id=system_layer.layer_id,
            messages_layer_id=messages_layer.layer_id,
            tool_registry_layer_id=tools_layer.layer_id,
            runtime_state_layer_id=runtime_layer.layer_id,
            capture_completeness="full",
        )
        nodes.append(node)
        drafts.append(TrailEventDraft(
            event_type=CONTEXT_NODE_OBSERVED,
            payload=node.model_dump(mode="json"),
            trace_id=trace_id,
            step_index=step_index,
            capture_method=["http_proxy_capture"],
        ))

        parent_node_id = node.node_id
        if step_index is not None:
            summary["step_node_id_map"][step_index] = node.node_id

    summary["node_count"] = len(nodes)
    summary["layer_count"] = len(seen_layer_ids)
    summary["active_path_leaf_id"] = nodes[-1].node_id if nodes else None

    drafts.append(TrailEventDraft(
        event_type=CONTEXT_TREE_RECONCILED,
        payload={
            "trace_id": trace_id,
            "node_count": summary["node_count"],
            "layer_count": summary["layer_count"],
            "active_path_leaf_id": summary["active_path_leaf_id"],
            "orphan_branch_roots": [],
            "subagent_session_ids": [],
            "capture_limitations": list(summary["capture_limitations"]),
        },
        trace_id=trace_id,
        capture_method=["http_proxy_capture"],
    ))

    try:
        append_event_batch(project_dir, drafts, writer=writer)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "http_proxy context_tree event append failed for trace %s: %s",
            trace_id, exc, exc_info=True,
        )
        summary["capture_limitations"].append("context_tree_not_captured")

    return summary

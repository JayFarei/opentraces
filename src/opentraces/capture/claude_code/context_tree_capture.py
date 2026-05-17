"""Context Tree capture from a Claude Code JSONL session.

This module is the Phase 2 capture entry point: it re-reads the
transcript JSONL after the main parser has produced a ``TraceRecord``,
walks the ``parentUuid`` graph to identify active path / orphan branches
/ compaction boundaries / sub-agent forks, builds the four per-step
layers, and emits ``context_*`` events into the canonical Trail event
log (``refs/opentraces/local/events/v1``).

The substrate's Phase 2 acceptance test is the
`capture-fidelity` / `branching-fidelity` journeys passing on synthetic
fixtures. See ``kb/plans/077-context-tree-substrate.md`` §"Capture".

Fan-out shape (per the goal prompt):
- Coordinator owns the parentUuid walker + the orchestrator
  (``emit_context_tree_events_from_record``).
- Agent D fills in the per-layer reconstruction stubs
  (``build_system_layer``, ``build_messages_layer``,
  ``build_tool_registry_layer``, ``build_runtime_state_layer``).
- Agent E fills in the detection stubs
  (``detect_compaction_boundaries``, ``detect_orphan_branches``,
  ``detect_subagent_forks``).

Stub returns are no-ops that produce a structurally valid empty Context
Tree, so the ingest path stays green while Phase 2 lands.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opentraces_schema.models import TraceRecord

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


# --------------------------------------------------------------------------- #
# Parent graph (the parentUuid walker — coordinator-owned)
# --------------------------------------------------------------------------- #


@dataclass
class JsonlRecord:
    """One line of a Claude Code session JSONL, with structural fields."""

    offset: int                       # byte offset in the source JSONL
    uuid: str | None                  # record's own uuid (None for system/result rows)
    parent_uuid: str | None           # parentUuid, the tree-link to the predecessor
    record_type: str                  # JSONL record "type" field
    subtype: str | None               # "subtype" if present (system.init, compact_boundary, etc.)
    raw: dict[str, Any]               # full parsed JSON payload


@dataclass
class ParentGraph:
    """Indexed parent graph + active path identification for one session.

    Built from the raw JSONL lines; consumed by the layer builders and
    the detectors. The active path is identified by walking parents
    backwards from the last assistant-type record on the longest
    chain (matching Claude Code's own resume semantics: the latest
    non-orphan leaf).
    """

    records: list[JsonlRecord] = field(default_factory=list)
    by_uuid: dict[str, JsonlRecord] = field(default_factory=dict)
    children_of: dict[str, list[str]] = field(default_factory=dict)
    active_leaf_uuid: str | None = None
    active_path_uuids: list[str] = field(default_factory=list)

    def active_path(self) -> list[JsonlRecord]:
        return [self.by_uuid[u] for u in self.active_path_uuids if u in self.by_uuid]


def read_jsonl_records(transcript_path: Path) -> list[JsonlRecord]:
    """Read a transcript JSONL into ``JsonlRecord`` instances with offsets.

    Robust to partial trailing lines (Claude Code appends live);
    skips JSON-parse errors with a debug log rather than failing.
    """
    records: list[JsonlRecord] = []
    if not transcript_path.exists():
        return records
    offset = 0
    with transcript_path.open("rb") as handle:
        for raw_line in handle:
            line_start = offset
            offset += len(raw_line)
            text = raw_line.decode("utf-8", errors="replace").rstrip("\n")
            if not text.strip():
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                logger.debug("skipping unparseable JSONL line at offset %s", line_start)
                continue
            records.append(
                JsonlRecord(
                    offset=line_start,
                    uuid=payload.get("uuid"),
                    parent_uuid=payload.get("parentUuid"),
                    record_type=str(payload.get("type", "unknown")),
                    subtype=payload.get("subtype"),
                    raw=payload,
                )
            )
    return records


def build_parent_graph(records: list[JsonlRecord]) -> ParentGraph:
    """Build the parent graph + identify the active path.

    The active path is the longest uuid-chain ending at a record whose
    uuid has no children (a leaf). When multiple leaves tie, prefer the
    last record by offset (latest in file). Records without ``uuid``
    (system / result rows) are stored but not part of the chain.
    """
    graph = ParentGraph(records=list(records))

    for rec in records:
        if rec.uuid:
            graph.by_uuid[rec.uuid] = rec
            graph.children_of.setdefault(rec.uuid, [])

    for rec in records:
        if rec.uuid and rec.parent_uuid and rec.parent_uuid in graph.by_uuid:
            graph.children_of.setdefault(rec.parent_uuid, []).append(rec.uuid)

    leaves = [
        rec for rec in records
        if rec.uuid and not graph.children_of.get(rec.uuid)
    ]
    if not leaves:
        return graph

    # Prefer leaves on the longest chain back to a root, ties broken by file order.
    def chain_length(uuid: str) -> int:
        seen: set[str] = set()
        length = 0
        cur: str | None = uuid
        while cur and cur not in seen:
            seen.add(cur)
            length += 1
            rec = graph.by_uuid.get(cur)
            if rec is None:
                break
            cur = rec.parent_uuid
        return length

    leaves.sort(key=lambda r: (chain_length(r.uuid or ""), r.offset))
    graph.active_leaf_uuid = leaves[-1].uuid

    # Walk parents from the active leaf back to root to materialize the path.
    path: list[str] = []
    cur = graph.active_leaf_uuid
    visited: set[str] = set()
    while cur and cur not in visited:
        visited.add(cur)
        path.append(cur)
        rec = graph.by_uuid.get(cur)
        cur = rec.parent_uuid if rec else None
    path.reverse()
    graph.active_path_uuids = path
    return graph


# --------------------------------------------------------------------------- #
# Layer builders (Agent D fills in)
# --------------------------------------------------------------------------- #


def build_system_layer(
    *,
    init_record: JsonlRecord | None,
    on_disk_claude_md: list[dict[str, Any]],
    on_disk_memory_md: str | None,
    append_system_prompt_override: str | None,
) -> ContextLayer:
    """Reconstruct the system layer for the active path.

    STUB (Agent D): returns a minimal approximated layer with empty
    claude_md_set. Agent D's implementation must:
    - Read ``~/.claude/CLAUDE.md``, project ``./CLAUDE.md``, and
      ``.claude/rules/*.md`` per the load order in
      https://code.claude.com/docs/en/memory
    - Hash each file's content at capture time
    - Include the environment block (cwd, model, claude_code_version)
      derived from the init_record
    - Include the MEMORY.md head hash (first 200 lines or 25KB)
    - Include the ``--append-system-prompt`` override hash if present
    """
    cwd = ""
    model = ""
    if init_record is not None:
        cwd = str(init_record.raw.get("cwd", ""))
        model = str(init_record.raw.get("model", ""))
    return build_layer(
        layer_type="system",
        content={
            "static_core_ref": f"claude_code:{init_record.raw.get('claude_code_version', 'unknown') if init_record else 'unknown'}",
            "claude_md_set": on_disk_claude_md,
            "memory_md_head_hash": _sha256_or_null(on_disk_memory_md),
            "append_system_prompt_hash": _sha256_or_null(append_system_prompt_override),
            "environment_block": json.dumps({"cwd": cwd, "model": model}, sort_keys=True),
        },
        completeness="approximated",
        capture_method="hardcoded_template",
    )


def build_messages_layer(
    *,
    active_path: list[JsonlRecord],
    is_summary: bool = False,
    summary_of_span: tuple[str, str] | None = None,
) -> ContextLayer:
    """Reconstruct the messages layer for the active path slice.

    STUB (Agent D): returns a minimal layer with per-message
    content_hash + uuid only. Agent D's implementation must:
    - Walk active_path records, extracting role + content + uuid + parent_uuid
    - Compute content_hash per message via sha256 over canonical material
    - Compute total_token_estimate (cheap heuristic; len(text)/4 is fine for v1)
    - Set is_summary=True when the layer represents a post-compaction
      summary substitution (passed in by detect_compaction_boundaries)
    - Set span_first_uuid / span_last_uuid from the first/last
      message-role records on the active path
    """
    messages = []
    for rec in active_path:
        if rec.record_type not in ("user", "assistant"):
            continue
        text = json.dumps(rec.raw.get("message", {}), sort_keys=True)
        messages.append({
            "role": rec.raw.get("message", {}).get("role", "unknown"),
            "uuid": rec.uuid or "",
            "parent_uuid": rec.parent_uuid,
            "content_hash": _sha256(text),
        })
    return build_layer(
        layer_type="messages",
        content={
            "messages": messages,
            "total_token_estimate": sum(len(m.get("content_hash", "")) for m in messages),
            "span_first_uuid": messages[0]["uuid"] if messages else "",
            "span_last_uuid": messages[-1]["uuid"] if messages else "",
            "is_summary": is_summary,
            "summary_of_span": list(summary_of_span) if summary_of_span else None,
        },
        completeness="full",
        capture_method="transcript_reconstruction",
    )


def build_tool_registry_layer(
    *,
    init_record: JsonlRecord | None,
    on_disk_mcp_servers: dict[str, Any],
    on_disk_skills: list[dict[str, Any]],
) -> ContextLayer:
    """Reconstruct the tool registry layer.

    STUB (Agent D): returns a stub layer with names-only from the
    init record. Agent D's implementation must:
    - Pull built-in tool list from init_record.raw["tools"] (names+desc only)
    - Cross-reference a version-pinned builtin schema table (keyed off
      claude_code_version) to fill input_schema where possible
    - Add MCP server tools (names from on_disk_mcp_servers; schemas
      stubbed unless probed)
    - Add skill tools (from on_disk_skills, source_path attribution)
    - Set deferred_tools list from any deferred_tools_delta records
      seen during the parent-graph walk (caller passes in if needed)
    """
    tools = []
    if init_record is not None:
        for t in init_record.raw.get("tools", []) or []:
            tools.append({
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "input_schema": None,
                "source": "builtin",
                "source_path": None,
            })
    return build_layer(
        layer_type="tool_registry",
        content={
            "tools": tools,
            "deferred_tools": [],
        },
        completeness="approximated",
        capture_method="hardcoded_template",
    )


def build_runtime_state_layer(
    *,
    init_record: JsonlRecord | None,
    on_disk_mcp_servers: dict[str, Any],
) -> ContextLayer:
    """Reconstruct the runtime state layer.

    STUB (Agent D): returns minimal state derived from init_record.
    Agent D's implementation must:
    - Pull cwd, model, permissionMode, claude_code_version from init_record
    - Allowlist env vars and hash their values (use the security
      walker's existing allowlist; never store raw env values unless
      the walker approves the key)
    - Add MCP server state (command_hash, args_hash, instructions_hash,
      tool_names list, tool_schemas: null in v1 unless probed)
    - Derive effort_level from init_record or downstream messages
    """
    cwd = ""
    model = ""
    permission_mode = "default"
    if init_record is not None:
        cwd = str(init_record.raw.get("cwd", ""))
        model = str(init_record.raw.get("model", ""))
        permission_mode = str(init_record.raw.get("permissionMode", "default"))
    return build_layer(
        layer_type="runtime_state",
        content={
            "cwd": cwd,
            "permission_mode": permission_mode,
            "model": model,
            "effort_level": None,
            "allowlisted_env": {},
            "mcp_servers": on_disk_mcp_servers,
        },
        completeness="approximated",
        capture_method="transcript_reconstruction",
    )


# --------------------------------------------------------------------------- #
# Detectors (Agent E fills in)
# --------------------------------------------------------------------------- #


@dataclass
class CompactionBoundary:
    """One detected compaction event in the parent graph."""

    pre_record_uuid: str               # last active-path record before the compact_boundary
    post_record_uuid: str              # first active-path record after the summary substitution
    compacted_span_first_uuid: str
    compacted_span_last_uuid: str
    summary_text_hash: str
    lossy_diff_removed_uuids: list[str]


@dataclass
class OrphanBranch:
    """One detected rewind orphan branch."""

    branch_root_uuid: str              # the record where the divergence happened
    leaf_uuid: str                     # the orphan branch's tail
    record_uuids_in_subtree: list[str]


@dataclass
class SubagentFork:
    """One detected subagent spawn within a parent session."""

    parent_record_uuid: str            # the Task tool_use record on the parent
    subagent_session_id: str
    subagent_jsonl_path: Path | None   # the child JSONL if discoverable


def detect_compaction_boundaries(
    graph: ParentGraph,
) -> list[CompactionBoundary]:
    """Find compaction boundaries in the active path.

    STUB (Agent E): returns []. Agent E's implementation must:
    - Walk active_path() looking for records with
      ``record_type == "system" and subtype == "compact_boundary"``
    - For each boundary, identify the synthetic ``isCompactSummary: true``
      record that follows (which substitutes for the compacted span)
    - Walk backwards from the boundary to find the span first/last uuids
      that were compacted (typically all active-path messages between
      the previous boundary, or root, and this boundary)
    - Compute summary_text_hash from the isCompactSummary record's content
    - Populate lossy_diff_removed_uuids with the set of pre-compaction
      message uuids that are NOT represented in the summary
    """
    return []


def detect_orphan_branches(graph: ParentGraph) -> list[OrphanBranch]:
    """Find rewound (Esc Esc) orphan branches in the parent graph.

    STUB (Agent E): returns []. Agent E's implementation must:
    - For each record in graph.records, find records whose parent_uuid
      points to a record that ALSO has at least one other child on the
      active path. The non-active-path child is the orphan root.
    - For each orphan root, walk children recursively to collect the
      full orphan subtree.
    - Filter out subtrees that share the active leaf (those aren't
      orphans, they're just unreached but reachable).
    """
    return []


def detect_subagent_forks(
    graph: ParentGraph,
    transcript_path: Path,
) -> list[SubagentFork]:
    """Find subagent spawns referenced by the parent session.

    STUB (Agent E): returns []. Agent E's implementation must:
    - Find records where record_type == "assistant" and the message
      content includes a tool_use with name == "Task"
    - For each, look for a sibling JSONL file under
      ``transcript_path.with_suffix("")/subagents/*.jsonl`` (the
      established convention per parse.py:1055 _load_subagent)
    - Match via the sibling ``.meta.json`` file's ``description`` field
      against the Task tool_use's ``input.description``
    - Resolve subagent_session_id from the child JSONL's sessionId
    """
    return []


# --------------------------------------------------------------------------- #
# Orchestrator + ingest entry point (coordinator-owned)
# --------------------------------------------------------------------------- #


def emit_context_tree_events_from_record(
    project_dir: Path,
    final_record: TraceRecord,
    transcript_path: Path | None = None,
) -> dict[str, Any]:
    """Emit Context Tree TrailEvents for one ingested session.

    Mirrors ``trails.emit_step_window_events_from_record`` in shape:
    called from ``core/ingest.py`` after the parsed record is stable
    but before reconciliation. Failures are logged and non-fatal.

    Returns a summary dict (node_count, layer_count, active_path_leaf_id,
    capture_limitations) for downstream consumers (doctor reports the
    aggregate).
    """
    summary = {
        "trace_id": final_record.trace_id,
        "node_count": 0,
        "layer_count": 0,
        "active_path_leaf_id": None,
        "capture_limitations": [],
    }

    if transcript_path is None:
        # Fall back to the trace record's source pointer when ingest
        # caller doesn't pass the path explicitly.
        transcript_path = _transcript_path_for(final_record)
    if transcript_path is None or not transcript_path.exists():
        summary["capture_limitations"].append("transcript_modified")
        return summary

    try:
        records = read_jsonl_records(transcript_path)
        graph = build_parent_graph(records)
        summary["active_path_leaf_id"] = graph.active_leaf_uuid

        init_record = _find_init_record(records)
        compactions = detect_compaction_boundaries(graph)
        orphans = detect_orphan_branches(graph)
        subagent_forks = detect_subagent_forks(graph, transcript_path)

        # Build the four layers for the whole active path (v1 approximation:
        # one layer set per session; Agent D will refine to per-step layers).
        active_path = graph.active_path()
        system_layer = build_system_layer(
            init_record=init_record,
            on_disk_claude_md=[],
            on_disk_memory_md=None,
            append_system_prompt_override=None,
        )
        messages_layer = build_messages_layer(active_path=active_path)
        tool_registry_layer = build_tool_registry_layer(
            init_record=init_record,
            on_disk_mcp_servers={},
            on_disk_skills=[],
        )
        runtime_state_layer = build_runtime_state_layer(
            init_record=init_record,
            on_disk_mcp_servers={},
        )

        # In v1, emit one ContextNode per active-path message-role record,
        # all pointing at the same four session-level layers. Per-step
        # layer differentiation lands when Agent D fills in the stubs.
        layers = [system_layer, messages_layer, tool_registry_layer, runtime_state_layer]
        nodes = _materialize_nodes_for_active_path(
            trace_id=final_record.trace_id,
            active_path=active_path,
            layers=layers,
            compactions=compactions,
            orphans=orphans,
            subagent_forks=subagent_forks,
        )

        # TODO: append events to refs/opentraces/local/events/v1 via
        # core.trails.event_log.append_event_batch once Agent D/E land
        # their content. For now we count what would be emitted.
        summary["node_count"] = len(nodes)
        summary["layer_count"] = len({layer.layer_id for layer in layers})

        if not nodes:
            summary["capture_limitations"].append("context_tree_not_captured")
        if not compactions and any(
            r.subtype == "compact_boundary" for r in records
        ):
            summary["capture_limitations"].append(
                "context_layer_unavailable"  # detector not yet wired
            )

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "context_tree capture failed for trace %s: %s",
            final_record.trace_id,
            exc,
            exc_info=True,
        )
        summary["capture_limitations"].append("context_tree_not_captured")

    return summary


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _find_init_record(records: list[JsonlRecord]) -> JsonlRecord | None:
    for rec in records:
        if rec.record_type == "system" and rec.subtype == "init":
            return rec
    return None


def _materialize_nodes_for_active_path(
    *,
    trace_id: str,
    active_path: list[JsonlRecord],
    layers: list[ContextLayer],
    compactions: list[CompactionBoundary],
    orphans: list[OrphanBranch],
    subagent_forks: list[SubagentFork],
) -> list[ContextNode]:
    """Build a flat list of ContextNodes for the active path.

    v1 approximation: every node points at the same four layers. Phase
    3's projection will diff layers across steps; Phase 2's job is
    just to get one node per active-path record on the event log.
    """
    system_id, messages_id, tool_id, runtime_id = (l.layer_id for l in layers)
    nodes: list[ContextNode] = []
    parent_node_id: str | None = None
    step_counter = 0
    for rec in active_path:
        if rec.record_type not in ("user", "assistant"):
            continue
        branch_type = "root" if parent_node_id is None else "linear"
        node = build_node(
            parent_node_id=parent_node_id,
            branch_type=branch_type,
            trace_id=trace_id,
            transcript_uuid=rec.uuid or f"_synthetic_{step_counter}",
            transcript_parent_uuid=rec.parent_uuid,
            transcript_offset=rec.offset,
            step_index=step_counter,
            system_layer_id=system_id,
            messages_layer_id=messages_id,
            tool_registry_layer_id=tool_id,
            runtime_state_layer_id=runtime_id,
            capture_completeness="approximated",
        )
        nodes.append(node)
        parent_node_id = node.node_id
        step_counter += 1
    return nodes


def _transcript_path_for(record: TraceRecord) -> Path | None:
    """Best-effort transcript path lookup from the record's metadata."""
    src = record.metadata.get("source_path") if hasattr(record, "metadata") else None
    if src:
        path = Path(src)
        if path.exists():
            return path
    return None


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_or_null(text: str | None) -> str | None:
    if text is None:
        return None
    return _sha256(text)

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
import os
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
    on_disk_claude_md: list[dict[str, Any]] | None = None,
    on_disk_memory_md: str | None = None,
    append_system_prompt_override: str | None = None,
    project_dir: Path | None = None,
    home_dir: Path | None = None,
    read_from_disk: bool = True,
    capture_limitations: list[str] | None = None,
) -> ContextLayer:
    """Reconstruct the system layer for the active path.

    Reads the Claude Code memory chain (user / project / local / managed
    CLAUDE.md plus ``.claude/rules/*.md``) at capture time, hashes each
    file body, and assembles the canonical ``system`` layer content.

    When ``read_from_disk`` is True, the on-disk variants are loaded and
    appended after any caller-supplied ``on_disk_claude_md`` entries
    (caller-supplied wins on duplicate paths). Missing files are skipped
    silently; their absence is documented via ``capture_limitations`` if
    a list is provided.

    Per https://code.claude.com/docs/en/memory the load order is:

      1. user-scope:    ``~/.claude/CLAUDE.md``
      2. project-scope: ``<project>/CLAUDE.md``
      3. local-scope:   ``<project>/.claude/CLAUDE.md``
      4. managed-scope: ``<project>/.claude/rules/*.md`` (sorted)
    """
    cwd = ""
    model = ""
    claude_code_version = "unknown"
    if init_record is not None:
        cwd = str(init_record.raw.get("cwd", ""))
        model = str(init_record.raw.get("model", ""))
        claude_code_version = str(
            init_record.raw.get("claude_code_version") or "unknown"
        )

    if project_dir is None and cwd:
        project_dir = Path(cwd)
    if home_dir is None:
        home_dir = Path.home()

    limitations = capture_limitations if capture_limitations is not None else []

    seen_paths: set[str] = set()
    claude_md_set: list[dict[str, Any]] = []
    for entry in on_disk_claude_md or []:
        path = str(entry.get("path", ""))
        if path and path not in seen_paths:
            seen_paths.add(path)
            claude_md_set.append(entry)

    if read_from_disk:
        candidates: list[tuple[Path, str, str]] = []
        # user scope
        candidates.append((home_dir / ".claude" / "CLAUDE.md", "user", "user_global"))
        if project_dir is not None:
            # project scope
            candidates.append((project_dir / "CLAUDE.md", "project", "project_root"))
            # local scope (checked-in or per-clone overrides)
            candidates.append(
                (project_dir / ".claude" / "CLAUDE.md", "local", "project_local")
            )
        for cand_path, scope, load_reason in candidates:
            entry = _read_claude_md_entry(cand_path, scope, load_reason)
            if entry is None:
                continue
            if entry["path"] in seen_paths:
                continue
            seen_paths.add(entry["path"])
            claude_md_set.append(entry)

        if project_dir is not None:
            rules_dir = project_dir / ".claude" / "rules"
            if rules_dir.is_dir():
                for rule_path in sorted(rules_dir.rglob("*.md")):
                    entry = _read_claude_md_entry(
                        rule_path, "managed", f"managed_rule:{rule_path.name}"
                    )
                    if entry is None:
                        continue
                    if entry["path"] in seen_paths:
                        continue
                    seen_paths.add(entry["path"])
                    claude_md_set.append(entry)

    memory_md_hash = _sha256_or_null(on_disk_memory_md)
    if memory_md_hash is None and read_from_disk:
        memory_md_text = _read_memory_md_head(project_dir, home_dir)
        memory_md_hash = _sha256_or_null(memory_md_text)

    append_hash = _sha256_or_null(append_system_prompt_override)
    if append_hash is None and init_record is not None:
        # Best effort: surface the override hash if the init record happens
        # to carry it. The CLI flag is not currently echoed into JSONL, so
        # this is mostly aspirational — we log a known v1 gap below.
        override_text = init_record.raw.get("append_system_prompt")
        if isinstance(override_text, str):
            append_hash = _sha256_or_null(override_text)
        elif "static_system_prompt_core_unknown" not in limitations:
            # Document the gap once per build.
            limitations.append("static_system_prompt_core_unknown")

    environment_block = json.dumps(
        {
            "cwd": cwd,
            "model": model,
            "claude_code_version": claude_code_version,
        },
        sort_keys=True,
    )

    return build_layer(
        layer_type="system",
        content={
            "static_core_ref": f"claude_code:{claude_code_version}",
            "claude_md_set": claude_md_set,
            "memory_md_head_hash": memory_md_hash,
            "append_system_prompt_hash": append_hash,
            "environment_block": environment_block,
        },
        completeness="approximated",
        capture_method="transcript_reconstruction",
    )


def build_messages_layer(
    *,
    active_path: list[JsonlRecord],
    is_summary: bool = False,
    summary_of_span: tuple[str, str] | None = None,
) -> ContextLayer:
    """Reconstruct the messages layer for the active path slice.

    Walks the active path and emits one entry per user/assistant record
    with role + uuid + parent_uuid + content_hash. ``content_hash`` is
    derived from the canonical JSON of the record's ``message`` payload
    so two records with identical messages collapse to the same hash.

    ``total_token_estimate`` uses a coarse ``len(text)//4`` heuristic
    over each message's user-visible text (string content, or the
    concatenation of every ``text`` block in a structured content list).
    Tool use / tool result blocks are also counted (their text payload
    contributes to context consumption even when not human-readable).
    """
    messages: list[dict[str, Any]] = []
    total_chars = 0
    for rec in active_path:
        if rec.record_type not in ("user", "assistant"):
            continue
        message_payload = rec.raw.get("message", {}) or {}
        role = message_payload.get("role") or rec.record_type or "unknown"
        content_text = _flatten_message_text(message_payload.get("content"))
        total_chars += len(content_text)
        messages.append(
            {
                "role": role,
                "uuid": rec.uuid or "",
                "parent_uuid": rec.parent_uuid,
                "content_hash": _sha256(
                    json.dumps(message_payload, sort_keys=True, ensure_ascii=False)
                ),
            }
        )

    span_first = messages[0]["uuid"] if messages else ""
    span_last = messages[-1]["uuid"] if messages else ""
    return build_layer(
        layer_type="messages",
        content={
            "messages": messages,
            "total_token_estimate": total_chars // 4,
            "span_first_uuid": span_first,
            "span_last_uuid": span_last,
            "is_summary": bool(is_summary),
            "summary_of_span": list(summary_of_span) if summary_of_span else None,
        },
        completeness="full",
        capture_method="transcript_reconstruction",
    )


def build_tool_registry_layer(
    *,
    init_record: JsonlRecord | None,
    on_disk_mcp_servers: dict[str, Any] | None = None,
    on_disk_skills: list[dict[str, Any]] | None = None,
    deferred_tools: list[str] | None = None,
    active_path: list[JsonlRecord] | None = None,
) -> ContextLayer:
    """Reconstruct the tool registry layer.

    Tools are sourced in canonical order so byte-identical inputs always
    produce the same layer_id:

    1. Built-ins from ``init_record.raw["tools"]`` (name + description
       only; ``input_schema`` is left ``None`` in v1 because Claude Code
       does not echo parameter schemas into the JSONL).
    2. MCP server tools from ``on_disk_mcp_servers`` (one row per tool
       per server; the dict shape is
       ``{server_name: {tool_names: [...]}}`` or
       ``{server_name: {tools: [{name, description}]}}``).
    3. Skill tools from ``on_disk_skills`` (each entry must carry at
       least ``name``; ``description`` and ``source_path`` are kept when
       present).

    ``deferred_tools`` is the caller-supplied list (typically from the
    orchestrator scanning ``deferred_tools_delta`` records). When
    ``active_path`` is supplied and ``deferred_tools`` is ``None``, we
    do a best-effort scan for deferred-tool deltas right here.
    """
    tools: list[dict[str, Any]] = []

    if init_record is not None:
        for tool in init_record.raw.get("tools", []) or []:
            if not isinstance(tool, dict):
                continue
            tools.append(
                {
                    "name": str(tool.get("name", "")),
                    "description": str(tool.get("description", "") or ""),
                    "input_schema": None,
                    "source": "builtin",
                    "source_path": None,
                }
            )

    for server_name in sorted((on_disk_mcp_servers or {}).keys()):
        server = on_disk_mcp_servers[server_name] or {}
        tool_entries: list[Any]
        if isinstance(server, dict) and isinstance(server.get("tools"), list):
            tool_entries = server["tools"]
        elif isinstance(server, dict) and isinstance(server.get("tool_names"), list):
            tool_entries = server["tool_names"]
        else:
            tool_entries = []
        for entry in tool_entries:
            if isinstance(entry, dict):
                tool_name = str(entry.get("name", ""))
                description = str(entry.get("description", "") or "")
            else:
                tool_name = str(entry)
                description = ""
            if not tool_name:
                continue
            tools.append(
                {
                    "name": f"mcp__{server_name}__{tool_name}",
                    "description": description,
                    "input_schema": None,
                    "source": "mcp",
                    "source_path": f"mcp_server:{server_name}",
                }
            )

    for skill in on_disk_skills or []:
        if not isinstance(skill, dict):
            continue
        skill_name = str(skill.get("name", ""))
        if not skill_name:
            continue
        tools.append(
            {
                "name": skill_name,
                "description": str(skill.get("description", "") or ""),
                "input_schema": None,
                "source": "skill",
                "source_path": skill.get("source_path"),
            }
        )

    if deferred_tools is None and active_path is not None:
        deferred_tools = _scan_deferred_tools(active_path)
    deferred_list = sorted({str(name) for name in (deferred_tools or [])})

    return build_layer(
        layer_type="tool_registry",
        content={
            "tools": tools,
            "deferred_tools": deferred_list,
        },
        completeness="approximated",
        capture_method="transcript_reconstruction",
    )


def build_runtime_state_layer(
    *,
    init_record: JsonlRecord | None,
    on_disk_mcp_servers: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    env_allowlist: tuple[str, ...] | None = None,
) -> ContextLayer:
    """Reconstruct the runtime state layer.

    Pulls ``cwd``, ``model``, ``permissionMode``, ``claude_code_version``
    and (when present) the ``effort.level`` setting from the
    ``system.init`` record. Environment values from the supplied ``env``
    map (defaults to ``os.environ``) are filtered through a conservative
    allowlist and stored as ``{KEY: sha256:<hex>}`` so raw values never
    land on the event log. ``mcp_servers`` is passed through verbatim;
    callers that have probed live MCP state can attach
    ``tool_names`` / ``tool_schemas`` there.

    ``capture_method`` is the weakest of the contributing sources:
    runtime metadata is reconstructed from the transcript even though
    the env block is captured live, so we tag the whole layer as
    ``transcript_reconstruction``.
    """
    cwd = ""
    model = ""
    permission_mode = "default"
    claude_code_version = "unknown"
    effort_level: str | None = None
    if init_record is not None:
        cwd = str(init_record.raw.get("cwd", "") or "")
        model = str(init_record.raw.get("model", "") or "")
        permission_mode = str(
            init_record.raw.get("permissionMode", "default") or "default"
        )
        claude_code_version = str(
            init_record.raw.get("claude_code_version") or "unknown"
        )
        effort_meta = init_record.raw.get("effort") or {}
        if isinstance(effort_meta, dict):
            level = effort_meta.get("level")
            if level is not None:
                effort_level = str(level)
        elif isinstance(effort_meta, str):
            effort_level = effort_meta

    env_source: dict[str, str]
    if env is not None:
        env_source = dict(env)
    else:
        env_source = dict(os.environ)
    allowlist = env_allowlist if env_allowlist is not None else DEFAULT_ENV_ALLOWLIST
    allowlisted_env: dict[str, str] = {}
    for key, value in env_source.items():
        if not _env_key_allowed(key, allowlist):
            continue
        if value is None:
            continue
        allowlisted_env[key] = _sha256(str(value))

    return build_layer(
        layer_type="runtime_state",
        content={
            "cwd": cwd,
            "permission_mode": permission_mode,
            "model": model,
            "claude_code_version": claude_code_version,
            "effort_level": effort_level,
            "allowlisted_env": dict(sorted(allowlisted_env.items())),
            "mcp_servers": on_disk_mcp_servers or {},
        },
        completeness="full",
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

    Walks ``graph.active_path()`` for records with
    ``record_type == "system" and subtype == "compact_boundary"``.
    For each boundary the synthetic ``isCompactSummary: true`` record
    that follows substitutes for the compacted span. ``pre_record_uuid``
    is the last active-path user/assistant record BEFORE the boundary;
    ``post_record_uuid`` is the first active-path user/assistant record
    AFTER the isCompactSummary record. The compacted span is the
    contiguous run of message-role records on the active path between
    the previous boundary (or root) and the pre_record_uuid, inclusive.
    """
    active = graph.active_path()
    boundaries: list[CompactionBoundary] = []

    boundary_positions: list[int] = [
        idx for idx, rec in enumerate(active)
        if rec.record_type == "system" and rec.subtype == "compact_boundary"
    ]
    if not boundary_positions:
        return boundaries

    # The lower bound of each compacted span: index just after the
    # previous boundary's summary record. For the first boundary it is
    # the start of the active path.
    prev_span_lower = 0
    for boundary_idx in boundary_positions:
        # pre_record_uuid: last user/assistant record on the active path
        # strictly before the compact_boundary record.
        pre_record_uuid: str | None = None
        for back_idx in range(boundary_idx - 1, prev_span_lower - 1, -1):
            rec = active[back_idx]
            if rec.record_type in ("user", "assistant") and rec.uuid:
                pre_record_uuid = rec.uuid
                break
        if pre_record_uuid is None:
            # Boundary with no preceding message-role record on the
            # active path; not a structurally meaningful compaction.
            # Skip but advance the lower bound past this boundary.
            prev_span_lower = boundary_idx + 1
            continue

        # Locate the synthetic isCompactSummary record immediately
        # after the boundary on the active path.
        summary_active_idx: int | None = None
        for fwd_idx in range(boundary_idx + 1, len(active)):
            rec = active[fwd_idx]
            if rec.raw.get("isCompactSummary") is True:
                summary_active_idx = fwd_idx
                break
            # Defensive: stop walking if we hit another boundary first
            # (the summary should come immediately after; if we miss
            # it the next boundary bounds our search).
            if rec.record_type == "system" and rec.subtype == "compact_boundary":
                break

        summary_record = (
            active[summary_active_idx] if summary_active_idx is not None else None
        )

        # post_record_uuid: first user/assistant record AFTER the
        # summary (or after the boundary itself if no summary was found).
        post_search_start = (
            summary_active_idx + 1
            if summary_active_idx is not None
            else boundary_idx + 1
        )
        post_record_uuid: str | None = None
        for fwd_idx in range(post_search_start, len(active)):
            rec = active[fwd_idx]
            if rec.record_type in ("user", "assistant") and rec.uuid:
                if rec.raw.get("isCompactSummary") is True:
                    continue
                post_record_uuid = rec.uuid
                break
        if post_record_uuid is None:
            # Compaction with no surviving post-boundary turns on the
            # active path. Still emit so the boundary count is correct;
            # consumers can detect the dangling case via empty post.
            post_record_uuid = ""

        # Compacted span: contiguous run of message-role records on the
        # active path from prev_span_lower up to (exclusive) the boundary.
        span_uuids: list[str] = []
        for span_idx in range(prev_span_lower, boundary_idx):
            rec = active[span_idx]
            if rec.record_type in ("user", "assistant") and rec.uuid:
                if rec.raw.get("isCompactSummary") is True:
                    # Don't include previous-compaction summaries in the
                    # next compaction's compacted span; they're already
                    # a summary substitution from a prior boundary.
                    continue
                span_uuids.append(rec.uuid)

        if span_uuids:
            compacted_first = span_uuids[0]
            compacted_last = span_uuids[-1]
        else:
            compacted_first = pre_record_uuid
            compacted_last = pre_record_uuid

        # Summary text hash: sha256 over the canonicalised message
        # payload of the isCompactSummary record. If no summary record
        # was found, hash the boundary's compact_metadata so the field
        # is still populated deterministically.
        if summary_record is not None:
            summary_payload = summary_record.raw.get("message", {})
        else:
            summary_payload = active[boundary_idx].raw.get("compact_metadata") or {}
        summary_text_hash = _sha256(
            json.dumps(summary_payload, sort_keys=True)
        )

        # v1 approximation: the post-compaction summary text is opaque,
        # so we treat the entire compacted span as "lossy diff removed".
        # A future iteration can do structural span recovery from the
        # summary text via NER or similar; the field shape will not
        # change, only the contents will shrink.
        lossy_diff_removed_uuids = list(span_uuids)

        boundaries.append(CompactionBoundary(
            pre_record_uuid=pre_record_uuid,
            post_record_uuid=post_record_uuid,
            compacted_span_first_uuid=compacted_first,
            compacted_span_last_uuid=compacted_last,
            summary_text_hash=summary_text_hash,
            lossy_diff_removed_uuids=lossy_diff_removed_uuids,
        ))

        # Advance the lower bound past this boundary's summary so the
        # next boundary's compacted span starts cleanly.
        prev_span_lower = (
            summary_active_idx + 1
            if summary_active_idx is not None
            else boundary_idx + 1
        )

    return boundaries


def detect_orphan_branches(graph: ParentGraph) -> list[OrphanBranch]:
    """Find rewound (Esc Esc) orphan branches in the parent graph.

    A rewind manifests as a parent with multiple children: one child's
    subtree lies on the active path, the others' subtrees are orphans.
    For each record whose parent has >= 2 children and whose own uuid
    is NOT on the active path, we treat that record as an orphan branch
    root and walk its descendants to collect the full subtree.
    """
    active_uuids: set[str] = set(graph.active_path_uuids)
    orphans: list[OrphanBranch] = []
    seen_roots: set[str] = set()

    for rec in graph.records:
        if not rec.uuid or not rec.parent_uuid:
            continue
        siblings = graph.children_of.get(rec.parent_uuid, [])
        if len(siblings) < 2:
            continue
        if rec.uuid in active_uuids:
            continue
        # The branch root must be the topmost off-path node in its
        # branch: its parent must be on the active path (otherwise an
        # ancestor will be the real branch root and we'd double-count
        # this descendant).
        if rec.parent_uuid not in active_uuids:
            continue
        if rec.uuid in seen_roots:
            continue
        seen_roots.add(rec.uuid)

        # Walk descendants iteratively to collect the full subtree.
        subtree: list[str] = []
        stack: list[str] = [rec.uuid]
        visited: set[str] = set()
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            subtree.append(current)
            for child in graph.children_of.get(current, []):
                if child not in visited:
                    stack.append(child)

        # Defensive: drop the subtree if it intersects the active path
        # (shouldn't happen by construction since we filtered above).
        if any(uuid in active_uuids for uuid in subtree):
            continue

        # Identify a leaf within the subtree: a uuid with no children.
        # Prefer the leaf appearing latest in file order, which is
        # typically the last record the user saw before rewinding.
        leaf_uuid = rec.uuid
        latest_offset = -1
        for uuid in subtree:
            if graph.children_of.get(uuid):
                continue
            child_rec = graph.by_uuid.get(uuid)
            if child_rec is None:
                continue
            if child_rec.offset > latest_offset:
                latest_offset = child_rec.offset
                leaf_uuid = uuid

        orphans.append(OrphanBranch(
            branch_root_uuid=rec.uuid,
            leaf_uuid=leaf_uuid,
            record_uuids_in_subtree=sorted(subtree),
        ))

    # Stable order keyed on branch_root_uuid for determinism.
    orphans.sort(key=lambda o: o.branch_root_uuid)
    return orphans


def detect_subagent_forks(
    graph: ParentGraph,
    transcript_path: Path,
) -> list[SubagentFork]:
    """Find subagent spawns referenced by the parent session.

    For each assistant record whose message content includes a ``Task``
    tool_use, look for a sibling JSONL under
    ``transcript_path.with_suffix("")/subagents/<stem>.jsonl`` paired
    with ``<stem>.meta.json``. The pairing convention matches
    ``parse.py::_load_subagent``: match the parent Task tool_use's
    ``input.description`` against the meta file's ``description`` field.
    When matched, resolve the child JSONL's ``sessionId`` from its
    ``system.init`` record. When unmatched, emit a SubagentFork with
    ``subagent_jsonl_path=None`` and an empty ``subagent_session_id``
    so the orchestrator can surface ``subagent_session_unreachable``
    via capture_limitations.
    """
    forks: list[SubagentFork] = []

    # Discover candidate Task tool_use records on the parent session.
    task_records: list[tuple[JsonlRecord, dict[str, Any]]] = []
    for rec in graph.records:
        if rec.record_type != "assistant" or not rec.uuid:
            continue
        message = rec.raw.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "Task":
                task_input = block.get("input") or {}
                if isinstance(task_input, dict):
                    task_records.append((rec, task_input))
                break  # one Task tool_use per assistant record is the norm

    if not task_records:
        return forks

    # Build the description -> jsonl_path map from the subagents/ dir.
    subagents_dir = transcript_path.with_suffix("") / "subagents"
    description_to_jsonl: dict[str, Path] = {}
    description_to_session_id: dict[str, str] = {}
    if subagents_dir.exists():
        for meta_file in sorted(subagents_dir.glob("*.meta.json")):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(meta, dict):
                continue
            description = meta.get("description")
            if not isinstance(description, str):
                continue
            # Resolve sibling JSONL: <stem>.meta.json -> <stem>.jsonl.
            jsonl_path = meta_file.with_suffix("").with_suffix(".jsonl")
            if not jsonl_path.exists():
                continue
            description_to_jsonl[description] = jsonl_path
            # Prefer the session_id from the JSONL's system.init record;
            # fall back to the meta file's session_id field if present.
            session_id = _read_subagent_session_id(jsonl_path)
            if session_id is None:
                meta_session_id = meta.get("session_id")
                if isinstance(meta_session_id, str):
                    session_id = meta_session_id
            if session_id:
                description_to_session_id[description] = session_id

    for rec, task_input in task_records:
        description = task_input.get("description")
        jsonl_path: Path | None = None
        subagent_session_id = ""
        if isinstance(description, str):
            jsonl_path = description_to_jsonl.get(description)
            subagent_session_id = description_to_session_id.get(description, "")
        forks.append(SubagentFork(
            parent_record_uuid=rec.uuid or "",
            subagent_session_id=subagent_session_id,
            subagent_jsonl_path=jsonl_path,
        ))

    # Stable order keyed on parent_record_uuid for determinism.
    forks.sort(key=lambda f: f.parent_record_uuid)
    return forks


def _read_subagent_session_id(jsonl_path: Path) -> str | None:
    """Extract sessionId from the first system.init line of a child JSONL.

    Best-effort: returns None on any read/parse error so callers can
    fall back to the meta.json's session_id or to an unreachable marker.
    """
    try:
        with jsonl_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                text = raw_line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(payload, dict)
                    and payload.get("type") == "system"
                    and payload.get("subtype") == "init"
                ):
                    session_id = payload.get("sessionId")
                    if isinstance(session_id, str):
                        return session_id
    except OSError:
        return None
    return None


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
            parent_graph=graph,
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
    parent_graph: ParentGraph | None = None,
) -> list[ContextNode]:
    """Build ContextNodes for the active path + branch attachments.

    Three passes:

    1. **Active-path pass.** One node per active-path message-role
       record. branch_type defaults to ``root`` (first node) / ``linear``,
       upgraded to ``compaction_fork`` when the record uuid matches a
       compaction boundary's ``post_record_uuid``.
    2. **Subagent fork pass.** For each detected subagent fork,
       attach a ``subagent_fork`` child node to the active-path node
       that corresponds to the Task tool_use's parent record uuid.
    3. **Orphan rewind pass.** For each detected orphan branch root,
       attach a ``rewind_branch`` node parented at the active-path
       node corresponding to the orphan's branch-divergence point
       (the orphan's transcript_parent_uuid). When ``parent_graph``
       is provided, the full orphan subtree is materialized as a
       chain of rewind_branch nodes.

    All nodes point at the same four session-level layers in v1;
    per-step layer differentiation lands in a follow-up Phase 3 task.
    """
    system_id, messages_id, tool_id, runtime_id = (l.layer_id for l in layers)
    nodes: list[ContextNode] = []
    uuid_to_node_id: dict[str, str] = {}

    # --- Pre-index detector results for O(1) lookups -----------------------

    post_to_compaction = {
        b.post_record_uuid: b for b in compactions if b.post_record_uuid
    }
    parent_uuid_to_subagent_fork: dict[str, SubagentFork] = {
        f.parent_record_uuid: f for f in subagent_forks if f.parent_record_uuid
    }

    # --- Pass 1: active path -----------------------------------------------

    parent_node_id: str | None = None
    step_counter = 0
    for rec in active_path:
        if rec.record_type not in ("user", "assistant"):
            continue
        if parent_node_id is None:
            branch_type = "root"
        elif rec.uuid and rec.uuid in post_to_compaction:
            branch_type = "compaction_fork"
        else:
            branch_type = "linear"
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
        if rec.uuid:
            uuid_to_node_id[rec.uuid] = node.node_id
        parent_node_id = node.node_id
        step_counter += 1

    # --- Pass 2: subagent forks --------------------------------------------
    # Each detected fork attaches as a child of the active-path node for
    # the Task tool_use's parent record. If that parent isn't on the
    # active path (rare, defensive), skip the fork rather than fabricate
    # a hanging node.

    for parent_uuid, fork in parent_uuid_to_subagent_fork.items():
        parent_node = uuid_to_node_id.get(parent_uuid)
        if parent_node is None:
            continue
        fork_node = build_node(
            parent_node_id=parent_node,
            branch_type="subagent_fork",
            trace_id=trace_id,
            transcript_uuid=fork.subagent_session_id,
            transcript_parent_uuid=parent_uuid,
            # Subagent JSONL is a separate file; offset 0 marks the
            # session start. Consumers join via subagent_session_id.
            transcript_offset=0,
            step_index=step_counter,
            system_layer_id=system_id,
            messages_layer_id=messages_id,
            tool_registry_layer_id=tool_id,
            runtime_state_layer_id=runtime_id,
            subagent_session_id=fork.subagent_session_id,
            capture_completeness="approximated",
        )
        nodes.append(fork_node)
        step_counter += 1

    # --- Pass 3: orphan rewind branches ------------------------------------
    # The orphan root's parent is the rewind anchor (a record on the
    # active path with multiple children). Materialize the orphan root
    # plus, when the parent_graph is available, walk the orphan subtree
    # to emit a chain of rewind_branch nodes.

    for orphan in orphans:
        # Root record of the orphan branch
        root_rec = (
            parent_graph.by_uuid.get(orphan.branch_root_uuid)
            if parent_graph is not None
            else None
        )
        if root_rec is None:
            continue
        anchor_uuid = root_rec.parent_uuid
        anchor_node_id = uuid_to_node_id.get(anchor_uuid) if anchor_uuid else None
        if anchor_node_id is None:
            # Rewind anchor not on the active path (extremely defensive);
            # skip rather than orphan-the-orphan.
            continue

        # Materialize one node per record in the orphan subtree, walking
        # from root to leaf. We rely on the detector's
        # record_uuids_in_subtree ordering (set into a stable order by
        # detect_orphan_branches).
        prev_node_id = anchor_node_id
        for sub_uuid in orphan.record_uuids_in_subtree:
            sub_rec = parent_graph.by_uuid.get(sub_uuid) if parent_graph else None
            if sub_rec is None or sub_rec.record_type not in ("user", "assistant"):
                continue
            sub_node = build_node(
                parent_node_id=prev_node_id,
                branch_type="rewind_branch",
                trace_id=trace_id,
                transcript_uuid=sub_uuid,
                transcript_parent_uuid=sub_rec.parent_uuid,
                transcript_offset=sub_rec.offset,
                step_index=step_counter,
                system_layer_id=system_id,
                messages_layer_id=messages_id,
                tool_registry_layer_id=tool_id,
                runtime_state_layer_id=runtime_id,
                capture_completeness="approximated",
            )
            nodes.append(sub_node)
            prev_node_id = sub_node.node_id
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


# --------------------------------------------------------------------------- #
# Layer-builder helpers
# --------------------------------------------------------------------------- #


# MEMORY.md head cap (per the goal prompt: first 200 lines OR 25KB,
# whichever is smaller).
_MEMORY_MD_HEAD_MAX_LINES = 200
_MEMORY_MD_HEAD_MAX_BYTES = 25 * 1024

# Conservative env allowlist for the runtime_state layer. Keys outside
# this list are dropped before hashing; never store raw values.
DEFAULT_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "SHELL",
    "TERM",
    "LANG",
    "LC_ALL",
    "USER",
    "HOME",
)

# Prefix-matched env keys (Claude Code + opentraces own knobs are safe
# to hash because we control them).
_DEFAULT_ENV_ALLOWLIST_PREFIXES: tuple[str, ...] = (
    "CLAUDE_",
    "OPENTRACES_",
)


def _env_key_allowed(key: str, allowlist: tuple[str, ...]) -> bool:
    if key in allowlist:
        return True
    return any(key.startswith(prefix) for prefix in _DEFAULT_ENV_ALLOWLIST_PREFIXES)


def _flatten_message_text(content: Any) -> str:
    """Flatten a Claude message ``content`` field to a single text blob.

    Handles all three shapes seen in the JSONL:
    - plain string ("trimmed the trailing whitespace.")
    - list of {type, text|content|input|...} blocks (typical assistant turn)
    - missing / unexpected types -> empty string
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif kind == "tool_use":
                input_payload = block.get("input")
                if isinstance(input_payload, dict):
                    parts.append(
                        json.dumps(input_payload, sort_keys=True, ensure_ascii=False)
                    )
                elif isinstance(input_payload, str):
                    parts.append(input_payload)
            elif kind == "tool_result":
                result_content = block.get("content")
                if isinstance(result_content, str):
                    parts.append(result_content)
                elif isinstance(result_content, list):
                    parts.append(_flatten_message_text(result_content))
        return "\n".join(parts)
    return ""


def _read_claude_md_entry(
    path: Path, scope: str, load_reason: str
) -> dict[str, Any] | None:
    """Read one CLAUDE.md candidate; return None if absent or unreadable."""
    try:
        if not path.is_file():
            return None
        body = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return {
        "path": str(path),
        "content_hash": _sha256(body),
        "scope": scope,
        "load_reason": load_reason,
    }


def _read_memory_md_head(
    project_dir: Path | None, home_dir: Path
) -> str | None:
    """Return the MEMORY.md head text (capped at 200 lines or 25KB).

    Best-effort: walks ``~/.claude/projects/`` looking for a project
    folder whose decoded path matches ``project_dir``. Returns None when
    the projects directory or matching project is absent.
    """
    if project_dir is None:
        return None
    projects_root = home_dir / ".claude" / "projects"
    if not projects_root.is_dir():
        return None
    target_resolved = str(project_dir.resolve())
    encoded_segments = {
        target_resolved.replace("/", "-"),
        target_resolved.lstrip("/").replace("/", "-"),
    }
    candidate_dirs: list[Path] = []
    for entry in projects_root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name in encoded_segments or entry.name == project_dir.name:
            candidate_dirs.append(entry)
    for cand in candidate_dirs:
        mem_path = cand / "memory" / "MEMORY.md"
        if not mem_path.is_file():
            continue
        try:
            body = mem_path.read_text(encoding="utf-8")
        except OSError:
            continue
        head_lines = body.splitlines(keepends=True)[:_MEMORY_MD_HEAD_MAX_LINES]
        head = "".join(head_lines)
        if len(head.encode("utf-8")) > _MEMORY_MD_HEAD_MAX_BYTES:
            head = head.encode("utf-8")[:_MEMORY_MD_HEAD_MAX_BYTES].decode(
                "utf-8", errors="ignore"
            )
        return head
    return None


def _scan_deferred_tools(active_path: list[JsonlRecord]) -> list[str]:
    """Best-effort scan for deferred-tools deltas on the active path.

    Claude Code does not have a single canonical record type for deferred
    tools yet; we look for any record whose ``subtype`` mentions deferred
    tools or whose ``data`` payload carries a ``deferred_tools`` list.
    """
    found: set[str] = set()
    for rec in active_path:
        raw = rec.raw
        # Direct subtype hit.
        if rec.subtype in {"deferred_tools_delta", "deferred_tools"}:
            payload = raw.get("data") or raw.get("payload") or raw
            tools = payload.get("deferred_tools") if isinstance(payload, dict) else None
            if isinstance(tools, list):
                found.update(str(t) for t in tools if t)
            continue
        # Hook-style payload sniff.
        data = raw.get("data")
        if isinstance(data, dict):
            tools = data.get("deferred_tools")
            if isinstance(tools, list):
                found.update(str(t) for t in tools if t)
    return sorted(found)

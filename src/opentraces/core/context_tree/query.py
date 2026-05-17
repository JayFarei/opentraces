"""Context Tree query projection.

Mirrors Trail's ``core/trails/query.py::TrailQueryProjection`` pattern:
a frozen dataclass holding indexed dicts that consumers query without
hitting the event log on every call. Built in a single pass from
``read_events(repo)`` over the canonical event log.

Phase 3 deliverable, per ``kb/plans/077-context-tree-substrate.md``
§"Search". Used by the ``opentraces ctx`` CLI verbs (tree, show,
step, reads, writes, diff, compactions) and by the resume/prune
helpers. No git I/O during query; survival is implicit in the
parent-graph + active-path structure stored on disk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..trails.event_log import read_events
from .contract import (
    CONTEXT_COMPACTION_OBSERVED,
    CONTEXT_LAYER_CAPTURED,
    CONTEXT_NODE_OBSERVED,
    CONTEXT_TREE_RECONCILED,
)
from .models import ContextLayer, ContextNode


# --------------------------------------------------------------------------- #
# Projection
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ContextTreeProjection:
    """Indexed view over Context Tree events on the canonical log.

    Build via ``build_context_tree_projection(repo)``; do not mutate
    after construction (frozen). Consumers query via the helper
    methods rather than reaching into the dicts directly.
    """

    nodes_by_id: dict[str, ContextNode] = field(default_factory=dict)
    layers_by_id: dict[str, ContextLayer] = field(default_factory=dict)
    nodes_by_trace: dict[str, list[str]] = field(default_factory=dict)
    active_leaves_by_trace: dict[str, str | None] = field(default_factory=dict)
    children_of: dict[str, list[str]] = field(default_factory=dict)
    # (pre_node_id, post_node_id) pairs per trace_id, in event order
    compactions_by_trace: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    # full compaction event payloads for richer queries
    compaction_events_by_trace: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # orphan branch roots (rewind subtree starting points) per trace
    orphan_branch_roots_by_trace: dict[str, list[str]] = field(default_factory=dict)
    # subagent session ids referenced by parent ContextNodes per trace
    subagent_session_ids_by_trace: dict[str, list[str]] = field(default_factory=dict)
    capture_limitations_by_trace: dict[str, list[str]] = field(default_factory=dict)

    # ----------------------------------------------------------------- #
    # Lookup helpers
    # ----------------------------------------------------------------- #

    def node_for_step(self, trace_id: str, step_index: int) -> ContextNode | None:
        for node_id in self.nodes_by_trace.get(trace_id, []):
            node = self.nodes_by_id.get(node_id)
            if node is not None and node.step_index == step_index:
                return node
        return None

    def node_for_transcript_uuid(
        self, trace_id: str, transcript_uuid: str
    ) -> ContextNode | None:
        for node_id in self.nodes_by_trace.get(trace_id, []):
            node = self.nodes_by_id.get(node_id)
            if node is not None and node.transcript_uuid == transcript_uuid:
                return node
        return None

    def active_path(
        self, trace_id: str, leaf_id: str | None = None
    ) -> list[ContextNode]:
        """Walk from a leaf node back to root, returned root-first.

        When ``leaf_id`` is None, the trace's active_leaf_id (last node
        emitted on the linear active path) is used.
        """
        target_leaf_node: ContextNode | None = None
        if leaf_id is not None:
            target_leaf_node = self.nodes_by_id.get(leaf_id)
        else:
            active_leaf_uuid = self.active_leaves_by_trace.get(trace_id)
            if active_leaf_uuid:
                target_leaf_node = self.node_for_transcript_uuid(
                    trace_id, active_leaf_uuid
                )
            if target_leaf_node is None:
                # Fallback: linear-branch leaf is the last node on the
                # trace whose branch_type is in {root, linear, compaction_fork}.
                for node_id in reversed(self.nodes_by_trace.get(trace_id, [])):
                    node = self.nodes_by_id.get(node_id)
                    if node and node.branch_type in (
                        "root", "linear", "compaction_fork"
                    ):
                        target_leaf_node = node
                        break
        if target_leaf_node is None:
            return []
        path: list[ContextNode] = []
        visited: set[str] = set()
        cur: ContextNode | None = target_leaf_node
        while cur is not None and cur.node_id not in visited:
            visited.add(cur.node_id)
            path.append(cur)
            cur = (
                self.nodes_by_id.get(cur.parent_node_id)
                if cur.parent_node_id
                else None
            )
        path.reverse()
        return path

    def nodes_referencing_layer(self, layer_id: str) -> list[ContextNode]:
        return [
            n for n in self.nodes_by_id.values()
            if layer_id in (
                n.system_layer_id,
                n.messages_layer_id,
                n.tool_registry_layer_id,
                n.runtime_state_layer_id,
            )
        ]

    def subagent_subtree(self, parent_node_id: str) -> list[ContextNode]:
        """All ContextNodes reachable from parent_node_id via subagent_fork."""
        results: list[ContextNode] = []
        stack: list[str] = [parent_node_id]
        seen: set[str] = set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            for child_id in self.children_of.get(cur, []):
                child = self.nodes_by_id.get(child_id)
                if child is None:
                    continue
                if child.branch_type == "subagent_fork":
                    results.append(child)
                stack.append(child_id)
        return results

    def orphan_subtree(self, orphan_root_node_id: str) -> list[ContextNode]:
        """All ContextNodes in the rewind orphan subtree rooted at the given node."""
        results: list[ContextNode] = []
        stack: list[str] = [orphan_root_node_id]
        seen: set[str] = set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            node = self.nodes_by_id.get(cur)
            if node is None:
                continue
            if cur != orphan_root_node_id and node.branch_type != "rewind_branch":
                # Don't cross out of the orphan subtree
                continue
            results.append(node)
            stack.extend(self.children_of.get(cur, []))
        return results

    def layer_diff(
        self, node_a_id: str, node_b_id: str
    ) -> dict[str, Any]:
        """Diff the layer references of two ContextNodes.

        Returns a dict with keys per layer_type indicating whether the
        layer changed between A and B. For layer types where the layer
        differs, both layer_ids and a content-level diff (added/removed
        top-level keys) are surfaced.
        """
        node_a = self.nodes_by_id.get(node_a_id)
        node_b = self.nodes_by_id.get(node_b_id)
        if node_a is None or node_b is None:
            return {
                "added": [],
                "removed": [],
                "changed": [],
                "error": "node not found",
            }
        layer_fields = (
            ("system", "system_layer_id"),
            ("messages", "messages_layer_id"),
            ("tool_registry", "tool_registry_layer_id"),
            ("runtime_state", "runtime_state_layer_id"),
        )
        changed: list[dict[str, Any]] = []
        for layer_type, attr in layer_fields:
            a_id = getattr(node_a, attr)
            b_id = getattr(node_b, attr)
            if a_id == b_id:
                continue
            a_layer = self.layers_by_id.get(a_id)
            b_layer = self.layers_by_id.get(b_id)
            entry: dict[str, Any] = {
                "layer_type": layer_type,
                "before_layer_id": a_id,
                "after_layer_id": b_id,
            }
            if a_layer is not None and b_layer is not None:
                a_keys = set(a_layer.content.keys())
                b_keys = set(b_layer.content.keys())
                entry["added_keys"] = sorted(b_keys - a_keys)
                entry["removed_keys"] = sorted(a_keys - b_keys)
            changed.append(entry)
        return {
            "added": [],     # for symmetry with TrailQueryProjection style
            "removed": [],
            "changed": changed,
        }

    def compaction_loss(
        self, trace_id: str, index: int
    ) -> dict[str, Any]:
        """Return the lossy-diff structure for compaction[index] in a trace."""
        events = self.compaction_events_by_trace.get(trace_id, [])
        if not events or index < 0 or index >= len(events):
            return {"error": "compaction index out of range", "trace_id": trace_id, "index": index}
        evt = events[index]
        return {
            "trace_id": trace_id,
            "index": index,
            "pre_node_id": evt.get("pre_node_id"),
            "post_node_id": evt.get("post_node_id"),
            "compacted_span_first_uuid": evt.get("compacted_span_first_uuid"),
            "compacted_span_last_uuid": evt.get("compacted_span_last_uuid"),
            "summary_text_hash": evt.get("summary_text_hash"),
            "removed_message_uuids": evt.get("lossy_diff_removed_uuids", []),
        }

    def reads_for_trace(
        self,
        trace_id: str,
        from_step: int | None = None,
        to_step: int | None = None,
    ) -> list[dict[str, Any]]:
        """Project the read history (tool_result records) for a trace.

        Walks the active path's messages layer for ``trace_id`` and
        emits one entry per tool_result record, scoped to the optional
        step range.
        """
        return _project_io_for_trace(
            self, trace_id, from_step, to_step, direction="read"
        )

    def writes_for_trace(
        self,
        trace_id: str,
        from_step: int | None = None,
        to_step: int | None = None,
    ) -> list[dict[str, Any]]:
        """Project the write history (Edit/Write tool_use records) for a trace."""
        return _project_io_for_trace(
            self, trace_id, from_step, to_step, direction="write"
        )

    def summary(self) -> dict[str, Any]:
        """One-shot summary for ``opentraces doctor`` and similar."""
        return {
            "trace_count": len(self.nodes_by_trace),
            "node_count": len(self.nodes_by_id),
            "layer_count": len(self.layers_by_id),
            "compaction_count": sum(
                len(v) for v in self.compactions_by_trace.values()
            ),
            "orphan_branch_count": sum(
                len(v) for v in self.orphan_branch_roots_by_trace.values()
            ),
            "subagent_session_count": sum(
                len(v) for v in self.subagent_session_ids_by_trace.values()
            ),
            "capture_limitations": _deduped_limitations(
                self.capture_limitations_by_trace.values()
            ),
        }


# --------------------------------------------------------------------------- #
# Builder
# --------------------------------------------------------------------------- #


def build_context_tree_projection(repo: Path) -> ContextTreeProjection:
    """Read the canonical event log and project Context Tree events.

    Single-pass over ``read_events(repo)``; no git operations beyond
    what ``read_events`` already does (process-level cached).
    """
    nodes_by_id: dict[str, ContextNode] = {}
    layers_by_id: dict[str, ContextLayer] = {}
    nodes_by_trace: dict[str, list[str]] = {}
    active_leaves_by_trace: dict[str, str | None] = {}
    children_of: dict[str, list[str]] = {}
    compactions_by_trace: dict[str, list[tuple[str, str]]] = {}
    compaction_events_by_trace: dict[str, list[dict[str, Any]]] = {}
    orphan_branch_roots_by_trace: dict[str, list[str]] = {}
    subagent_session_ids_by_trace: dict[str, list[str]] = {}
    capture_limitations_by_trace: dict[str, list[str]] = {}

    for event in read_events(repo):
        et = event.event_type
        if et == CONTEXT_LAYER_CAPTURED:
            try:
                layer = ContextLayer.model_validate(event.payload)
            except Exception:
                continue
            layers_by_id.setdefault(layer.layer_id, layer)
        elif et == CONTEXT_NODE_OBSERVED:
            try:
                node = ContextNode.model_validate(event.payload)
            except Exception:
                continue
            nodes_by_id.setdefault(node.node_id, node)
            nodes_by_trace.setdefault(node.trace_id, []).append(node.node_id)
            if node.parent_node_id:
                children_of.setdefault(node.parent_node_id, []).append(node.node_id)
            if node.branch_type == "rewind_branch":
                orphan_branch_roots_by_trace.setdefault(node.trace_id, []).append(
                    node.node_id
                )
            if node.subagent_session_id:
                subagent_session_ids_by_trace.setdefault(node.trace_id, []).append(
                    node.subagent_session_id
                )
        elif et == CONTEXT_COMPACTION_OBSERVED:
            trace_id = event.trace_id or event.payload.get("trace_id")
            if not trace_id:
                continue
            pre = event.payload.get("pre_node_id")
            post = event.payload.get("post_node_id")
            if pre and post:
                compactions_by_trace.setdefault(trace_id, []).append((pre, post))
            compaction_events_by_trace.setdefault(trace_id, []).append(
                dict(event.payload)
            )
        elif et == CONTEXT_TREE_RECONCILED:
            trace_id = event.trace_id or event.payload.get("trace_id")
            if not trace_id:
                continue
            leaf = event.payload.get("active_path_leaf_id")
            active_leaves_by_trace[trace_id] = leaf
            for lim in event.payload.get("capture_limitations", []) or []:
                if isinstance(lim, str):
                    capture_limitations_by_trace.setdefault(trace_id, []).append(lim)

    return ContextTreeProjection(
        nodes_by_id=nodes_by_id,
        layers_by_id=layers_by_id,
        nodes_by_trace=nodes_by_trace,
        active_leaves_by_trace=active_leaves_by_trace,
        children_of=children_of,
        compactions_by_trace=compactions_by_trace,
        compaction_events_by_trace=compaction_events_by_trace,
        orphan_branch_roots_by_trace=orphan_branch_roots_by_trace,
        subagent_session_ids_by_trace=subagent_session_ids_by_trace,
        capture_limitations_by_trace=capture_limitations_by_trace,
    )


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _project_io_for_trace(
    projection: ContextTreeProjection,
    trace_id: str,
    from_step: int | None,
    to_step: int | None,
    *,
    direction: str,
) -> list[dict[str, Any]]:
    """Walk the active path's messages layer and project reads or writes.

    ``direction == "read"`` emits tool_result entries (environment to agent);
    ``direction == "write"`` emits Edit/Write/MultiEdit tool_use entries
    (agent to environment). Both look at the per-step ContextNode's
    messages_layer content (assembled by Agent D's
    ``build_messages_layer``); since v1 uses one session-level layer,
    each ContextNode shares the same messages_layer_id and the
    projection deduplicates accordingly.
    """
    write_tool_names = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
    nodes = [
        projection.nodes_by_id[node_id]
        for node_id in projection.nodes_by_trace.get(trace_id, [])
        if node_id in projection.nodes_by_id
    ]
    if from_step is not None:
        nodes = [n for n in nodes if (n.step_index or 0) >= from_step]
    if to_step is not None:
        nodes = [n for n in nodes if (n.step_index or 0) <= to_step]

    seen_layer_ids: set[str] = set()
    entries: list[dict[str, Any]] = []
    for node in nodes:
        if node.messages_layer_id in seen_layer_ids:
            continue
        seen_layer_ids.add(node.messages_layer_id)
        layer = projection.layers_by_id.get(node.messages_layer_id)
        if layer is None:
            continue
        for msg in layer.content.get("messages", []) or []:
            role = msg.get("role")
            uuid = msg.get("uuid")
            if direction == "read" and role == "user":
                # In Claude Code, tool_result messages are role=user;
                # raw content_hash is what we have at the layer level.
                entries.append({
                    "step_index": node.step_index,
                    "node_id": node.node_id,
                    "uuid": uuid,
                    "role": role,
                    "content_hash": msg.get("content_hash"),
                })
            elif direction == "write" and role == "assistant":
                # The layer doesn't preserve tool_use names yet in v1;
                # consumers that need names should fall back to the
                # original JSONL via transcript_offset. We surface the
                # node + offset so they can do so cheaply.
                entries.append({
                    "step_index": node.step_index,
                    "node_id": node.node_id,
                    "uuid": uuid,
                    "role": role,
                    "content_hash": msg.get("content_hash"),
                    "transcript_offset": node.transcript_offset,
                })
    return entries


def _deduped_limitations(seqs: Iterable[list[str]]) -> list[str]:
    out: set[str] = set()
    for seq in seqs:
        for s in seq:
            if s:
                out.add(s)
    return sorted(out)


def context_tree_summary(repo: Path) -> dict[str, Any]:
    """Convenience function for ``opentraces doctor``."""
    projection = build_context_tree_projection(repo)
    return projection.summary()

"""Deterministic burst detection over Trace Maps.

Cluster B / Plan 54 trace-map projections.

A *change burst* is a contiguous cluster of code-change nodes
(``file_edit`` and ``patch_created``) whose step indexes are within
``gap`` of one another. Each burst is exposed as a virtual
``change_burst`` ``TraceMapNode`` carrying:

* ``step_range`` — ``[min_step, max_step]`` of the underlying nodes
* ``intent_user_step`` / ``intent_text`` — the ``user_instruction``
  active for the first edit (via ``active_user_step``)
* ``unique_files`` — repo-relative path -> edit count
* ``patches`` — ``[{patch_id, git_anchor_id, commit_sha,
  evidence_firmness, evidence_tier}, ...]`` aggregated from the
  contained ``patch_created`` nodes
* ``unique_git_anchors`` / ``has_git_anchor`` — convenience accessors
  for the lineage downstream

The algorithm is deterministic so callers can compare bursts across
runs. ``patch_created`` nodes are co-located in the same burst as the
``file_edit`` nodes they were derived from when their ``step_index``
falls within ``gap`` of the cluster (typical of post-commit anchoring).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from opentraces_schema import TraceMap, TraceMapEdge, TraceMapNode


DEFAULT_BURST_GAP = 35
"""Default ``step_index`` gap for the burst projection.

Empirically the value the canonical ``--bursts`` flag uses. Configurable
via the ``--burst-gap`` CLI flag and the ``gap`` argument here.
"""


_BURST_INPUT_TYPES = {"file_edit", "patch_created"}


@dataclass
class Burst:
    """A single change burst.

    The ``patches`` and ``unique_files`` shapes are deliberately
    JSON-friendly: the ``--bursts`` CLI surfaces them verbatim through
    ``metadata`` on a virtual ``change_burst`` node so consumers can
    pull them with one ``jq`` expression.
    """

    step_range: list[int]
    intent_user_step: int | None
    intent_text: str | None
    unique_files: dict[str, int] = field(default_factory=dict)
    patches: list[dict[str, Any]] = field(default_factory=list)
    unique_git_anchors: list[str] = field(default_factory=list)
    has_git_anchor: bool = False
    # Internal: the contributing node ids from the source trace map.
    contributing_node_ids: list[str] = field(default_factory=list)

    def to_metadata(self) -> dict[str, Any]:
        """Serialize to the JSON shape exposed in CLI output."""
        return {
            "step_range": list(self.step_range),
            "intent_user_step": self.intent_user_step,
            "intent_text": self.intent_text,
            "unique_files": dict(self.unique_files),
            "patches": [dict(p) for p in self.patches],
            "unique_git_anchors": list(self.unique_git_anchors),
            "has_git_anchor": self.has_git_anchor,
        }


def detect_bursts(
    trace_map: TraceMap,
    *,
    gap: int = DEFAULT_BURST_GAP,
) -> list[Burst]:
    """Cluster file_edit / patch_created nodes by step proximity.

    Parameters
    ----------
    trace_map:
        The source Trace Map. Only the nodes whose ``action_type`` is in
        ``{"file_edit", "patch_created"}`` are considered.
    gap:
        Maximum allowable step-index distance between two consecutive
        nodes for them to share the same burst. ``gap=1`` means strict
        adjacency, ``gap=200`` collapses everything within a long
        session.

    Returns
    -------
    Ordered list of :class:`Burst`. Order matches ``step_range[0]``
    ascending.
    """

    if not trace_map.nodes:
        return []
    if gap < 1:
        gap = 1

    candidates: list[TraceMapNode] = [
        node for node in trace_map.nodes if node.action_type in _BURST_INPUT_TYPES and node.step_index is not None
    ]
    if not candidates:
        return []

    # Stable sort by step_index, then by node order to keep deterministic.
    candidates.sort(key=lambda n: (n.step_index or 0, _node_ordinal(trace_map, n.node_id)))

    # Map preview text by node_id so we can look up intent without scanning.
    user_text_by_step: dict[int, str | None] = {
        node.step_index: node.text_preview
        for node in trace_map.nodes
        if node.action_type == "user_instruction" and node.step_index is not None
    }

    bursts: list[Burst] = []
    current: list[TraceMapNode] = []
    last_step: int | None = None

    def _flush() -> None:
        if not current:
            return
        bursts.append(_make_burst(current, user_text_by_step))

    for node in candidates:
        step = node.step_index or 0
        if last_step is not None and (step - last_step) > gap:
            _flush()
            current = []
        current.append(node)
        last_step = step
    _flush()
    return bursts


def bursts_to_trace_map(
    source: TraceMap,
    bursts: list[Burst],
) -> TraceMap:
    """Project a list of :class:`Burst` into a TraceMap of ``change_burst`` nodes.

    Edges are emitted as ``previous_next`` between consecutive bursts so
    consumers can traverse them in step order.
    """

    nodes: list[TraceMapNode] = []
    edges: list[TraceMapEdge] = []
    previous: TraceMapNode | None = None
    trace_id = source.trace_id

    for ordinal, burst in enumerate(bursts, 1):
        unique_files_sum = sum(burst.unique_files.values()) or 0
        node = TraceMapNode(
            node_id=f"tmn:{trace_id}:burst:{ordinal}",
            trace_id=trace_id,
            unit_id=f"tu:{trace_id}:burst:{ordinal}",
            action_type="change_burst",
            step_index=burst.step_range[0] if burst.step_range else None,
            start_step_index=burst.step_range[0] if burst.step_range else None,
            end_step_index=burst.step_range[1] if burst.step_range else None,
            previous_node_id=previous.node_id if previous else None,
            files_modified=sorted(burst.unique_files.keys()),
            anchor_refs=list(burst.unique_git_anchors),
            text_preview=burst.intent_text,
            metadata=burst.to_metadata() | {
                "edit_count": unique_files_sum,
                "contributing_node_ids": list(burst.contributing_node_ids),
            },
            active_user_step=burst.intent_user_step,
        )
        if previous:
            previous.next_node_id = node.node_id
            edges.append(
                TraceMapEdge(
                    edge_id=f"tme:{trace_id}:burst:{len(edges) + 1}",
                    trace_id=trace_id,
                    source_node_id=previous.node_id,
                    target_node_id=node.node_id,
                    edge_type="previous_next",
                )
            )
        nodes.append(node)
        previous = node

    return TraceMap(
        trace_id=trace_id,
        root_node_ids=[nodes[0].node_id] if nodes else [],
        nodes=nodes,
        edges=edges,
        limitations=source.limitations,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _node_ordinal(trace_map: TraceMap, node_id: str) -> int:
    for index, node in enumerate(trace_map.nodes):
        if node.node_id == node_id:
            return index
    return len(trace_map.nodes)


def _make_burst(
    nodes: list[TraceMapNode],
    user_text_by_step: dict[int, str | None],
) -> Burst:
    steps = [n.step_index for n in nodes if n.step_index is not None]
    step_range = [min(steps), max(steps)] if steps else [0, 0]

    # Intent comes from the active_user_step of the first node in the burst.
    first = nodes[0]
    intent_step = first.active_user_step
    intent_text: str | None = None
    if intent_step is not None:
        text = user_text_by_step.get(intent_step)
        if text:
            intent_text = text[:300]

    unique_files: dict[str, int] = {}
    patches: list[dict[str, Any]] = []
    unique_git_anchors: list[str] = []
    seen_anchors: set[str] = set()

    for node in nodes:
        for path in node.files_modified:
            unique_files[path] = unique_files.get(path, 0) + 1
        if node.action_type == "patch_created":
            meta = node.metadata or {}
            patch_id = meta.get("trace_patch_id") or meta.get("patch_id")
            anchor_id = meta.get("git_anchor_id")
            patches.append(
                {
                    "patch_id": patch_id,
                    "git_anchor_id": anchor_id,
                    "commit_sha": meta.get("commit_sha"),
                    "evidence_firmness": meta.get("evidence_firmness"),
                    "evidence_tier": meta.get("evidence_tier"),
                }
            )
            if isinstance(anchor_id, str) and anchor_id not in seen_anchors:
                seen_anchors.add(anchor_id)
                unique_git_anchors.append(anchor_id)

    return Burst(
        step_range=step_range,
        intent_user_step=intent_step,
        intent_text=intent_text,
        unique_files=unique_files,
        patches=patches,
        unique_git_anchors=unique_git_anchors,
        has_git_anchor=bool(unique_git_anchors),
        contributing_node_ids=[n.node_id for n in nodes],
    )

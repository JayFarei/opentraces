"""Cluster H T2 — adaptive burst gap.

Per-trace, the burst detector picks ``gap`` from the median
step-distance between consecutive ``file_edit`` / ``patch_created``
nodes (median × 4, floored at the default 35, capped at 100). When
the caller passes an explicit ``gap`` value, it overrides the
adaptive choice unconditionally. When there are too few candidate
edits to compute a delta, the adaptive helper falls back to
``DEFAULT_BURST_GAP``.
"""

from __future__ import annotations

from typing import Any

import pytest

from opentraces_schema import TraceMap, TraceMapEdge, TraceMapNode

from opentraces.core.bursts import (
    ADAPTIVE_GAP_MAX,
    DEFAULT_BURST_GAP,
    _compute_adaptive_gap,
    detect_bursts,
)


def _node(
    *,
    ordinal: int,
    trace_id: str,
    action_type: str,
    step_index: int,
    files_modified: list[str] | None = None,
) -> TraceMapNode:
    return TraceMapNode(
        node_id=f"tmn:{trace_id}:{ordinal}",
        trace_id=trace_id,
        unit_id=f"tu:{trace_id}:{ordinal}",
        action_type=action_type,  # type: ignore[arg-type]
        step_index=step_index,
        start_step_index=step_index,
        end_step_index=step_index,
        files_modified=files_modified or [],
    )


def _trace_map(trace_id: str, nodes: list[TraceMapNode]) -> TraceMap:
    edges: list[TraceMapEdge] = []
    for index, node in enumerate(nodes[1:], 1):
        previous = nodes[index - 1]
        node.previous_node_id = previous.node_id
        previous.next_node_id = node.node_id
        edges.append(
            TraceMapEdge(
                edge_id=f"tme:{trace_id}:{index}",
                trace_id=trace_id,
                source_node_id=previous.node_id,
                target_node_id=node.node_id,
                edge_type="previous_next",
            )
        )
    return TraceMap(
        trace_id=trace_id,
        root_node_ids=[nodes[0].node_id] if nodes else [],
        nodes=nodes,
        edges=edges,
    )


def test_adaptive_gap_for_sparse_trace_widens() -> None:
    """3 edits at steps 50, 130, 200 → median delta ~75; adaptive gap caps at 100.

    The default 35 gap would split this into 3 bursts (deltas 80, 70
    both > 35). The adaptive widening (median × 4 = 300, clamped to
    100) collapses them into 1 burst (deltas ≤ 100).
    """
    trace_id = "t-sparse"
    nodes = [
        _node(ordinal=1, trace_id=trace_id, action_type="file_edit",
              step_index=50, files_modified=["a.py"]),
        _node(ordinal=2, trace_id=trace_id, action_type="file_edit",
              step_index=130, files_modified=["a.py"]),
        _node(ordinal=3, trace_id=trace_id, action_type="file_edit",
              step_index=200, files_modified=["a.py"]),
    ]
    cands = [n for n in nodes if n.action_type == "file_edit"]
    adaptive = _compute_adaptive_gap(cands)
    assert adaptive > DEFAULT_BURST_GAP, (
        f"adaptive gap should widen for sparse trace (got {adaptive})"
    )
    assert adaptive <= ADAPTIVE_GAP_MAX
    # And the burst detector with the adaptive default emits 1 burst.
    bursts = detect_bursts(_trace_map(trace_id, nodes), commit_lookup=False)
    assert len(bursts) == 1
    assert bursts[0].step_range == [50, 200]


def test_adaptive_gap_for_dense_trace_narrows() -> None:
    """30 edits 1-2 steps apart → adaptive gap floors at default (35).

    The naïve median × 4 here would be ~5, which would fragment the
    trace at every 6+ step gap. The DEFAULT floor protects dense
    iteration loops from over-splitting.
    """
    trace_id = "t-dense"
    nodes: list[TraceMapNode] = []
    step = 5
    for i in range(30):
        nodes.append(
            _node(
                ordinal=i + 1,
                trace_id=trace_id,
                action_type="file_edit",
                step_index=step,
                files_modified=["a.py"],
            )
        )
        step += 1 if i % 2 == 0 else 2
    cands = [n for n in nodes if n.action_type == "file_edit"]
    adaptive = _compute_adaptive_gap(cands)
    # Dense trace: median delta is 1-2; clamped at default 35.
    assert adaptive == DEFAULT_BURST_GAP, (
        f"dense trace adaptive gap should equal DEFAULT_BURST_GAP, got {adaptive}"
    )
    bursts = detect_bursts(_trace_map(trace_id, nodes), commit_lookup=False)
    assert len(bursts) == 1


def test_explicit_burst_gap_overrides_adaptive() -> None:
    """Passing ``gap=N`` to ``detect_bursts`` always wins (CLI ``--burst-gap``)."""
    trace_id = "t-override"
    # Construct a trace where adaptive would pick 100 (sparse, gap=80
    # between adjacent edits) but the caller forces gap=10. Forced 10
    # must split the 3 edits into 3 separate bursts.
    nodes = [
        _node(ordinal=1, trace_id=trace_id, action_type="file_edit",
              step_index=10, files_modified=["a.py"]),
        _node(ordinal=2, trace_id=trace_id, action_type="file_edit",
              step_index=90, files_modified=["a.py"]),
        _node(ordinal=3, trace_id=trace_id, action_type="file_edit",
              step_index=170, files_modified=["a.py"]),
    ]
    # Adaptive would coalesce into 1 burst.
    bursts_adaptive = detect_bursts(_trace_map(trace_id, nodes), commit_lookup=False)
    assert len(bursts_adaptive) == 1
    # Explicit gap=10 forces 3 bursts.
    bursts_forced = detect_bursts(
        _trace_map(trace_id, nodes), gap=10, commit_lookup=False
    )
    assert len(bursts_forced) == 3
    # And gap=200 forces 1 burst regardless of sparseness.
    bursts_loose = detect_bursts(
        _trace_map(trace_id, nodes), gap=200, commit_lookup=False
    )
    assert len(bursts_loose) == 1


def test_adaptive_gap_handles_too_few_edits_gracefully() -> None:
    """Zero or one edits cannot produce a delta — fall back to DEFAULT_BURST_GAP."""
    # Zero edits.
    assert _compute_adaptive_gap([]) == DEFAULT_BURST_GAP
    # One edit.
    one = [
        _node(ordinal=1, trace_id="t-1", action_type="file_edit",
              step_index=10, files_modified=["a.py"]),
    ]
    assert _compute_adaptive_gap(one) == DEFAULT_BURST_GAP


def test_adaptive_gap_preserves_entry6_at_default_floor() -> None:
    """Regression guard: a dense trace mirroring entry #6's early phase
    (median delta 1-2, internal range 32-76 with mid-burst pauses up
    to 30 steps) must stay as 1 burst under the adaptive default.

    This is the failure mode an unfloored ``median × 4`` heuristic
    triggers — the median delta 2 would clamp to gap=8, splitting
    the burst at any internal pause longer than 8 steps. The
    DEFAULT_BURST_GAP floor protects the entry #6 burst.
    """
    trace_id = "t-entry6-like"
    # Steps modelled on entry #6's [32, 76] sub-range: 32-65 are
    # 1-2 steps apart, then pauses of 5, 25 steps before 76.
    edit_steps = [
        32, 33, 34, 35, 37, 38, 40, 42, 44, 45, 47, 49, 51, 52, 53,
        54, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 68, 71, 76,
    ]
    nodes = [
        _node(
            ordinal=i + 1,
            trace_id=trace_id,
            action_type="file_edit",
            step_index=s,
            files_modified=["a.py"],
        )
        for i, s in enumerate(edit_steps)
    ]
    bursts = detect_bursts(_trace_map(trace_id, nodes), commit_lookup=False)
    assert len(bursts) == 1, (
        f"Entry-#6-like dense trace fragmented under adaptive gap: "
        f"{[b.step_range for b in bursts]}"
    )
    assert bursts[0].step_range == [32, 76]

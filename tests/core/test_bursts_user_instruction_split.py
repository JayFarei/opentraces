"""Cluster H T9 — hard split on non-trigger user instruction mid-burst.

When ``hard_split_on_user_pivot=True`` is passed to ``detect_bursts``,
a non-trigger ``user_instruction`` node falling strictly between two
adjacent edits forces a burst split — even if the step gap is below
the configured ``gap``. Triggers ("yes", "go ahead", "ship it")
authorise in-flight work and do NOT split.

Default behaviour (T9 off) keeps the prior contract: only the gap
heuristic governs splitting.
"""

from __future__ import annotations

from typing import Any

from opentraces_schema import TraceMap, TraceMapEdge, TraceMapNode

from opentraces.core.bursts import detect_bursts


def _node(
    *,
    ordinal: int,
    trace_id: str,
    action_type: str,
    step_index: int,
    files_modified: list[str] | None = None,
    text_preview: str | None = None,
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
        text_preview=text_preview,
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


def test_non_trigger_user_instruction_splits_burst() -> None:
    """A non-trigger user_instruction strictly between edits splits the burst.

    Edits at steps 5, 10 and 25, 30. A non-trigger user_instruction
    at step 18 (gap 5 → 18 → 25, all within a 35-gap window). Without
    T9: 1 burst. With T9: 2 bursts.
    """
    trace_id = "t-pivot"
    nodes = [
        _node(ordinal=1, trace_id=trace_id, action_type="user_instruction",
              step_index=1, text_preview="please refactor"),
        _node(ordinal=2, trace_id=trace_id, action_type="file_edit",
              step_index=5, files_modified=["a.py"]),
        _node(ordinal=3, trace_id=trace_id, action_type="file_edit",
              step_index=10, files_modified=["a.py"]),
        _node(ordinal=4, trace_id=trace_id, action_type="user_instruction",
              step_index=18,
              text_preview="actually let's also redo the type hints in helpers.py"),
        _node(ordinal=5, trace_id=trace_id, action_type="file_edit",
              step_index=25, files_modified=["helpers.py"]),
        _node(ordinal=6, trace_id=trace_id, action_type="file_edit",
              step_index=30, files_modified=["helpers.py"]),
    ]
    tm = _trace_map(trace_id, nodes)

    # Default: 1 burst (gap 35 >= 25-10=15).
    bursts_default = detect_bursts(tm.nodes, commit_lookup=False)
    assert len(bursts_default) == 1
    assert bursts_default[0].step_range == [5, 30]

    # T9 on: 2 bursts split at the user_instruction step 18.
    bursts_t9 = detect_bursts(
        tm.nodes, commit_lookup=False, hard_split_on_user_pivot=True,
    )
    assert len(bursts_t9) == 2
    assert bursts_t9[0].step_range == [5, 10]
    assert bursts_t9[1].step_range == [25, 30]


def test_trigger_user_instruction_does_not_split() -> None:
    """A trigger user_instruction ("ok") between edits does NOT split,
    even with T9 on."""
    trace_id = "t-trigger"
    nodes = [
        _node(ordinal=1, trace_id=trace_id, action_type="user_instruction",
              step_index=1, text_preview="please refactor"),
        _node(ordinal=2, trace_id=trace_id, action_type="file_edit",
              step_index=5, files_modified=["a.py"]),
        _node(ordinal=3, trace_id=trace_id, action_type="user_instruction",
              step_index=8, text_preview="ok"),
        _node(ordinal=4, trace_id=trace_id, action_type="file_edit",
              step_index=10, files_modified=["a.py"]),
        _node(ordinal=5, trace_id=trace_id, action_type="user_instruction",
              step_index=15, text_preview="go ahead"),
        _node(ordinal=6, trace_id=trace_id, action_type="file_edit",
              step_index=20, files_modified=["a.py"]),
    ]
    tm = _trace_map(trace_id, nodes)

    bursts_t9 = detect_bursts(
        tm.nodes, commit_lookup=False, hard_split_on_user_pivot=True,
    )
    assert len(bursts_t9) == 1
    assert bursts_t9[0].step_range == [5, 20]


def test_hard_split_off_by_default() -> None:
    """Default ``detect_bursts`` does NOT hard-split on user pivots.

    This is the contract that keeps the entry #6 labeled regression
    happy: real-world traces routinely incorporate mid-burst
    redirections into one commit, so default-off is the right choice.
    """
    trace_id = "t-default"
    nodes = [
        _node(ordinal=1, trace_id=trace_id, action_type="user_instruction",
              step_index=1, text_preview="please refactor"),
        _node(ordinal=2, trace_id=trace_id, action_type="file_edit",
              step_index=5, files_modified=["a.py"]),
        _node(ordinal=3, trace_id=trace_id, action_type="user_instruction",
              step_index=10,
              text_preview="actually do this completely different way"),
        _node(ordinal=4, trace_id=trace_id, action_type="file_edit",
              step_index=15, files_modified=["a.py"]),
    ]
    tm = _trace_map(trace_id, nodes)
    bursts_default = detect_bursts(tm.nodes, commit_lookup=False)
    assert len(bursts_default) == 1


def test_hard_split_does_not_fire_when_user_instruction_outside_window() -> None:
    """User instructions outside (last_edit, next_edit) don't trigger T9.

    User instruction at step 100, edits at 5 and 10. The instruction
    is past the burst, not "between" two edits — no split.
    """
    trace_id = "t-outside"
    nodes = [
        _node(ordinal=1, trace_id=trace_id, action_type="file_edit",
              step_index=5, files_modified=["a.py"]),
        _node(ordinal=2, trace_id=trace_id, action_type="file_edit",
              step_index=10, files_modified=["a.py"]),
        _node(ordinal=3, trace_id=trace_id, action_type="user_instruction",
              step_index=100, text_preview="next thing please"),
    ]
    tm = _trace_map(trace_id, nodes)
    bursts = detect_bursts(
        tm.nodes, commit_lookup=False, hard_split_on_user_pivot=True,
    )
    assert len(bursts) == 1
    assert bursts[0].step_range == [5, 10]


def test_hard_split_handles_consecutive_user_instructions() -> None:
    """Multiple non-trigger pivots in a row should split each time."""
    trace_id = "t-multi"
    nodes = [
        _node(ordinal=1, trace_id=trace_id, action_type="file_edit",
              step_index=5, files_modified=["a.py"]),
        _node(ordinal=2, trace_id=trace_id, action_type="user_instruction",
              step_index=10,
              text_preview="actually try a completely different approach"),
        _node(ordinal=3, trace_id=trace_id, action_type="file_edit",
              step_index=15, files_modified=["b.py"]),
        _node(ordinal=4, trace_id=trace_id, action_type="user_instruction",
              step_index=18,
              text_preview="hmm, redo it again with a different layout"),
        _node(ordinal=5, trace_id=trace_id, action_type="file_edit",
              step_index=25, files_modified=["c.py"]),
    ]
    tm = _trace_map(trace_id, nodes)
    bursts = detect_bursts(
        tm.nodes, commit_lookup=False, hard_split_on_user_pivot=True,
    )
    assert len(bursts) == 3
    assert bursts[0].step_range == [5, 5]
    assert bursts[1].step_range == [15, 15]
    assert bursts[2].step_range == [25, 25]

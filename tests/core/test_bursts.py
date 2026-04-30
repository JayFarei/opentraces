"""Burst detection over Trace Maps (Cluster B / Plan 54 trace-map projections).

Bursts cluster `file_edit` and `patch_created` nodes by step-index proximity,
and emit one virtual `change_burst` node per cluster carrying intent and
patch metadata. The tests use small synthetic ``TraceMap`` inputs so the
algorithm can be exercised independently of the full ingest pipeline.
"""

from __future__ import annotations

from typing import Any

from opentraces_schema import TraceMap, TraceMapEdge, TraceMapNode


def _node(
    *,
    ordinal: int,
    trace_id: str,
    action_type: str,
    step_index: int,
    files_modified: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    text_preview: str | None = None,
    active_user_step: int | None = None,
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
        metadata=metadata or {},
        active_user_step=active_user_step,
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


def test_burst_detection_empty_trace_returns_no_bursts():
    from opentraces.core.bursts import detect_bursts

    trace_map = _trace_map("t-empty", [])
    bursts = detect_bursts(trace_map)
    assert bursts == []


def test_burst_detection_groups_adjacent_file_edits():
    """File edits at steps [10, 12, 15] and [80, 82] should yield 2 bursts (gap=35)."""
    from opentraces.core.bursts import detect_bursts

    trace_id = "t-groups"
    nodes = [
        _node(ordinal=1, trace_id=trace_id, action_type="user_instruction", step_index=1, text_preview="please fix bug"),
        _node(ordinal=2, trace_id=trace_id, action_type="file_edit", step_index=10, files_modified=["a.py"], active_user_step=1),
        _node(ordinal=3, trace_id=trace_id, action_type="file_edit", step_index=12, files_modified=["a.py"], active_user_step=1),
        _node(ordinal=4, trace_id=trace_id, action_type="file_edit", step_index=15, files_modified=["b.py"], active_user_step=1),
        _node(ordinal=5, trace_id=trace_id, action_type="user_instruction", step_index=70, text_preview="now refactor"),
        _node(ordinal=6, trace_id=trace_id, action_type="file_edit", step_index=80, files_modified=["c.py"], active_user_step=70),
        _node(ordinal=7, trace_id=trace_id, action_type="file_edit", step_index=82, files_modified=["c.py"], active_user_step=70),
    ]
    trace_map = _trace_map(trace_id, nodes)
    bursts = detect_bursts(trace_map, gap=35)
    assert len(bursts) == 2
    assert bursts[0].step_range == [10, 15]
    assert bursts[1].step_range == [80, 82]
    # Ordering preserved.
    assert bursts[0].step_range[1] < bursts[1].step_range[0]


def test_burst_gap_knob_changes_count():
    """Gap=5 should split [10,12,15] [80,82] into 5 bursts. Gap=200 collapses to 1."""
    from opentraces.core.bursts import detect_bursts

    trace_id = "t-gap"
    # Steps: 10, 12, 15, 80, 82
    nodes = [
        _node(ordinal=1, trace_id=trace_id, action_type="user_instruction", step_index=1, text_preview="please fix bug"),
        _node(ordinal=2, trace_id=trace_id, action_type="file_edit", step_index=10, files_modified=["a.py"], active_user_step=1),
        _node(ordinal=3, trace_id=trace_id, action_type="file_edit", step_index=12, files_modified=["a.py"], active_user_step=1),
        _node(ordinal=4, trace_id=trace_id, action_type="file_edit", step_index=15, files_modified=["b.py"], active_user_step=1),
        _node(ordinal=5, trace_id=trace_id, action_type="file_edit", step_index=80, files_modified=["c.py"], active_user_step=1),
        _node(ordinal=6, trace_id=trace_id, action_type="file_edit", step_index=82, files_modified=["c.py"], active_user_step=1),
    ]
    trace_map = _trace_map(trace_id, nodes)

    # gap=5: 10,12 within 5? gap = next - prev. 12-10=2 OK. 15-12=3 OK. 80-15=65 split. 82-80=2 OK.
    # That's 2 bursts (10-15) and (80-82). To get 5 bursts, use gap=1 (each isolated).
    # Re-spec: gap=1: 10 vs 12 = 2 > 1 → split. 12 vs 15 = 3 > 1 → split. So [10],[12],[15],[80],[82] = 5 bursts.
    bursts_tight = detect_bursts(trace_map, gap=1)
    assert len(bursts_tight) == 5

    bursts_loose = detect_bursts(trace_map, gap=200)
    assert len(bursts_loose) == 1


def test_burst_carries_intent_from_active_user_step():
    """A burst should report intent_user_step matching the user_instruction preceding the burst."""
    from opentraces.core.bursts import detect_bursts

    trace_id = "t-intent"
    nodes = [
        _node(ordinal=1, trace_id=trace_id, action_type="user_instruction", step_index=5, text_preview="please add feature X to the codebase"),
        _node(ordinal=2, trace_id=trace_id, action_type="file_edit", step_index=10, files_modified=["x.py"], active_user_step=5),
        _node(ordinal=3, trace_id=trace_id, action_type="file_edit", step_index=12, files_modified=["x.py"], active_user_step=5),
    ]
    trace_map = _trace_map(trace_id, nodes)
    bursts = detect_bursts(trace_map, gap=35)
    assert len(bursts) == 1
    burst = bursts[0]
    assert burst.intent_user_step == 5
    assert burst.intent_text and "please add feature X" in burst.intent_text


def test_burst_aggregates_patch_metadata():
    """3 patch_created nodes in one burst, each with git_anchor_id and commit_sha."""
    from opentraces.core.bursts import detect_bursts

    trace_id = "t-patches"
    nodes = [
        _node(ordinal=1, trace_id=trace_id, action_type="user_instruction", step_index=1, text_preview="make changes"),
        _node(
            ordinal=2,
            trace_id=trace_id,
            action_type="file_edit",
            step_index=10,
            files_modified=["a.py"],
            active_user_step=1,
        ),
        _node(
            ordinal=3,
            trace_id=trace_id,
            action_type="patch_created",
            step_index=11,
            files_modified=["a.py"],
            metadata={
                "trace_patch_id": "patch-1",
                "git_anchor_id": "anchor-1",
                "commit_sha": "abc111",
                "evidence_firmness": "firm",
                "evidence_tier": "tool_emitted",
            },
            active_user_step=1,
        ),
        _node(
            ordinal=4,
            trace_id=trace_id,
            action_type="patch_created",
            step_index=12,
            files_modified=["b.py"],
            metadata={
                "trace_patch_id": "patch-2",
                "git_anchor_id": "anchor-2",
                "commit_sha": "abc222",
                "evidence_firmness": "firm",
                "evidence_tier": "tool_emitted",
            },
            active_user_step=1,
        ),
        _node(
            ordinal=5,
            trace_id=trace_id,
            action_type="patch_created",
            step_index=13,
            files_modified=["c.py"],
            metadata={
                "trace_patch_id": "patch-3",
                "git_anchor_id": "anchor-3",
                "commit_sha": "abc333",
                "evidence_firmness": "provisional",
                "evidence_tier": "overlapping",
            },
            active_user_step=1,
        ),
    ]
    trace_map = _trace_map(trace_id, nodes)
    bursts = detect_bursts(trace_map, gap=35)
    assert len(bursts) == 1
    burst = bursts[0]
    assert len(burst.patches) == 3
    assert {p["patch_id"] for p in burst.patches} == {"patch-1", "patch-2", "patch-3"}
    assert sorted(burst.unique_git_anchors) == ["anchor-1", "anchor-2", "anchor-3"]
    assert burst.has_git_anchor is True
    # Each patch carries firmness/tier so reviewers can filter.
    firmness_per_patch = sorted(p["evidence_firmness"] for p in burst.patches)
    assert firmness_per_patch == ["firm", "firm", "provisional"]


def test_burst_unique_files_aggregates_edit_counts():
    from opentraces.core.bursts import detect_bursts

    trace_id = "t-unique-files"
    nodes = [
        _node(ordinal=1, trace_id=trace_id, action_type="user_instruction", step_index=1, text_preview="edit"),
        _node(ordinal=2, trace_id=trace_id, action_type="file_edit", step_index=10, files_modified=["a.py"], active_user_step=1),
        _node(ordinal=3, trace_id=trace_id, action_type="file_edit", step_index=11, files_modified=["a.py"], active_user_step=1),
        _node(ordinal=4, trace_id=trace_id, action_type="file_edit", step_index=12, files_modified=["b.py"], active_user_step=1),
    ]
    trace_map = _trace_map(trace_id, nodes)
    bursts = detect_bursts(trace_map, gap=35)
    assert len(bursts) == 1
    assert bursts[0].unique_files == {"a.py": 2, "b.py": 1}


def test_bursts_to_trace_map_emits_change_burst_nodes():
    """The burst projection wraps bursts as TraceMapNodes with action_type=change_burst."""
    from opentraces.core.bursts import bursts_to_trace_map, detect_bursts

    trace_id = "t-emit"
    nodes = [
        _node(ordinal=1, trace_id=trace_id, action_type="user_instruction", step_index=1, text_preview="go"),
        _node(ordinal=2, trace_id=trace_id, action_type="file_edit", step_index=10, files_modified=["a.py"], active_user_step=1),
        _node(ordinal=3, trace_id=trace_id, action_type="file_edit", step_index=12, files_modified=["a.py"], active_user_step=1),
        _node(ordinal=4, trace_id=trace_id, action_type="file_edit", step_index=80, files_modified=["b.py"], active_user_step=1),
    ]
    trace_map = _trace_map(trace_id, nodes)
    bursts = detect_bursts(trace_map, gap=35)
    assert len(bursts) == 2
    burst_map = bursts_to_trace_map(trace_map, bursts)
    assert all(node.action_type == "change_burst" for node in burst_map.nodes)
    # Ordering edges between consecutive bursts as previous_next.
    assert all(edge.edge_type == "previous_next" for edge in burst_map.edges)
    assert len(burst_map.edges) == max(0, len(burst_map.nodes) - 1)


# ─── Cluster E extensions ─────────────────────────────────────────────────


def test_burst_intent_object_present():
    """Every burst must carry the structured ``intent`` dict (Cluster E I7)."""
    from opentraces.core.bursts import detect_bursts

    trace_id = "t-intent-shape"
    nodes = [
        _node(ordinal=1, trace_id=trace_id, action_type="user_instruction", step_index=1, text_preview="please refactor the cache"),
        _node(ordinal=2, trace_id=trace_id, action_type="user_instruction", step_index=5, text_preview="ok"),
        _node(ordinal=3, trace_id=trace_id, action_type="file_edit", step_index=10, files_modified=["a.py"], active_user_step=5),
        _node(ordinal=4, trace_id=trace_id, action_type="file_edit", step_index=12, files_modified=["a.py"], active_user_step=5),
    ]
    trace_map = _trace_map(trace_id, nodes)
    bursts = detect_bursts(trace_map, gap=35)
    assert len(bursts) == 1
    burst = bursts[0]
    intent = burst.intent
    assert isinstance(intent, dict)
    # Required structural keys.
    for key in (
        "trigger",
        "most_substantive_spec",
        "spec_chain",
        "burst_commit_sha",
        "commit_subject",
        "commit_body",
    ):
        assert key in intent, f"intent missing {key!r}"
    # Trigger and spec come from the user_instructions before the burst.
    assert intent["trigger"] is not None
    assert intent["trigger"]["step"] == 5
    assert intent["most_substantive_spec"]["step"] == 1
    assert [item["step"] for item in intent["spec_chain"]] == [1]


def test_burst_unique_files_dedup_normalises_abs_and_rel():
    """abs and repo-relative paths to the same file collapse onto one entry (D6)."""
    from opentraces.core.bursts import FOREIGN_AGENT_PATH_PREFIXES, detect_bursts

    trace_id = "t-dedup"
    foreign_root = FOREIGN_AGENT_PATH_PREFIXES[0]
    nodes = [
        _node(
            ordinal=1,
            trace_id=trace_id,
            action_type="file_edit",
            step_index=10,
            files_modified=[f"{foreign_root}src/foo.py"],
            active_user_step=None,
        ),
        _node(
            ordinal=2,
            trace_id=trace_id,
            action_type="file_edit",
            step_index=11,
            files_modified=["src/foo.py"],
            active_user_step=None,
        ),
        _node(
            ordinal=3,
            trace_id=trace_id,
            action_type="file_edit",
            step_index=12,
            files_modified=[f"{foreign_root}src/bar.py"],
            active_user_step=None,
        ),
    ]
    trace_map = _trace_map(trace_id, nodes)
    bursts = detect_bursts(trace_map, gap=35, commit_lookup=False)
    assert len(bursts) == 1
    files = bursts[0].unique_files
    # Two unique files (src/foo.py and src/bar.py), not three.
    assert len(files) == 2
    assert "src/foo.py" in files
    assert "src/bar.py" in files


def test_burst_unique_files_dedup_preserves_hunk_counts():
    """Dedup must SUM the per-file edit counts, not silently drop them."""
    from opentraces.core.bursts import FOREIGN_AGENT_PATH_PREFIXES, detect_bursts

    trace_id = "t-dedup-counts"
    foreign_root = FOREIGN_AGENT_PATH_PREFIXES[0]
    nodes = [
        _node(
            ordinal=i + 1,
            trace_id=trace_id,
            action_type="file_edit",
            step_index=10 + i,
            files_modified=[
                f"{foreign_root}src/foo.py" if i % 2 == 0 else "src/foo.py"
            ],
        )
        for i in range(5)
    ]
    trace_map = _trace_map(trace_id, nodes)
    bursts = detect_bursts(trace_map, gap=35, commit_lookup=False)
    files = bursts[0].unique_files
    # 5 edits to src/foo.py (3 abs + 2 rel) collapse onto one key with count 5.
    assert files == {"src/foo.py": 5}


def test_burst_commit_sha_modal_across_patches():
    """``burst_commit_sha`` is the modal commit_sha across patches (D5)."""
    from opentraces.core.bursts import detect_bursts

    trace_id = "t-modal"
    nodes = [
        _node(
            ordinal=1,
            trace_id=trace_id,
            action_type="patch_created",
            step_index=10,
            files_modified=["a.py"],
            metadata={
                "trace_patch_id": "p1",
                "git_anchor_id": "anchor-1",
                "commit_sha": "alpha111",
                "evidence_firmness": "firm",
                "evidence_tier": "tool_emitted",
            },
        ),
        _node(
            ordinal=2,
            trace_id=trace_id,
            action_type="patch_created",
            step_index=11,
            files_modified=["b.py"],
            metadata={
                "trace_patch_id": "p2",
                "git_anchor_id": "anchor-2",
                "commit_sha": "alpha111",
                "evidence_firmness": "firm",
                "evidence_tier": "tool_emitted",
            },
        ),
        _node(
            ordinal=3,
            trace_id=trace_id,
            action_type="patch_created",
            step_index=12,
            files_modified=["c.py"],
            metadata={
                "trace_patch_id": "p3",
                "git_anchor_id": "anchor-3",
                "commit_sha": "beta222",
                "evidence_firmness": "firm",
                "evidence_tier": "tool_emitted",
            },
        ),
    ]
    trace_map = _trace_map(trace_id, nodes)
    bursts = detect_bursts(trace_map, gap=35, commit_lookup=False)
    assert len(bursts) == 1
    burst = bursts[0]
    # Modal commit is alpha111 (2 patches); beta222 has only 1.
    assert burst.burst_commit_sha == "alpha111"


def test_burst_commit_lookup_handles_missing_repo_gracefully():
    """If the configured repo path doesn't exist, lookup absorbs the failure."""
    from pathlib import Path

    from opentraces.core.bursts import detect_bursts

    trace_id = "t-no-repo"
    nodes = [
        _node(
            ordinal=1,
            trace_id=trace_id,
            action_type="patch_created",
            step_index=10,
            files_modified=["a.py"],
            metadata={
                "trace_patch_id": "p1",
                "commit_sha": "deadbeef0000000000000000000000000000",
                "evidence_firmness": "firm",
                "evidence_tier": "tool_emitted",
            },
        ),
    ]
    trace_map = _trace_map(trace_id, nodes)
    # Non-existent path; commit lookup must defensively absorb the
    # failure rather than raising.
    bursts = detect_bursts(
        trace_map,
        gap=35,
        repo_path=Path("/nonexistent/path/that/does/not/exist"),
        commit_lookup=True,
    )
    assert len(bursts) == 1
    burst = bursts[0]
    assert burst.burst_commit_sha == "deadbeef0000000000000000000000000000"
    # Lookup either skipped (repo path didn't exist so we never ran
    # git) or recorded an error — never raised.
    assert burst.intent.get("commit_subject") in (None,)
    assert burst.intent.get("commit_body") in (None,)


def test_burst_intent_text_legacy_alias_remains():
    """Old consumers reading ``intent_text`` / ``intent_user_step`` keep working."""
    from opentraces.core.bursts import detect_bursts

    trace_id = "t-legacy"
    nodes = [
        _node(ordinal=1, trace_id=trace_id, action_type="user_instruction", step_index=1, text_preview="please add foo to bar"),
        _node(ordinal=2, trace_id=trace_id, action_type="user_instruction", step_index=5, text_preview="ok"),
        _node(ordinal=3, trace_id=trace_id, action_type="file_edit", step_index=10, files_modified=["a.py"], active_user_step=5),
    ]
    trace_map = _trace_map(trace_id, nodes)
    bursts = detect_bursts(trace_map, gap=35, commit_lookup=False)
    burst = bursts[0]
    # Legacy single-string intent points at the spec, not the trigger.
    assert burst.intent_user_step == 1
    assert burst.intent_text and "please add foo to bar" in burst.intent_text
    # Structured intent agrees on the spec.
    assert burst.intent["most_substantive_spec"]["step"] == 1

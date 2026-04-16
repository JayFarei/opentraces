"""Tests for the shared trace tree adapter (plan 048 phase A)."""

from __future__ import annotations

from opentraces_schema import TraceRecord


def _record(steps: list[dict]) -> TraceRecord:
    return TraceRecord.model_validate({
        "schema_version": "0.3.0",
        "trace_id": "trace-001",
        "session_id": "sess-001",
        "agent": {"name": "claude-code", "model": "anthropic/claude-opus-4-6"},
        "task": {"description": "tree test"},
        "steps": steps,
        "metrics": {},
    })


def test_build_tree_linear_trace_includes_tool_and_observation_nodes():
    from opentraces.clients.text.trace_tree import build_tree, flatten_tree

    record = _record([
        {"step_index": 1, "role": "user", "content": "start", "timestamp": "2026-04-16T10:00:00Z"},
        {
            "step_index": 2,
            "role": "agent",
            "content": "reading",
            "timestamp": "2026-04-16T10:00:01Z",
            "tool_calls": [{"tool_call_id": "tc-1", "tool_name": "Read", "input": {"file_path": "app.py"}}],
            "observations": [{"source_call_id": "tc-1", "content": "def main(): pass"}],
        },
        {"step_index": 3, "role": "user", "content": "continue", "timestamp": "2026-04-16T10:00:02Z"},
    ])

    tree = build_tree(record)
    flat = flatten_tree(tree, "s3", folded=set(), filter_mode="default", search="")

    assert [node.id for node in tree] == ["s1"]
    assert [item.node.id for item in flat] == ["s1", "s2", "s3", "s2-tc0", "s2-ob0"]
    assert flat[3].node.kind == "tool_call"
    assert flat[4].node.kind == "observation"
    assert [item.node.id for item in flat if item.on_active_path] == ["s1", "s2", "s3"]


def test_flatten_tree_promotes_active_branch_before_older_sibling():
    from opentraces.clients.text.trace_tree import build_tree, flatten_tree

    record = _record([
        {"step_index": 1, "role": "user", "content": "root", "timestamp": "2026-04-16T10:00:00Z"},
        {"step_index": 2, "role": "agent", "content": "older sibling", "parent_step": 1, "timestamp": "2026-04-16T10:00:01Z"},
        {"step_index": 3, "role": "agent", "content": "active sibling", "parent_step": 1, "timestamp": "2026-04-16T10:00:02Z"},
        {"step_index": 4, "role": "user", "content": "leaf", "parent_step": 3, "timestamp": "2026-04-16T10:00:03Z"},
    ])

    tree = build_tree(record)
    flat = flatten_tree(tree, "s4", folded=set(), filter_mode="default", search="")

    assert [item.node.id for item in flat] == ["s1", "s3", "s4", "s2"]


def test_flatten_tree_filters_and_fold_toggles_are_stable():
    from opentraces.clients.text.trace_tree import build_tree, flatten_tree

    record = _record([
        {"step_index": 1, "role": "user", "content": "start", "timestamp": "2026-04-16T10:00:00Z"},
        {
            "step_index": 2,
            "role": "agent",
            "content": "delegate alpha",
            "timestamp": "2026-04-16T10:00:01Z",
            "subagent_trajectory_ref": "subtrace-alpha",
        },
        {
            "step_index": 3,
            "role": "agent",
            "content": "tool work",
            "timestamp": "2026-04-16T10:00:02Z",
            "tool_calls": [{"tool_call_id": "tc-1", "tool_name": "Bash", "input": {"command": "pytest"}}],
            "observations": [{"source_call_id": "tc-1", "content": "12 passed"}],
        },
        {
            "step_index": 4,
            "role": "agent",
            "content": "delegate beta",
            "parent_step": 2,
            "timestamp": "2026-04-16T10:00:03Z",
            "subagent_trajectory_ref": "subtrace-beta",
        },
        {"step_index": 5, "role": "user", "content": "done", "timestamp": "2026-04-16T10:00:04Z"},
    ])

    tree = build_tree(record, step_labels={"s4": "decision"})

    default_ids = [item.node.id for item in flatten_tree(tree, "s5", folded=set(), filter_mode="default", search="")]
    no_tools_ids = [item.node.id for item in flatten_tree(tree, "s5", folded=set(), filter_mode="no-tools", search="")]
    labeled_ids = [item.node.id for item in flatten_tree(tree, "s5", folded=set(), filter_mode="labeled-only", search="")]
    folded_ids = [item.node.id for item in flatten_tree(tree, "s5", folded={"s2"}, filter_mode="default", search="")]

    assert "s2-subagent" in default_ids
    assert "s4-subagent" in default_ids
    assert "s3-tc0" not in no_tools_ids
    assert "s3-ob0" not in no_tools_ids
    assert labeled_ids == ["s4"]
    assert folded_ids == ["s1", "s2"]

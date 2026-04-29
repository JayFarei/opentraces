"""Tests for the `filter_trace_map_actions` compactness projection."""

from __future__ import annotations

from opentraces_schema import Agent, Observation, Step, ToolCall, TraceRecord


def _heavy_trace() -> TraceRecord:
    """Build a trace that mixes user/agent text, edits, tool calls, and observations.

    Without filtering, the JSON dump should be dominated by tool_call/tool_result
    bodies (long observation content). The lineage filter trims those out.
    """
    big_blob = "X" * 200
    steps = [
        Step(step_index=1, role="user", content="please fix the bug and prove tests pass"),
        Step(
            step_index=2,
            role="agent",
            content="Plan:\n- read files\n- patch\n- test",
            tool_calls=[
                ToolCall(tool_call_id="tc-r1", tool_name="Read", input={"file_path": "a.py"}),
                ToolCall(tool_call_id="tc-r2", tool_name="Read", input={"file_path": "b.py"}),
                ToolCall(tool_call_id="tc-bash1", tool_name="Bash", input={"command": "ls -la"}),
            ],
            observations=[
                Observation(source_call_id="tc-r1", content=big_blob, output_summary=big_blob),
                Observation(source_call_id="tc-r2", content=big_blob, output_summary=big_blob),
                Observation(source_call_id="tc-bash1", content=big_blob, output_summary=big_blob),
            ],
        ),
        Step(
            step_index=3,
            role="agent",
            tool_calls=[
                ToolCall(
                    tool_call_id="tc-edit",
                    tool_name="Edit",
                    input={"file_path": "src/parser.py", "old_string": "x", "new_string": "y"},
                )
            ],
            observations=[Observation(source_call_id="tc-edit", content=big_blob)],
        ),
        Step(
            step_index=4,
            role="agent",
            tool_calls=[
                ToolCall(tool_call_id="tc-test", tool_name="Bash", input={"command": "pytest tests/"}),
            ],
            observations=[
                Observation(source_call_id="tc-test", content=big_blob, output_summary="2 passed"),
            ],
        ),
        Step(step_index=5, role="agent", content="Done. tests pass."),
    ]
    return TraceRecord(
        trace_id="trace-actions-filter",
        session_id="session-actions-filter",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "Bug fix"},
        steps=steps,
    )


def test_actions_filter_compactness():
    """`--actions user_instruction,file_edit,patch_created` should slash size by >=50%.

    The lineage filter is the canonical compact projection: it keeps the user
    intent, the structural edits/patches, and skips tool_call/tool_result
    bodies that often hold long observation blobs.
    """
    from opentraces.core.trace_map import build_trace_map, filter_trace_map_actions

    trace_map = build_trace_map(_heavy_trace())
    full_size = len(trace_map.model_dump_json())

    filtered = filter_trace_map_actions(
        trace_map,
        {"user_instruction", "file_edit", "patch_created"},
    )
    filtered_size = len(filtered.model_dump_json())

    assert filtered_size <= full_size // 2, (
        f"filter should compact at least 50%; got full={full_size} filtered={filtered_size}"
    )
    # All retained nodes must be in the filter set.
    assert all(
        node.action_type in {"user_instruction", "file_edit", "patch_created"}
        for node in filtered.nodes
    )
    # The structural skeleton (root + edges among kept nodes) is preserved.
    assert filtered.root_node_ids
    kept_ids = {node.node_id for node in filtered.nodes}
    for edge in filtered.edges:
        assert edge.source_node_id in kept_ids
        assert edge.target_node_id in kept_ids


def test_actions_filter_default_unchanged_when_none():
    """When no filter is provided, output is unchanged byte-identical."""
    from opentraces.core.trace_map import build_trace_map, filter_trace_map_actions

    trace_map = build_trace_map(_heavy_trace())
    unchanged = filter_trace_map_actions(trace_map, None)
    assert unchanged.model_dump_json() == trace_map.model_dump_json()


def test_actions_filter_keeps_lineage_action_set():
    """Document the canonical lineage action set works end-to-end."""
    from opentraces.core.trace_map import build_trace_map, filter_trace_map_actions

    trace_map = build_trace_map(_heavy_trace())
    lineage = {
        "user_instruction",
        "file_edit",
        "patch_created",
        "git_anchor",
        "test_run",
        "error_signal",
        "final_response",
    }
    filtered = filter_trace_map_actions(trace_map, lineage)
    # The fixture has 1 user_instruction, 1 file_edit, 2 test_run, 1 final_response.
    types = sorted(node.action_type for node in filtered.nodes)
    assert "user_instruction" in types
    assert "file_edit" in types
    assert "test_run" in types
    assert "final_response" in types
    # tool_call / tool_result bodies are dropped.
    assert "tool_call" not in types
    assert "tool_result" not in types

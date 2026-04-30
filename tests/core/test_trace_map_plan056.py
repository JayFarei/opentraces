"""Plan 56 Trace Map behavior tests."""

from opentraces_schema import Agent, Observation, Step, ToolCall, TraceRecord


# Re-exported so newer tests can extend without duplicating the fixture.
__all__ = ["_bug_fix_trace"]


def _bug_fix_trace() -> TraceRecord:
    return TraceRecord(
        trace_id="trace-plan056-map",
        session_id="session-plan056-map",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "Fix the parser regression and prove tests pass"},
        steps=[
            Step(
                step_index=1,
                role="user",
                content="Fix the parser regression and prove tests pass.",
            ),
            Step(
                step_index=2,
                role="agent",
                content="Plan:\n- reproduce the failing parser test\n- patch the parser\n- rerun pytest",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-fail",
                        tool_name="Bash",
                        input={"command": "pytest tests/test_parser.py"},
                    )
                ],
                observations=[
                    Observation(
                        source_call_id="tc-fail",
                        content="FAILED tests/test_parser.py::test_parser_regression",
                        output_summary="1 failed",
                    )
                ],
            ),
            Step(
                step_index=3,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-edit",
                        tool_name="Edit",
                        input={
                            "file_path": "src/parser.py",
                            "old_string": "return parse_bad(token)",
                            "new_string": "return parse_good(token)",
                        },
                    )
                ],
                observations=[Observation(source_call_id="tc-edit", content="ok")],
            ),
            Step(
                step_index=4,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-pass",
                        tool_name="Bash",
                        input={"command": "pytest tests/test_parser.py"},
                    )
                ],
                observations=[
                    Observation(
                        source_call_id="tc-pass",
                        content="tests/test_parser.py . 1 passed",
                        output_summary="1 passed",
                    )
                ],
            ),
            Step(
                step_index=5,
                role="agent",
                content="Implemented the parser fix and verified pytest passes.",
            ),
        ],
    )


def test_build_trace_map_classifies_broad_action_nodes_without_semantic_labels():
    from opentraces.core.trace_map import build_trace_map

    trace_map = build_trace_map(_bug_fix_trace())

    action_types = [node.action_type for node in trace_map.nodes]
    assert action_types == [
        "user_instruction",
        "agent_plan",
        "test_run",
        "tool_result",
        "file_edit",
        "tool_result",
        "test_run",
        "tool_result",
        "final_response",
    ]
    assert trace_map.root_node_ids == ["tmn:trace-plan056-map:1"]
    assert all(edge.edge_type == "previous_next" for edge in trace_map.edges)

    edit_node = next(node for node in trace_map.nodes if node.action_type == "file_edit")
    assert edit_node.files_modified == ["src/parser.py"]
    assert edit_node.unit_id == "tu:trace-plan056-map:tool:tc-edit"

    serialized = trace_map.model_dump(mode="json")
    assert "intent" not in serialized
    assert "trajectory" not in serialized
    assert "outcome" not in serialized


def test_candidate_slice_walks_from_instruction_to_nearby_verification_nodes():
    from opentraces.core.trace_map import build_trace_map, slice_trace_map_for_candidate

    trace_map = build_trace_map(_bug_fix_trace())
    edit_node = next(node for node in trace_map.nodes if node.action_type == "file_edit")

    sliced = slice_trace_map_for_candidate(trace_map, edit_node.node_id, max_steps=6)

    assert [node.action_type for node in sliced.nodes] == [
        "user_instruction",
        "agent_plan",
        "test_run",
        "file_edit",
        "test_run",
        "final_response",
    ]
    assert len(sliced.nodes) <= 6
    assert all(node.action_type != "tool_result" for node in sliced.nodes)


def test_build_trace_map_marks_sorted_last_agent_text_as_final_response():
    from opentraces.core.trace_map import build_trace_map

    record = _bug_fix_trace()
    record.steps = [
        record.steps[0],
        record.steps[-1],
        *record.steps[1:-1],
    ]

    trace_map = build_trace_map(record)

    final_nodes = [node for node in trace_map.nodes if node.action_type == "final_response"]
    assert len(final_nodes) == 1
    assert final_nodes[0].step_index == 5
    assert final_nodes[0].text_preview == "Implemented the parser fix and verified pytest passes."


def _abs_paths_trace() -> TraceRecord:
    """A trace whose tool calls mix absolute and repo-relative paths.

    The repo root is communicated via ``metadata.cwd`` so the trace map
    builder can normalize ``/Users/.../foo.py`` and ``foo.py`` into the
    same logical entry.
    """
    return TraceRecord(
        trace_id="trace-paths",
        session_id="session-paths",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "edit foo and bar"},
        steps=[
            Step(step_index=1, role="user", content="edit foo.py and bar.py"),
            Step(
                step_index=2,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-abs",
                        tool_name="Edit",
                        input={
                            "file_path": "/Users/dev/repo/foo.py",
                            "old_string": "a",
                            "new_string": "b",
                        },
                    ),
                ],
            ),
            Step(
                step_index=3,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-rel",
                        tool_name="Edit",
                        input={
                            "file_path": "foo.py",
                            "old_string": "c",
                            "new_string": "d",
                        },
                    ),
                ],
            ),
            Step(
                step_index=4,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-read",
                        tool_name="Read",
                        input={"file_path": "/Users/dev/repo/bar.py"},
                    ),
                ],
            ),
        ],
        metadata={"cwd": "/Users/dev/repo"},
    )


def test_active_user_step_populated_on_action_nodes():
    """Every action node should carry active_user_step pointing back to the
    most recent preceding user_instruction. Nodes before any user_instruction
    leave the field unset.
    """
    from opentraces.core.trace_map import build_trace_map

    trace_map = build_trace_map(_bug_fix_trace())

    user_steps = sorted({step.step_index for step in _bug_fix_trace().steps if step.role == "user"})
    for node in trace_map.nodes:
        if node.action_type in {"file_edit", "patch_created", "git_anchor", "test_run"}:
            assert node.active_user_step is not None, f"node {node.node_id} missing active_user_step"
            assert node.active_user_step in user_steps


def test_path_normalization_dedupes_abs_and_relative():
    """Paths in files_modified must be repo-relative; absolute siblings live
    in metadata for downstream needs. The same logical file must not appear
    twice in different forms.
    """
    from opentraces.core.trace_map import build_trace_map

    trace_map = build_trace_map(_abs_paths_trace())

    edits = [n for n in trace_map.nodes if n.action_type == "file_edit"]
    assert edits, "expected at least one file_edit node"
    # Combine all files_modified across edits — there should be no duplicates
    # like both "foo.py" and "/Users/dev/repo/foo.py" present.
    all_modified: list[str] = []
    for node in edits:
        all_modified.extend(node.files_modified)
    assert "/Users/dev/repo/foo.py" not in all_modified
    assert "foo.py" in all_modified

    # Per-node: the absolute sibling lives in metadata.
    for node in edits:
        if "/Users/dev/repo/foo.py" in node.metadata.get("files_modified_absolute", []):
            assert node.files_modified == ["foo.py"], node.files_modified

    reads = [n for n in trace_map.nodes if n.action_type == "file_read"]
    assert reads, "expected at least one file_read node"
    for node in reads:
        assert "/Users/dev/repo/bar.py" not in node.files_read
        assert "bar.py" in node.files_read

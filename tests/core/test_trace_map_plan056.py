"""Plan 56 Trace Map behavior tests."""

from opentraces_schema import Agent, Observation, Step, ToolCall, TraceRecord


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

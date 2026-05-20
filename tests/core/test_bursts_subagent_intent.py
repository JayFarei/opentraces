"""Sub-agent burst intent re-attribution.

A sub-agent's driving instruction is the ``Task``/``Agent`` tool prompt the
parent passed, not a ``user_instruction``. Inlined sub-agent steps are also
renumbered to the trace tail, so the naive "latest user instruction before
the burst" heuristic grabs the parent session's *last* user turn — which says
nothing about what the sub-agent was dispatched to do.

These tests prove the burst pass re-attributes a sub-agent burst's intent to
the dispatching Task prompt while a sibling main-agent burst keeps using the
user-instruction chain.
"""

from __future__ import annotations

from opentraces_schema import Agent, Step, ToolCall, TraceRecord

from opentraces.core.bursts import detect_bursts
from opentraces.core.trace_map import build_trace_map

_AGENT = Agent(name="claude-code", model="claude-opus-4-7")


SUBAGENT_PROMPT = (
    "Migrate session token storage to the encrypted backend and add a "
    "regression test covering the rotation path."
)


def _record_with_subagent_and_main_edits() -> TraceRecord:
    """A parent session that dispatches one sub-agent and also edits directly.

    Step layout mirrors the real parser: the sub-agent's steps are inlined and
    renumbered to the tail (steps 40-41), each tagged ``call_type="subagent"``
    with ``parent_step`` pointing back at the dispatch step (2).
    """
    return TraceRecord(
        trace_id="trace-subagent-intent",
        session_id="session-subagent-intent",
        agent=_AGENT,
        task={"description": "auth work"},
        steps=[
            Step(
                step_index=1,
                role="user",
                content="Refactor the auth module and update its tests.",
            ),
            Step(
                step_index=2,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-dispatch",
                        tool_name="Task",
                        input={
                            "subagent_type": "general-purpose",
                            "description": "Migrate token storage",
                            "prompt": SUBAGENT_PROMPT,
                        },
                    )
                ],
                subagent_trajectory_ref="sub-session-1",
            ),
            # Main-agent edit near the dispatch — its own burst.
            Step(
                step_index=3,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-main-edit",
                        tool_name="Edit",
                        input={
                            "file_path": "src/auth/module.py",
                            "old_string": "old",
                            "new_string": "new",
                        },
                    )
                ],
            ),
            # Parent's LAST user turn — a pure trigger, unrelated to the
            # sub-agent's task. This is what the naive heuristic would
            # mis-attribute the sub-agent burst to.
            Step(step_index=4, role="user", content="ok ship it"),
            # Inlined sub-agent edits, renumbered to the tail.
            Step(
                step_index=40,
                role="agent",
                call_type="subagent",
                agent_role="general",
                parent_step=2,
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-sub-1",
                        tool_name="Edit",
                        input={
                            "file_path": "src/auth/token_store.py",
                            "old_string": "plaintext",
                            "new_string": "encrypted",
                        },
                    )
                ],
            ),
            Step(
                step_index=41,
                role="agent",
                call_type="subagent",
                agent_role="general",
                parent_step=2,
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-sub-2",
                        tool_name="Write",
                        input={
                            "file_path": "tests/test_token_rotation.py",
                            "content": "def test_rotation(): ...",
                        },
                    )
                ],
            ),
        ],
    )


def test_subagent_burst_intent_sourced_from_task_prompt():
    record = _record_with_subagent_and_main_edits()
    trace_map = build_trace_map(record)

    # The dispatch surfaces as a subagent_call node carrying the prompt.
    dispatch_nodes = [n for n in trace_map.nodes if n.action_type == "subagent_call"]
    assert len(dispatch_nodes) == 1
    dispatch_meta = dispatch_nodes[0].metadata["subagent_dispatch"]
    assert dispatch_meta["subagent_type"] == "general-purpose"
    assert dispatch_meta["prompt"].startswith("Migrate session token storage")

    # Sub-agent file_edit nodes carry the join provenance.
    sub_edits = [
        n
        for n in trace_map.nodes
        if n.action_type == "file_edit" and n.metadata.get("call_type") == "subagent"
    ]
    assert len(sub_edits) == 2
    assert all(n.metadata.get("parent_step") == 2 for n in sub_edits)

    # Explicit gap keeps the main-agent edit (step 3) and the tail
    # sub-agent edits (steps 40-41) in separate bursts.
    bursts = detect_bursts(trace_map, gap=10, commit_lookup=False)
    assert len(bursts) == 2

    main_burst, sub_burst = bursts[0], bursts[1]

    # Main-agent burst keeps the user-instruction chain.
    assert main_burst.intent.get("intent_source") in (None, "user_instruction_chain")
    assert main_burst.intent["most_substantive_spec"]["text"].startswith(
        "Refactor the auth module"
    )

    # Sub-agent burst is re-attributed to the Task prompt.
    assert sub_burst.intent["intent_source"] == "subagent_dispatch"
    spec_text = sub_burst.intent["most_substantive_spec"]["text"]
    assert spec_text.startswith("Migrate session token storage")
    # NOT the parent's last turn, NOT the parent's broad goal.
    assert "ship it" not in spec_text
    assert "Refactor the auth module" not in spec_text
    # The dispatch step (2), not a tail step, is the spec's anchor.
    assert sub_burst.intent["most_substantive_spec"]["step"] == 2
    assert sub_burst.intent["subagent"]["subagent_type"] == "general-purpose"
    assert sub_burst.intent["subagent"]["dispatch_step"] == 2
    # Legacy alias mirrors the re-attributed spec.
    assert sub_burst.intent_text.startswith("Migrate session token storage")


def test_mixed_burst_falls_back_to_user_chain():
    """A burst mixing a main-agent edit and a sub-agent edit stays on the
    user-instruction chain (the safe default)."""
    record = TraceRecord(
        trace_id="trace-mixed",
        session_id="session-mixed",
        agent=_AGENT,
        task={"description": "mixed"},
        steps=[
            Step(step_index=1, role="user", content="Do the broad refactor."),
            Step(
                step_index=2,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-d",
                        tool_name="Task",
                        input={"subagent_type": "general", "prompt": "narrow task"},
                    )
                ],
            ),
            Step(
                step_index=3,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-main",
                        tool_name="Edit",
                        input={"file_path": "a.py", "old_string": "x", "new_string": "y"},
                    )
                ],
            ),
            Step(
                step_index=4,
                role="agent",
                call_type="subagent",
                parent_step=2,
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-sub",
                        tool_name="Edit",
                        input={"file_path": "b.py", "old_string": "x", "new_string": "y"},
                    )
                ],
            ),
        ],
    )
    trace_map = build_trace_map(record)
    bursts = detect_bursts(trace_map, commit_lookup=False)
    assert len(bursts) == 1
    burst = bursts[0]
    assert burst.intent.get("intent_source") in (None, "user_instruction_chain")
    assert burst.intent["most_substantive_spec"]["text"].startswith("Do the broad refactor")

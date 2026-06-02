from __future__ import annotations

from opentraces_schema import Agent, Step, ToolCall, TraceRecord

from opentraces.core.skill_detection import detect_skill_invocations, trace_skill_names


def _trace(
    *,
    agent_name: str,
    steps: list[Step] | None = None,
    metadata: dict | None = None,
) -> TraceRecord:
    return TraceRecord(
        trace_id=f"trace-{agent_name}",
        session_id=f"session-{agent_name}",
        agent=Agent(name=agent_name),
        task={"description": "Use a skill to inspect the trace."},
        steps=steps or [],
        metadata=metadata or {},
    )


def test_detects_claude_slash_command_skill_metadata() -> None:
    record = _trace(
        agent_name="claude-code",
        metadata={
            "skill_invocations": [
                {
                    "name": "scout-research",
                    "source": "claude_slash_command",
                    "command_name": "/scout-research",
                    "args": "research browser-harness for VFS retrieval",
                }
            ]
        },
    )

    invocations = detect_skill_invocations(record)

    assert [invocation.skill_name for invocation in invocations] == ["scout-research"]
    assert invocations[0].source == "claude_slash_command"
    assert invocations[0].command_name == "/scout-research"
    assert invocations[0].args == "research browser-harness for VFS retrieval"
    assert trace_skill_names(record) == ["scout-research"]


def test_detects_codex_skill_name_tool_call_and_metadata_without_double_counting() -> None:
    record = _trace(
        agent_name="codex-cli",
        steps=[
            Step(
                step_index=2,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="call_skill",
                        tool_name="skill_invoke",
                        input={"skill_name": "opentraces"},
                    )
                ],
            )
        ],
        metadata={
            "skill_invocations": [
                {
                    "step_index": 2,
                    "tool_call_id": "call_skill",
                    "skill_name": "opentraces",
                    "source": "codex_cli_tool_call",
                }
            ]
        },
    )

    invocations = detect_skill_invocations(record)

    assert len(invocations) == 1
    assert invocations[0].skill_name == "opentraces"
    assert invocations[0].source == "codex_cli_tool_call"
    assert invocations[0].tool_call_id == "call_skill"
    assert trace_skill_names(record) == ["opentraces"]


def test_detects_codex_skill_metadata_without_tool_call_rows() -> None:
    record = _trace(
        agent_name="codex-cli",
        metadata={
            "skill_invocations": [
                {
                    "step_index": 2,
                    "tool_call_id": "call_skill",
                    "skill_name": "opentraces",
                    "source": "codex_cli_tool_call",
                }
            ]
        },
    )

    invocations = detect_skill_invocations(record)

    assert [invocation.skill_name for invocation in invocations] == ["opentraces"]
    assert invocations[0].metadata_index == 0
    assert invocations[0].source == "codex_cli_tool_call"


def test_detects_codex_skill_body_read_tool_call_without_metadata() -> None:
    record = _trace(
        agent_name="codex-cli",
        steps=[
            Step(
                step_index=3,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="call_read_skill",
                        tool_name="exec_command",
                        input={
                            "cmd": (
                                "sed -n '1,120p' "
                                "/tmp/home/.codex/skills/opentraces/SKILL.md"
                            ),
                            "workdir": "/tmp/project",
                        },
                    )
                ],
            )
        ],
    )

    invocations = detect_skill_invocations(record)

    assert len(invocations) == 1
    assert invocations[0].skill_name == "opentraces"
    assert invocations[0].source == "codex_cli_skill_body_read"
    assert invocations[0].step_index == 3
    assert invocations[0].tool_call_id == "call_read_skill"
    assert invocations[0].evidence["skill_path"] == (
        "/tmp/home/.codex/skills/opentraces/SKILL.md"
    )


def test_ignores_glob_skill_body_read_tool_call() -> None:
    record = _trace(
        agent_name="codex-cli",
        steps=[
            Step(
                step_index=3,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="call_read_skill",
                        tool_name="exec_command",
                        input={
                            "cmd": "ls /tmp/home/.codex/skills/*/SKILL.md",
                            "workdir": "/tmp/project",
                        },
                    )
                ],
            )
        ],
    )

    assert detect_skill_invocations(record) == []


def test_skill_body_read_scanner_skips_long_non_skill_paths() -> None:
    bad_path = "/tmp/home/" + ("nested/" * 4000) + "NOT_A_SKILL.md"
    record = _trace(
        agent_name="codex-cli",
        steps=[
            Step(
                step_index=3,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="call_read_skill",
                        tool_name="exec_command",
                        input={
                            "cmd": (
                                f"cat {bad_path} && sed -n '1,40p' "
                                "/tmp/home/.codex/skills/opentraces/SKILL.md"
                            ),
                            "workdir": "/tmp/project",
                        },
                    )
                ],
            )
        ],
    )

    invocations = detect_skill_invocations(record)

    assert [invocation.skill_name for invocation in invocations] == ["opentraces"]

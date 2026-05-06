"""Tests for Claude Code session parser."""

import json
from pathlib import Path

import pytest

from opentraces.capture.claude_code.parse import ClaudeCodeParser
from opentraces_schema import TraceRecord


def _write_session(tmp_path: Path, lines: list[dict], name: str = "test-session.jsonl") -> Path:
    """Write JSONL lines to a temp session file."""
    session_file = tmp_path / name
    with open(session_file, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return session_file


def _make_minimal_session() -> list[dict]:
    """Create a minimal valid Claude Code session."""
    return [
        {
            "type": "user",
            "sessionId": "test-sess",
            "timestamp": "2026-03-27T10:00:00Z",
            "version": "1.0.83",
            "message": {"role": "user", "content": "Fix the bug in parser.ts"},
        },
        {
            "type": "assistant",
            "sessionId": "test-sess",
            "timestamp": "2026-03-27T10:00:05Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "I need to read the file first"},
                    {
                        "type": "tool_use",
                        "id": "tc_001",
                        "name": "Read",
                        "input": {"file_path": "src/parser.ts", "offset": 1},
                    },
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 80,
                    "cache_creation_input_tokens": 20,
                },
            },
        },
        {
            "type": "user",
            "sessionId": "test-sess",
            "timestamp": "2026-03-27T10:00:06Z",
            "toolUseResult": {"stdout": "function parse() { ... }"},
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tc_001",
                        "content": "function parse() {\n  return null;\n}",
                    }
                ],
            },
        },
        {
            "type": "assistant",
            "sessionId": "test-sess",
            "timestamp": "2026-03-27T10:00:10Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I found the bug. The parse function returns null."},
                ],
                "usage": {
                    "input_tokens": 200,
                    "output_tokens": 100,
                    "cache_read_input_tokens": 150,
                    "cache_creation_input_tokens": 50,
                },
            },
        },
    ]


def _current_repo_session_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    encoded = repo_root.resolve().as_posix().replace("/", "-")
    return Path.home() / ".claude" / "projects" / encoded


class TestClaudeCodeParser:
    def test_discover_sessions(self, tmp_path):
        project_dir = tmp_path / "project-1"
        project_dir.mkdir()
        (project_dir / "session-1.jsonl").write_text("{}\n")
        (project_dir / "session-2.jsonl").write_text("{}\n")

        parser = ClaudeCodeParser()
        sessions = list(parser.discover_sessions(tmp_path))
        assert len(sessions) == 2

    def test_discover_skips_subagent_files(self, tmp_path):
        project_dir = tmp_path / "project-1"
        project_dir.mkdir()
        (project_dir / "main-session.jsonl").write_text("{}\n")
        sub_dir = project_dir / "main-session" / "subagents"
        sub_dir.mkdir(parents=True)
        (sub_dir / "agent-abc.jsonl").write_text("{}\n")

        parser = ClaudeCodeParser()
        sessions = list(parser.discover_sessions(tmp_path))
        assert len(sessions) == 1
        assert "main-session" in sessions[0].name

    def test_parse_minimal_session(self, tmp_path):
        lines = _make_minimal_session()
        session_file = _write_session(tmp_path, lines)

        parser = ClaudeCodeParser()
        record = parser.parse_session(session_file)

        assert record is not None
        assert isinstance(record, TraceRecord)
        assert record.agent.name == "claude-code"
        assert record.agent.version == "1.0.83"
        assert len(record.steps) >= 2

    def test_parse_marks_trace_trails_snapshot_resume_contract(self, tmp_path):
        lines = _make_minimal_session()
        session_file = _write_session(tmp_path, lines)

        parser = ClaudeCodeParser()
        record = parser.parse_session(session_file)

        assert record is not None
        assert record.schema_version == "0.3.0"
        assert record.metadata["trace_trails"] == {
            "schema_version": "opentraces.trace_trails.v1",
            "snapshot_resume_contract": "opentraces.snapshot_resume.v1",
        }

    def test_tool_result_correlation(self, tmp_path):
        lines = _make_minimal_session()
        session_file = _write_session(tmp_path, lines)

        parser = ClaudeCodeParser()
        record = parser.parse_session(session_file)

        # Find step with tool calls
        tool_steps = [s for s in record.steps if s.tool_calls]
        assert len(tool_steps) >= 1

        step = tool_steps[0]
        assert step.tool_calls[0].tool_name == "Read"
        assert step.tool_calls[0].tool_call_id == "tc_001"
        assert len(step.observations) >= 1
        assert step.observations[0].source_call_id == "tc_001"
        assert "parse" in (step.observations[0].content or "")

    def test_reasoning_content_extracted(self, tmp_path):
        lines = _make_minimal_session()
        session_file = _write_session(tmp_path, lines)

        parser = ClaudeCodeParser()
        record = parser.parse_session(session_file)

        # Find step with reasoning
        reasoning_steps = [s for s in record.steps if s.reasoning_content]
        assert len(reasoning_steps) >= 1
        assert "read the file" in reasoning_steps[0].reasoning_content

    def test_token_usage_extracted(self, tmp_path):
        lines = _make_minimal_session()
        session_file = _write_session(tmp_path, lines)

        parser = ClaudeCodeParser()
        record = parser.parse_session(session_file)

        # Check metrics aggregation
        assert record.metrics.total_input_tokens > 0
        assert record.metrics.total_output_tokens > 0

    def test_parser_collects_step_anchors(self, tmp_path):
        """Anchors are captured on the parser instance for local state,
        not embedded in the trace record (schema stays portable)."""
        lines = _make_minimal_session()
        lines[0]["uuid"] = "line-user-001"
        lines[1]["uuid"] = "line-assistant-001"
        lines[3]["uuid"] = "line-assistant-002"
        session_file = _write_session(tmp_path, lines)

        parser = ClaudeCodeParser()
        record = parser.parse_session(session_file)

        assert record is not None
        assert len(record.steps) >= 2

        # Steps themselves carry no anchor field.
        for step in record.steps:
            assert not hasattr(step, "source_anchor") or getattr(
                step, "source_anchor", None
            ) is None

        # Parser exposes the anchor map keyed by step_index.
        anchors = parser.step_anchors
        assert anchors[1]["entry_uuid"] == "line-user-001"
        assert anchors[1]["line_no"] == 0
        assert anchors[1]["session_file_relpath"] is None

        assert anchors[2]["entry_uuid"] == "line-assistant-001"
        assert anchors[2]["line_no"] == 1

    def test_incremental_parse_keeps_first_appended_entry(self, tmp_path):
        """Seeking to the prior EOF must not drop the first new JSONL line."""
        session_file = _write_session(
            tmp_path,
            _make_minimal_session()[:1],
            "incremental-session.jsonl",
        )
        byte_offset = session_file.stat().st_size
        with session_file.open("a") as f:
            f.write(json.dumps({
                "type": "user",
                "sessionId": "incremental-sess",
                "timestamp": "2026-03-27T10:00:01Z",
                "message": {"role": "user", "content": "resume from here"},
            }) + "\n")
            f.write(json.dumps({
                "type": "assistant",
                "sessionId": "incremental-sess",
                "timestamp": "2026-03-27T10:00:02Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tc_incremental", "name": "Bash",
                         "input": {"command": "echo hello"}},
                    ],
                    "usage": {"input_tokens": 20, "output_tokens": 10},
                },
            }) + "\n")

        parser = ClaudeCodeParser()
        record = parser.parse_session(session_file, byte_offset=byte_offset)

        assert record is not None
        assert len(record.steps) == 2
        assert record.steps[0].role == "user"
        assert record.steps[0].content == "resume from here"
        assert record.steps[1].tool_calls[0].tool_name == "Bash"

    def test_snippet_extraction_from_read(self, tmp_path):
        lines = _make_minimal_session()
        session_file = _write_session(tmp_path, lines)

        parser = ClaudeCodeParser()
        record = parser.parse_session(session_file)

        all_snippets = [s for step in record.steps for s in step.snippets]
        assert len(all_snippets) >= 1
        assert all_snippets[0].file_path == "src/parser.ts"
        assert all_snippets[0].language == "typescript"

    def test_warmup_detection(self, tmp_path):
        lines = [
            {
                "type": "user",
                "sessionId": "s",
                "timestamp": "2026-03-27T10:00:00Z",
                "message": {"role": "user", "content": "Hello"},
            },
            {
                "type": "assistant",
                "sessionId": "s",
                "timestamp": "2026-03-27T10:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "thinking", "thinking": ""}],
                    "usage": {"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 5000, "cache_creation_input_tokens": 100},
                },
            },
            # Need a real step with tool call for quality threshold
            {
                "type": "assistant",
                "sessionId": "s",
                "timestamp": "2026-03-27T10:00:02Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tc_1", "name": "Bash", "input": {"command": "ls"}},
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                },
            },
            {
                "type": "user",
                "sessionId": "s",
                "timestamp": "2026-03-27T10:00:03Z",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "tc_1", "content": "file1.txt"}],
                },
            },
        ]
        session_file = _write_session(tmp_path, lines)

        parser = ClaudeCodeParser()
        record = parser.parse_session(session_file)
        assert record is not None

        warmup_steps = [s for s in record.steps if s.call_type == "warmup"]
        assert len(warmup_steps) >= 1

    def test_quality_filter_rejects_trivial(self, tmp_path):
        """Session with no tool calls should be rejected."""
        lines = [
            {
                "type": "user",
                "sessionId": "s",
                "timestamp": "2026-03-27T10:00:00Z",
                "message": {"role": "user", "content": "Hello"},
            },
            {
                "type": "assistant",
                "sessionId": "s",
                "timestamp": "2026-03-27T10:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hi there"}],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
        ]
        session_file = _write_session(tmp_path, lines)

        parser = ClaudeCodeParser()
        record = parser.parse_session(session_file)
        assert record is None  # Below quality threshold

    def test_corrupted_lines_handled(self, tmp_path):
        """Parser should handle corrupted lines gracefully."""
        session_file = tmp_path / "bad-session.jsonl"
        good_lines = _make_minimal_session()
        with open(session_file, "w") as f:
            f.write(json.dumps(good_lines[0]) + "\n")
            f.write("this is not valid json\n")
            for line in good_lines[1:]:
                f.write(json.dumps(line) + "\n")

        parser = ClaudeCodeParser()
        record = parser.parse_session(session_file)
        # Should still parse (1 corrupted out of 5 = 20% < threshold? Actually 1/5=20% > 5%)
        # With 5 lines and 1 corrupted that's 20% which exceeds 5% threshold
        # So it should return None
        # Actually our threshold is 5%, and 1/5 = 20%, so this should be rejected
        # Let me adjust - add more good lines so corruption rate is low
        assert record is None  # 20% corruption rate exceeds 5% threshold

    def test_content_hash_in_output(self, tmp_path):
        lines = _make_minimal_session()
        session_file = _write_session(tmp_path, lines)

        parser = ClaudeCodeParser()
        record = parser.parse_session(session_file)
        assert record is not None

        jsonl = record.to_jsonl_line()
        parsed = json.loads(jsonl)
        assert parsed["content_hash"] is not None
        assert len(parsed["content_hash"]) == 64

    def test_first_user_message_as_task(self, tmp_path):
        lines = _make_minimal_session()
        session_file = _write_session(tmp_path, lines)

        parser = ClaudeCodeParser()
        record = parser.parse_session(session_file)
        assert record is not None
        assert record.task.description is not None
        assert "bug" in record.task.description.lower() or "parser" in record.task.description.lower()

    def test_first_user_message_skips_synthetic_wrappers(self, tmp_path):
        """Local-command caveats and slash-command wrappers must not be task.description."""
        lines = [
            {
                "type": "user", "sessionId": "s", "timestamp": "2026-03-27T10:00:00Z",
                "message": {"role": "user", "content": "<local-command-caveat>Caveat: ignore this</local-command-caveat>"},
            },
            {
                "type": "user", "sessionId": "s", "timestamp": "2026-03-27T10:00:01Z",
                "message": {"role": "user", "content": "<command-name>/clear</command-name>"},
            },
            {
                "type": "user", "sessionId": "s", "timestamp": "2026-03-27T10:00:02Z",
                "message": {"role": "user", "content": "Actually fix the login bug"},
            },
        ] + _make_minimal_session()[1:]  # drop the default first user message
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert record.task.description == "Actually fix the login bug"

    def test_slash_skill_command_records_skill_metadata(self, tmp_path):
        lines = [
            {
                "type": "user",
                "sessionId": "s",
                "timestamp": "2026-05-06T13:18:37.417Z",
                "uuid": "cmd-uuid",
                "message": {
                    "role": "user",
                    "content": (
                        "<command-message>scout-research</command-message>\n"
                        "<command-name>/scout-research</command-name>\n"
                        "<command-args>research browser-harness for VFS retrieval</command-args>"
                    ),
                },
            },
            {
                "type": "user",
                "sessionId": "s",
                "timestamp": "2026-05-06T13:18:37.417Z",
                "uuid": "skill-body-uuid",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Base directory for this skill: "
                                "/Users/me/.claude/skills/scout-research\n\n"
                                "# Scout Research\n\nSkill instructions..."
                            ),
                        }
                    ],
                },
            },
        ] + _make_minimal_session()[1:]

        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))

        assert record is not None
        assert record.task.description == "research browser-harness for VFS retrieval"
        assert record.metadata["skill_invocations"] == [
            {
                "name": "scout-research",
                "source": "claude_slash_command",
                "timestamp": "2026-05-06T13:18:37.417Z",
                "body_uuid": "skill-body-uuid",
                "body_line_no": 1,
                "command_name": "/scout-research",
                "command_message": "scout-research",
                "args": "research browser-harness for VFS retrieval",
                "command_uuid": "cmd-uuid",
                "command_line_no": 0,
            }
        ]
        rendered_steps = "\n".join(step.content or "" for step in record.steps)
        assert "<command-name>" not in rendered_steps
        assert "Base directory for this skill" not in rendered_steps

    def test_first_user_message_skips_compact_resumption(self, tmp_path):
        lines = [
            {
                "type": "user", "sessionId": "s", "timestamp": "2026-03-27T10:00:00Z",
                "message": {"role": "user",
                            "content": "This session is being continued from a previous conversation that ran out of context."},
            },
            {
                "type": "user", "sessionId": "s", "timestamp": "2026-03-27T10:00:01Z",
                "message": {"role": "user", "content": "Add a retry to the uploader"},
            },
        ] + _make_minimal_session()[1:]
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert record.task.description == "Add a retry to the uploader"

    def test_agent_model_normalized_with_provider_prefix(self, tmp_path):
        lines = [{
            "type": "system", "subtype": "init",
            "sessionId": "s", "timestamp": "2026-03-27T10:00:00Z",
            "model": "claude-opus-4-6", "claude_code_version": "2.1.85",
            "cwd": "/home/user/project", "tools": [], "mcp_servers": [],
        }] + _make_minimal_session()
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert record.agent.model == "anthropic/claude-opus-4-6"
        for step in record.steps:
            if step.model is not None:
                assert "/" in step.model

    def test_dangling_tool_use_handled(self, tmp_path):
        """Tool call without result should get error observation."""
        lines = [
            {
                "type": "user",
                "sessionId": "s",
                "timestamp": "2026-03-27T10:00:00Z",
                "message": {"role": "user", "content": "Do something"},
            },
            {
                "type": "assistant",
                "sessionId": "s",
                "timestamp": "2026-03-27T10:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tc_orphan", "name": "Bash", "input": {"command": "exit 1"}},
                        {"type": "tool_use", "id": "tc_ok", "name": "Read", "input": {"file_path": "x.txt"}},
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            },
            {
                "type": "user",
                "sessionId": "s",
                "timestamp": "2026-03-27T10:00:02Z",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tc_ok", "content": "file content"},
                    ],
                },
            },
            {
                "type": "assistant",
                "sessionId": "s",
                "timestamp": "2026-03-27T10:00:03Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Done"}],
                    "usage": {"input_tokens": 50, "output_tokens": 20},
                },
            },
        ]
        session_file = _write_session(tmp_path, lines)

        parser = ClaudeCodeParser()
        record = parser.parse_session(session_file)
        assert record is not None

        # Find the dangling observation
        for step in record.steps:
            for obs in step.observations:
                if obs.source_call_id == "tc_orphan":
                    assert obs.error == "no_result"
                    return
        pytest.fail("Dangling tool_use observation not found")


class TestParentStepIntegrity:
    """Verify parent_step references remain valid after renumbering."""

    def test_parent_step_updated_after_renumber(self, tmp_path):
        """Simulate a session with subagent steps to verify parent_step fixup."""
        # Create a main session that triggers an Agent tool call
        main_lines = [
            {
                "type": "user",
                "sessionId": "main-sess",
                "timestamp": "2026-03-27T10:00:00Z",
                "version": "1.0.83",
                "message": {"role": "user", "content": "Investigate the bug"},
            },
            {
                "type": "assistant",
                "sessionId": "main-sess",
                "timestamp": "2026-03-27T10:00:05Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Let me look into this."},
                        {
                            "type": "tool_use",
                            "id": "tc_agent_1",
                            "name": "Agent",
                            "input": {"description": "explore the codebase", "subagent_type": "Explore"},
                        },
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 50,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                },
            },
            {
                "type": "user",
                "sessionId": "main-sess",
                "timestamp": "2026-03-27T10:00:10Z",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tc_agent_1", "content": "Done exploring"},
                    ],
                },
            },
            {
                "type": "assistant",
                "sessionId": "main-sess",
                "timestamp": "2026-03-27T10:00:15Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Based on my exploration, the bug is in parser.ts."},
                        {
                            "type": "tool_use",
                            "id": "tc_read_1",
                            "name": "Read",
                            "input": {"file_path": "src/parser.ts"},
                        },
                    ],
                    "usage": {"input_tokens": 200, "output_tokens": 100,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                },
            },
            {
                "type": "user",
                "sessionId": "main-sess",
                "timestamp": "2026-03-27T10:00:16Z",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tc_read_1", "content": "function parse() {}"},
                    ],
                },
            },
        ]

        # Create the subagent session files
        main_session_dir = tmp_path / "main-sess"
        subagents_dir = main_session_dir / "subagents"
        subagents_dir.mkdir(parents=True)

        # Write meta.json for the subagent
        meta = {"description": "explore the codebase"}
        (subagents_dir / "sub-agent-1.meta.json").write_text(json.dumps(meta))

        # Write subagent session JSONL
        sub_lines = [
            {
                "type": "user",
                "sessionId": "sub-agent-1",
                "timestamp": "2026-03-27T10:00:06Z",
                "message": {"role": "user", "content": "Explore the codebase"},
            },
            {
                "type": "assistant",
                "sessionId": "sub-agent-1",
                "timestamp": "2026-03-27T10:00:07Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tc_sub_1",
                            "name": "Glob",
                            "input": {"pattern": "**/*.ts"},
                        },
                    ],
                    "usage": {"input_tokens": 50, "output_tokens": 30,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                },
            },
            {
                "type": "user",
                "sessionId": "sub-agent-1",
                "timestamp": "2026-03-27T10:00:08Z",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tc_sub_1", "content": "src/parser.ts"},
                    ],
                },
            },
        ]
        with open(subagents_dir / "sub-agent-1.jsonl", "w") as f:
            for line in sub_lines:
                f.write(json.dumps(line) + "\n")

        # Write main session
        main_file = _write_session(tmp_path, main_lines, "main-sess.jsonl")

        parser = ClaudeCodeParser()
        record = parser.parse_session(main_file)
        assert record is not None

        # Verify all parent_step references point to valid step indices
        valid_indices = {s.step_index for s in record.steps}
        for step in record.steps:
            if step.parent_step is not None:
                assert step.parent_step in valid_indices, (
                    f"Step {step.step_index} has parent_step={step.parent_step} "
                    f"which is not in valid indices {valid_indices}"
                )

        # Verify step indices are sequential starting from 1
        indices = [s.step_index for s in record.steps]
        assert indices == list(range(1, len(record.steps) + 1)), (
            f"Step indices not sequential: {indices}"
        )

    def test_step_anchors_follow_renumbered_subagent_steps(self, tmp_path):
        """Sub-agent inlining must keep anchors aligned with final step ids."""
        main_lines = [
            {
                "type": "user",
                "sessionId": "main-sess",
                "uuid": "main-user-1",
                "timestamp": "2026-03-27T10:00:00Z",
                "message": {"role": "user", "content": "Investigate the bug"},
            },
            {
                "type": "assistant",
                "sessionId": "main-sess",
                "uuid": "main-assistant-1",
                "timestamp": "2026-03-27T10:00:05Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tc_agent_1",
                            "name": "Agent",
                            "input": {
                                "description": "explore the codebase",
                                "subagent_type": "Explore",
                            },
                        },
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 50,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                },
            },
        ]

        main_session_dir = tmp_path / "main-sess"
        subagents_dir = main_session_dir / "subagents"
        subagents_dir.mkdir(parents=True)
        (subagents_dir / "sub-agent-1.meta.json").write_text(
            json.dumps({"description": "explore the codebase"})
        )

        sub_lines = [
            {
                "type": "user",
                "sessionId": "sub-agent-1",
                "uuid": "sub-user-1",
                "timestamp": "2026-03-27T10:00:06Z",
                "message": {"role": "user", "content": "Explore the codebase"},
            },
            {
                "type": "assistant",
                "sessionId": "sub-agent-1",
                "uuid": "sub-assistant-1",
                "timestamp": "2026-03-27T10:00:07Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Found the bug"}],
                    "usage": {"input_tokens": 50, "output_tokens": 30,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                },
            },
        ]
        with open(subagents_dir / "sub-agent-1.jsonl", "w") as f:
            for line in sub_lines:
                f.write(json.dumps(line) + "\n")

        main_file = _write_session(tmp_path, main_lines, "main-sess.jsonl")

        parser = ClaudeCodeParser()
        record = parser.parse_session(main_file)
        assert record is not None
        assert [s.step_index for s in record.steps] == [1, 2, 3, 4]

        anchors = parser.step_anchors
        assert anchors[1]["entry_uuid"] == "main-user-1"
        assert anchors[1]["session_file_relpath"] is None
        assert anchors[2]["entry_uuid"] == "sub-user-1"
        assert anchors[2]["line_no"] == 0
        assert anchors[3]["entry_uuid"] == "sub-assistant-1"
        assert anchors[3]["line_no"] == 1
        assert anchors[4]["entry_uuid"] == "main-assistant-1"


class TestParserOnRealSessions:
    """Integration tests using real Claude Code sessions if available."""

    @pytest.fixture(autouse=True)
    def _isolate_opentraces_global_state(self):
        """Use the real HOME for local-session parser smoke tests."""
        yield

    @pytest.fixture
    def projects_path(self):
        path = Path.home() / ".claude" / "projects"
        if not path.exists():
            pytest.skip("No Claude Code sessions available")
        return path

    def test_can_parse_at_least_one_session(self, projects_path):
        parser = ClaudeCodeParser()
        sessions = list(parser.discover_sessions(projects_path))
        assert len(sessions) > 0, "No sessions discovered"

        parsed = 0
        for session in sessions[:50]:
            record = parser.parse_session(session)
            if record is not None:
                parsed += 1
                assert record.agent.name == "claude-code"
                assert len(record.steps) >= 2
                assert record.content_hash is None  # Not computed until to_jsonl_line()
                break

        assert parsed > 0, "Could not parse any sessions"

    def test_recent_repo_sessions_round_trip_away_summaries(self):
        session_dir = _current_repo_session_dir()
        if not session_dir.exists():
            pytest.skip("No Claude Code sessions for this repo")

        recent_sessions = sorted(
            session_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        recap_sessions: list[tuple[Path, list[dict[str, str | None]]]] = []

        for session in recent_sessions[:20]:
            raw_recaps: list[dict[str, str | None]] = []
            with session.open() as f:
                for line in f:
                    row = json.loads(line)
                    if (
                        row.get("type") == "system"
                        and row.get("subtype") == "away_summary"
                        and row.get("content")
                    ):
                        raw_recaps.append({
                            "timestamp": row.get("timestamp"),
                            "content": row["content"],
                        })
            if raw_recaps:
                recap_sessions.append((session, raw_recaps))
            if len(recap_sessions) >= 3:
                break

        if not recap_sessions:
            pytest.skip("No away_summary records in recent repo sessions")

        parser = ClaudeCodeParser()
        for session, raw_recaps in recap_sessions:
            record = parser.parse_session(session)
            assert record is not None, f"failed to parse {session.name}"

            parsed_recaps = record.metadata.get("away_summaries") or []
            assert parsed_recaps == raw_recaps

            user_texts = [step.content or "" for step in record.steps if step.role == "user"]
            for recap in raw_recaps:
                assert recap["content"] not in user_texts


# ---------------------------------------------------------------------------
# New line type enrichment tests
# ---------------------------------------------------------------------------

class TestSessionEnrichmentLines:
    """Parser correctly handles type:'result', type:'system', type:'opentraces_hook'."""

    # -- type: 'result' -------------------------------------------------------

    def test_result_success_overrides_estimated_cost(self, tmp_path):
        lines = _make_minimal_session() + [{
            "type": "result", "subtype": "success",
            "sessionId": "test-sess", "timestamp": "2026-03-27T10:01:00Z",
            "total_cost_usd": 0.042, "stop_reason": "end_turn",
            "num_turns": 3, "duration_ms": 5000,
        }]
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert record.metrics.estimated_cost_usd == pytest.approx(0.042)

    def test_result_success_populates_metadata(self, tmp_path):
        lines = _make_minimal_session() + [{
            "type": "result", "subtype": "success",
            "sessionId": "test-sess", "timestamp": "2026-03-27T10:01:00Z",
            "total_cost_usd": 0.01, "stop_reason": "end_turn",
            "num_turns": 2, "permission_denials": 1,
        }]
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert record.metadata["stop_reason"] == "end_turn"
        assert record.metadata["num_turns"] == 2
        assert record.metadata["permission_denials"] == 1

    def test_result_success_end_turn_is_inferred_not_derived(self, tmp_path):
        """end_turn is a generation stop condition, not a task-success signal.
        success stays None - enrichment pipeline (git commit, CI) sets it later."""
        lines = _make_minimal_session() + [{
            "type": "result", "subtype": "success",
            "sessionId": "test-sess", "timestamp": "2026-03-27T10:01:00Z",
            "total_cost_usd": 0.01, "stop_reason": "end_turn",
        }]
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert record.outcome.success is None  # not known from stop reason alone
        assert record.outcome.signal_source == "result_message"
        assert record.outcome.signal_confidence == "inferred"

    def test_result_error_subtype_sets_outcome_failure(self, tmp_path):
        lines = _make_minimal_session() + [{
            "type": "result", "subtype": "error_max_turns",
            "sessionId": "test-sess", "timestamp": "2026-03-27T10:01:00Z",
        }]
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert record.outcome.success is False
        assert record.outcome.signal_source == "result_message"
        assert record.outcome.description == "error_max_turns"
        assert record.metadata["stop_reason"] == "error_max_turns"

    def test_result_error_budget_exceeded(self, tmp_path):
        lines = _make_minimal_session() + [{
            "type": "result", "subtype": "error_max_budget_usd",
            "sessionId": "test-sess", "timestamp": "2026-03-27T10:01:00Z",
        }]
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert record.outcome.success is False
        assert record.metadata["stop_reason"] == "error_max_budget_usd"

    def test_no_result_line_outcome_is_empty(self, tmp_path):
        record = ClaudeCodeParser().parse_session(
            _write_session(tmp_path, _make_minimal_session())
        )
        assert record is not None
        assert record.outcome.success is None

    def test_result_duration_fallback_when_no_step_timestamps(self, tmp_path):
        """duration_ms from result message populates total_duration_s when steps lack timestamps."""
        lines = [
            {
                "type": "user", "sessionId": "s",
                "message": {"role": "user", "content": "Fix bug"},
            },
            {
                "type": "assistant", "sessionId": "s",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "t1", "name": "Read",
                                 "input": {"file_path": "x.py"}}],
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            },
            {
                "type": "user", "sessionId": "s",
                "message": {"role": "user",
                            "content": [{"type": "tool_result", "tool_use_id": "t1",
                                         "content": "ok"}]},
            },
            {
                "type": "assistant", "sessionId": "s",
                "message": {"role": "assistant",
                            "content": [{"type": "text", "text": "Done"}],
                            "usage": {"input_tokens": 120, "output_tokens": 30}},
            },
            {
                "type": "result", "subtype": "success", "sessionId": "s",
                "total_cost_usd": 0.005, "stop_reason": "end_turn",
                "duration_ms": 8000,
            },
        ]
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert record.metrics.total_duration_s == pytest.approx(8.0)

    # -- type: 'system' -------------------------------------------------------

    def test_system_init_extracts_model(self, tmp_path):
        lines = [{
            "type": "system", "subtype": "init",
            "sessionId": "s", "timestamp": "2026-03-27T10:00:00Z",
            "model": "claude-opus-4-5", "claude_code_version": "2.0.0",
            "cwd": "/home/user/project", "permissionMode": "default",
            "tools": [], "mcp_servers": [],
        }] + _make_minimal_session()
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert record.agent.model == "anthropic/claude-opus-4-5"
        assert record.agent.version == "2.0.0"

    def test_system_init_extracts_mcp_servers(self, tmp_path):
        lines = [{
            "type": "system", "subtype": "init",
            "sessionId": "s", "timestamp": "2026-03-27T10:00:00Z",
            "model": "claude-sonnet-4-6",
            "mcp_servers": [{"name": "filesystem"}, {"name": "slack"}],
            "tools": [], "permissionMode": "default",
        }] + _make_minimal_session()
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert record.metadata.get("mcp_servers") == ["filesystem", "slack"]

    def test_system_init_priority_over_queue_operation(self, tmp_path):
        """system/init model takes priority over queue-operation fallback."""
        lines = [{
            "type": "system", "subtype": "init",
            "sessionId": "s", "timestamp": "2026-03-27T10:00:00Z",
            "model": "claude-opus-4-5", "mcp_servers": [], "tools": [],
        }] + [{
            "type": "queue-operation", "sessionId": "s",
            "content": '{"model": "claude-haiku-fallback"}',
        }] + _make_minimal_session()
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert record.agent.model == "anthropic/claude-opus-4-5"

    def test_system_init_extracts_permission_mode(self, tmp_path):
        lines = [{
            "type": "system", "subtype": "init",
            "sessionId": "s", "permissionMode": "bypassPermissions",
            "model": "claude-sonnet-4-6", "mcp_servers": [], "tools": [],
        }] + _make_minimal_session()
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert record.metadata.get("permission_mode") == "bypassPermissions"

    def test_system_compact_boundary_sets_is_compacted(self, tmp_path):
        lines = _make_minimal_session() + [{
            "type": "system", "subtype": "compact_boundary",
            "sessionId": "s", "timestamp": "2026-03-27T10:01:00Z",
            "pre_tokens": 50000,
            "compact_metadata": {"trigger": "manual"},
        }]
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert record.metadata.get("is_compacted") is True
        events = record.metadata.get("compaction_events", [])
        assert len(events) == 1
        assert events[0]["pre_tokens"] == 50000
        assert events[0]["trigger"] == "manual"

    def test_system_compact_boundary_multiple_events(self, tmp_path):
        lines = _make_minimal_session() + [
            {
                "type": "system", "subtype": "compact_boundary",
                "sessionId": "s", "timestamp": "2026-03-27T10:01:00Z",
                "pre_tokens": 40000,
            },
            {
                "type": "system", "subtype": "compact_boundary",
                "sessionId": "s", "timestamp": "2026-03-27T10:02:00Z",
                "pre_tokens": 55000,
            },
        ]
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert len(record.metadata.get("compaction_events", [])) == 2

    def test_system_post_turn_summary_collected(self, tmp_path):
        lines = _make_minimal_session() + [{
            "type": "system", "subtype": "post_turn_summary",
            "sessionId": "s", "timestamp": "2026-03-27T10:01:00Z",
            "status_category": "completed",
            "is_noteworthy": True,
            "title": "Fixed the parser bug",
        }]
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        summaries = record.metadata.get("post_turn_summaries", [])
        assert len(summaries) == 1
        assert summaries[0]["status_category"] == "completed"
        assert summaries[0]["is_noteworthy"] is True
        assert summaries[0]["title"] == "Fixed the parser bug"

    # -- type: 'opentraces_hook' ----------------------------------------------

    def test_opentraces_hook_stop_captures_git_state(self, tmp_path):
        lines = _make_minimal_session() + [{
            "type": "opentraces_hook", "event": "Stop",
            "timestamp": "2026-03-27T10:01:00Z",
            "data": {
                "git": {"sha": "abc123", "dirty": False, "files_changed": 0},
                "permission_mode": "default",
                "trail": {
                    "worktree_root": str(tmp_path),
                    "tree_id": {"algo": "sha1", "hex": "a" * 40},
                    "git_head": {"algo": "sha1", "hex": "b" * 40},
                },
            },
        }]
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        git = record.metadata.get("hook_git_final", {})
        assert git.get("sha") == "abc123"
        assert git.get("dirty") is False
        stop = record.metadata.get("hook_stop", [])
        assert len(stop) == 1
        assert stop[0]["timestamp"] == "2026-03-27T10:01:00Z"
        assert stop[0]["trail"]["tree_id"]["hex"] == "a" * 40

    def test_opentraces_hook_postcompact_sets_is_compacted(self, tmp_path):
        lines = _make_minimal_session() + [{
            "type": "opentraces_hook", "event": "PostCompact",
            "timestamp": "2026-03-27T10:01:00Z",
            "data": {"messages_removed": 30, "messages_kept": 8},
        }]
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert record.metadata.get("is_compacted") is True
        events = record.metadata.get("compaction_events", [])
        assert len(events) == 1
        assert events[0]["messages_removed"] == 30
        assert events[0]["messages_kept"] == 8

    def test_opentraces_hook_pretooluse_preserves_trail_boundary(self, tmp_path):
        lines = _make_minimal_session() + [{
            "type": "opentraces_hook",
            "event": "PreToolUse",
            "timestamp": "2026-03-27T10:01:00Z",
            "data": {
                "tool": "Bash",
                "tool_use_id": "toolu_pre",
                "tool_input": {"command": "echo hi"},
                "trail": {
                    "worktree_root": str(tmp_path),
                    "tree_id": {"algo": "sha1", "hex": "a" * 40},
                    "git_head": {"algo": "sha1", "hex": "b" * 40},
                },
            },
        }]
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        pre = record.metadata["hook_pre_tool_use"]["toolu_pre"]
        assert pre["timestamp"] == "2026-03-27T10:01:00Z"
        assert pre["tool"] == "Bash"
        assert pre["tool_input"]["command"] == "echo hi"
        assert pre["trail"]["tree_id"]["hex"] == "a" * 40

    def test_opentraces_hook_posttooluse_preserves_trail_boundary(self, tmp_path):
        lines = _make_minimal_session() + [{
            "type": "opentraces_hook",
            "event": "PostToolUse",
            "timestamp": "2026-03-27T10:01:02Z",
            "data": {
                "tool": "Bash",
                "tool_use_id": "toolu_post",
                "capture_status": "hook_only",
                "limitations": ["hook_only"],
                "tool_input": {"command": "echo hi"},
                "tool_response": {"stdout": "hi\n"},
                "trail": {
                    "worktree_root": str(tmp_path),
                    "tree_id": {"algo": "sha1", "hex": "c" * 40},
                    "git_head": {"algo": "sha1", "hex": "d" * 40},
                },
            },
        }]
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        post = record.metadata["hook_post_tool_use"]["toolu_post"]
        assert post["timestamp"] == "2026-03-27T10:01:02Z"
        assert post["capture_status"] == "hook_only"
        assert post["limitations"] == ["hook_only"]
        assert post["trail"]["tree_id"]["hex"] == "c" * 40

    def test_hook_stop_permission_mode_fallback(self, tmp_path):
        """Hook Stop sets permission_mode when system/init is absent."""
        lines = _make_minimal_session() + [{
            "type": "opentraces_hook", "event": "Stop",
            "timestamp": "2026-03-27T10:01:00Z",
            "data": {"permission_mode": "acceptEdits", "git": {}},
        }]
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert record.metadata.get("permission_mode") == "acceptEdits"

    # -- slug -----------------------------------------------------------------

    def test_slug_extracted_from_transcript_message(self, tmp_path):
        lines = _make_minimal_session()
        # Add slug to the first line
        lines[0] = {**lines[0], "slug": "fix-the-parser-bug"}
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert record.metadata.get("slug") == "fix-the-parser-bug"

    def test_slug_takes_first_occurrence(self, tmp_path):
        """Only the first slug seen is kept."""
        lines = _make_minimal_session()
        lines[0] = {**lines[0], "slug": "first-slug"}
        lines[1] = {**lines[1], "slug": "second-slug"}
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert record.metadata.get("slug") == "first-slug"


class TestParseStepsEnrichment:
    """Tests for isMeta warmup flag and Bash exitCode handling."""

    def _make_session_with_bash(
        self, exit_code: int | None = 0, interrupted: bool = False
    ) -> list[dict]:
        """Session with a Bash tool call and configurable toolUseResult."""
        tur: dict = {"stdout": "output", "stderr": ""}
        if exit_code is not None:
            tur["exitCode"] = exit_code
        if interrupted:
            tur["interrupted"] = True
        return [
            {
                "type": "user", "sessionId": "s",
                "timestamp": "2026-03-27T10:00:00Z",
                "message": {"role": "user", "content": "run tests"},
            },
            {
                "type": "assistant", "sessionId": "s",
                "timestamp": "2026-03-27T10:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [{
                        "type": "tool_use", "id": "bash_001", "name": "Bash",
                        "input": {"command": "pytest"},
                    }],
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                },
            },
            {
                "type": "user", "sessionId": "s",
                "timestamp": "2026-03-27T10:00:03Z",
                "toolUseResult": tur,
                "message": {
                    "role": "user",
                    "content": [{
                        "type": "tool_result", "tool_use_id": "bash_001",
                        "content": "output",
                    }],
                },
            },
            {
                "type": "assistant", "sessionId": "s",
                "timestamp": "2026-03-27T10:00:05Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Tests done."}],
                    "usage": {"input_tokens": 150, "output_tokens": 30},
                },
            },
        ]

    def test_imeta_flag_marks_warmup_regardless_of_tokens(self, tmp_path):
        """isMeta=True on a high-token step still produces call_type='warmup'."""
        lines = _make_minimal_session()
        # Replace first assistant step with a high-token isMeta step
        lines.insert(1, {
            "type": "assistant", "sessionId": "test-sess",
            "timestamp": "2026-03-27T09:59:59Z",
            "isMeta": True,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "This is a warmup with lots of text"}],
                "usage": {"input_tokens": 5000, "output_tokens": 800},
            },
        })
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        warmup_steps = [s for s in record.steps if s.call_type == "warmup"]
        assert len(warmup_steps) >= 1
        warmup_contents = [s.content for s in warmup_steps]
        assert any("warmup" in (c or "") for c in warmup_contents)

    def test_bash_nonzero_exit_code_sets_obs_error(self, tmp_path):
        lines = self._make_session_with_bash(exit_code=1)
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        bash_step = next(s for s in record.steps if s.tool_calls)
        obs = next(o for o in bash_step.observations if o.source_call_id == "bash_001")
        assert obs.error == "exit_code:1"

    def test_bash_zero_exit_code_no_error(self, tmp_path):
        lines = self._make_session_with_bash(exit_code=0)
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        bash_step = next(s for s in record.steps if s.tool_calls)
        obs = next(o for o in bash_step.observations if o.source_call_id == "bash_001")
        assert obs.error is None

    def test_bash_interrupted_sets_obs_error(self, tmp_path):
        lines = self._make_session_with_bash(exit_code=0, interrupted=True)
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        bash_step = next(s for s in record.steps if s.tool_calls)
        obs = next(o for o in bash_step.observations if o.source_call_id == "bash_001")
        assert obs.error == "interrupted"

    def test_is_error_flag_takes_precedence_over_exit_code(self, tmp_path):
        """When is_error=True in tool_result, that wins over non-zero exitCode."""
        tur = {"exitCode": 1, "stdout": "", "stderr": "crash"}
        lines = [
            {
                "type": "user", "sessionId": "s",
                "timestamp": "2026-03-27T10:00:00Z",
                "message": {"role": "user", "content": "run"},
            },
            {
                "type": "assistant", "sessionId": "s",
                "timestamp": "2026-03-27T10:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "t1", "name": "Bash",
                                 "input": {"command": "bad"}}],
                    "usage": {"input_tokens": 50, "output_tokens": 10},
                },
            },
            {
                "type": "user", "sessionId": "s",
                "timestamp": "2026-03-27T10:00:02Z",
                "toolUseResult": tur,
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "t1",
                                 "content": "crash", "is_error": True}],
                },
            },
            {
                "type": "assistant", "sessionId": "s",
                "timestamp": "2026-03-27T10:00:03Z",
                "message": {"role": "assistant",
                            "content": [{"type": "text", "text": "Error handled."}],
                            "usage": {"input_tokens": 80, "output_tokens": 20}},
            },
        ]
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        bash_step = next(s for s in record.steps if s.tool_calls)
        obs = next(o for o in bash_step.observations if o.source_call_id == "t1")
        assert obs.error == "tool_error"

    def test_hook_git_sha_populates_vcs_base_commit(self, tmp_path):
        """Stop hook git SHA is promoted to Environment.vcs.base_commit."""
        lines = _make_minimal_session() + [{
            "type": "opentraces_hook", "event": "Stop",
            "timestamp": "2026-03-27T10:01:00Z",
            "data": {
                "git": {"sha": "deadbeef1234", "dirty": False, "files_changed": 2},
            },
        }]
        # Add a gitBranch to the first line so vcs type is git
        lines[0] = {**lines[0], "gitBranch": "feat/my-feature"}
        record = ClaudeCodeParser().parse_session(_write_session(tmp_path, lines))
        assert record is not None
        assert record.environment.vcs.type == "git"
        assert record.environment.vcs.base_commit == "deadbeef1234"
        assert record.environment.vcs.branch == "feat/my-feature"

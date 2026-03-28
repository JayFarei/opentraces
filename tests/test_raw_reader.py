"""Tests for the independent raw session JSONL reader."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from opentraces.quality.raw_reader import RawSessionSummary, read_raw_session


def _write_jsonl(lines: list[dict | str], tmp_path: Path) -> Path:
    """Write lines to a JSONL file. Strings are written as-is (for corrupted lines)."""
    path = tmp_path / "session.jsonl"
    with open(path, "w") as f:
        for line in lines:
            if isinstance(line, str):
                f.write(line + "\n")
            else:
                f.write(json.dumps(line) + "\n")
    return path


class TestMinimalSession:
    """Test with a synthetic 4-line session: user message, assistant with
    tool_use+thinking, user with tool_result, assistant with text."""

    @pytest.fixture
    def session_path(self, tmp_path: Path) -> Path:
        lines = [
            {
                "type": "user",
                "sessionId": "abc",
                "timestamp": "2026-03-28T10:00:00Z",
                "message": {
                    "role": "user",
                    "content": "hello world",
                },
            },
            {
                "type": "assistant",
                "sessionId": "abc",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "let me think about this..."},
                        {
                            "type": "tool_use",
                            "id": "tc_001",
                            "name": "Read",
                            "input": {"file_path": "/foo"},
                        },
                    ],
                    "model": "claude-opus-4-6-20250327",
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            },
            {
                "type": "user",
                "sessionId": "abc",
                "timestamp": "2026-03-28T10:00:05Z",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tc_001",
                            "content": "file contents here",
                        },
                    ],
                },
            },
            {
                "type": "assistant",
                "sessionId": "abc",
                "timestamp": "2026-03-28T10:00:10Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "I read the file for you"},
                    ],
                    "model": "claude-opus-4-6-20250327",
                    "usage": {"input_tokens": 200, "output_tokens": 30},
                },
            },
        ]
        return _write_jsonl(lines, tmp_path)

    def test_total_lines(self, session_path: Path) -> None:
        s = read_raw_session(session_path)
        assert s.total_lines == 4

    def test_message_counts(self, session_path: Path) -> None:
        s = read_raw_session(session_path)
        assert s.user_messages == 2
        assert s.assistant_messages == 2

    def test_tool_use_blocks(self, session_path: Path) -> None:
        s = read_raw_session(session_path)
        assert s.tool_use_blocks == 1

    def test_tool_result_blocks(self, session_path: Path) -> None:
        s = read_raw_session(session_path)
        assert s.tool_result_blocks == 1

    def test_thinking_blocks(self, session_path: Path) -> None:
        s = read_raw_session(session_path)
        assert s.thinking_blocks_total == 1
        assert s.thinking_blocks_with_content == 1

    def test_usage_entries(self, session_path: Path) -> None:
        s = read_raw_session(session_path)
        assert s.usage_entries == 2

    def test_timestamps(self, session_path: Path) -> None:
        s = read_raw_session(session_path)
        # Lines 1, 3, 4 have timestamps
        assert s.timestamps == 3

    def test_models_seen(self, session_path: Path) -> None:
        s = read_raw_session(session_path)
        assert s.models_seen == ["claude-opus-4-6-20250327"]

    def test_content_chars(self, session_path: Path) -> None:
        s = read_raw_session(session_path)
        # "hello world" (11) + "file contents here" (18) + "I read the file for you" (23)
        assert s.total_content_chars == 11 + 18 + 23

    def test_no_corrupted_lines(self, session_path: Path) -> None:
        s = read_raw_session(session_path)
        assert s.corrupted_lines == 0

    def test_no_subagent_calls(self, session_path: Path) -> None:
        s = read_raw_session(session_path)
        assert s.subagent_tool_calls == 0


class TestThinkingBlocks:
    """Test thinking blocks with content vs empty (encrypted)."""

    def test_encrypted_thinking_not_counted_as_with_content(self, tmp_path: Path) -> None:
        lines = [
            {
                "type": "assistant",
                "sessionId": "abc",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "real reasoning here"},
                        {"type": "thinking", "thinking": ""},
                        {"type": "thinking", "thinking": "   "},
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
        ]
        path = _write_jsonl(lines, tmp_path)
        s = read_raw_session(path)
        assert s.thinking_blocks_total == 3
        assert s.thinking_blocks_with_content == 1

    def test_thinking_without_field(self, tmp_path: Path) -> None:
        """A thinking block that lacks the 'thinking' key entirely."""
        lines = [
            {
                "type": "assistant",
                "sessionId": "abc",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking"},
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
        ]
        path = _write_jsonl(lines, tmp_path)
        s = read_raw_session(path)
        assert s.thinking_blocks_total == 1
        assert s.thinking_blocks_with_content == 0


class TestSubagentDetection:
    """Test that Agent/Task tool calls are counted as subagent calls."""

    def test_agent_and_task_tool_calls(self, tmp_path: Path) -> None:
        lines = [
            {
                "type": "assistant",
                "sessionId": "abc",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tc_001",
                            "name": "Agent",
                            "input": {"description": "explore codebase"},
                        },
                        {
                            "type": "tool_use",
                            "id": "tc_002",
                            "name": "Task",
                            "input": {"description": "run tests"},
                        },
                        {
                            "type": "tool_use",
                            "id": "tc_003",
                            "name": "Read",
                            "input": {"file_path": "/foo"},
                        },
                    ],
                    "usage": {"input_tokens": 50, "output_tokens": 20},
                },
            },
        ]
        path = _write_jsonl(lines, tmp_path)
        s = read_raw_session(path)
        assert s.tool_use_blocks == 3
        assert s.subagent_tool_calls == 2


class TestCorruptedLines:
    """Test that corrupted lines are tolerated and counted."""

    def test_corrupted_lines_skipped(self, tmp_path: Path) -> None:
        lines: list[dict | str] = [
            {
                "type": "user",
                "sessionId": "abc",
                "timestamp": "2026-03-28T10:00:00Z",
                "message": {"role": "user", "content": "hello"},
            },
            "this is not valid json {{{",
            '{"incomplete": true',
            {
                "type": "assistant",
                "sessionId": "abc",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "hi"}],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
        ]
        path = _write_jsonl(lines, tmp_path)
        s = read_raw_session(path)
        assert s.total_lines == 4
        assert s.corrupted_lines == 2
        assert s.user_messages == 1
        assert s.assistant_messages == 1

    def test_non_dict_json_counted_as_corrupted(self, tmp_path: Path) -> None:
        """A valid JSON line that is not a dict should be counted as corrupted."""
        lines: list[dict | str] = [
            '"just a string"',
            "[1, 2, 3]",
            {
                "type": "user",
                "sessionId": "abc",
                "message": {"role": "user", "content": "hello"},
            },
        ]
        path = _write_jsonl(lines, tmp_path)
        s = read_raw_session(path)
        assert s.total_lines == 3
        assert s.corrupted_lines == 2
        assert s.user_messages == 1


class TestEmptyFile:
    """Test that an empty file returns zero counts."""

    def test_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        s = read_raw_session(path)
        assert s.total_lines == 0
        assert s.corrupted_lines == 0
        assert s.user_messages == 0
        assert s.assistant_messages == 0
        assert s.tool_use_blocks == 0
        assert s.models_seen == []

    def test_blank_lines_only(self, tmp_path: Path) -> None:
        path = tmp_path / "blank.jsonl"
        path.write_text("\n\n\n")
        s = read_raw_session(path)
        assert s.total_lines == 3
        assert s.corrupted_lines == 0
        assert s.user_messages == 0


class TestQueueOperation:
    """Test queue-operation lines for model and system prompt extraction."""

    def test_queue_operation_extracts_model(self, tmp_path: Path) -> None:
        lines = [
            {
                "type": "queue-operation",
                "content": json.dumps({
                    "model": "claude-sonnet-4-20250514",
                    "tools": [{"name": "Read"}],
                }),
            },
            {
                "type": "assistant",
                "sessionId": "abc",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "hi"}],
                    "model": "claude-opus-4-6-20250327",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
        ]
        path = _write_jsonl(lines, tmp_path)
        s = read_raw_session(path)
        assert "claude-sonnet-4-20250514" in s.models_seen
        assert "claude-opus-4-6-20250327" in s.models_seen

    def test_queue_operation_counts_system_prompt(self, tmp_path: Path) -> None:
        lines = [
            {
                "type": "queue-operation",
                "content": json.dumps({
                    "model": "claude-opus-4-6-20250327",
                    "system": "You are a helpful assistant.",
                }),
            },
            {
                "type": "queue-operation",
                "content": json.dumps({
                    "model": "claude-opus-4-6-20250327",
                    "system": "You are a helpful assistant.",
                }),
            },
        ]
        path = _write_jsonl(lines, tmp_path)
        s = read_raw_session(path)
        assert s.system_prompt_count == 2


class TestToolResultContent:
    """Test content char counting from tool result blocks."""

    def test_tool_result_with_list_content(self, tmp_path: Path) -> None:
        lines = [
            {
                "type": "user",
                "sessionId": "abc",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tc_001",
                            "content": [
                                {"type": "text", "text": "line one"},
                                {"type": "text", "text": "line two"},
                            ],
                        },
                    ],
                },
            },
        ]
        path = _write_jsonl(lines, tmp_path)
        s = read_raw_session(path)
        # "line one" (8) + "line two" (8) = 16
        assert s.total_content_chars == 16
        assert s.tool_result_blocks == 1


class TestUsageEntries:
    """Test that usage entries require non-zero tokens."""

    def test_zero_tokens_not_counted(self, tmp_path: Path) -> None:
        lines = [
            {
                "type": "assistant",
                "sessionId": "abc",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "hi"}],
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        ]
        path = _write_jsonl(lines, tmp_path)
        s = read_raw_session(path)
        assert s.usage_entries == 0

    def test_nonzero_tokens_counted(self, tmp_path: Path) -> None:
        lines = [
            {
                "type": "assistant",
                "sessionId": "abc",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "hi"}],
                    "usage": {"input_tokens": 0, "output_tokens": 1},
                },
            },
        ]
        path = _write_jsonl(lines, tmp_path)
        s = read_raw_session(path)
        assert s.usage_entries == 1


class TestNonexistentFile:
    """Test graceful handling of missing files."""

    def test_missing_file_returns_empty_summary(self) -> None:
        s = read_raw_session(Path("/nonexistent/path/session.jsonl"))
        assert s.total_lines == 0
        assert s.corrupted_lines == 0

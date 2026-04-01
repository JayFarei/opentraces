"""Tests for the Hermes Agent parser (ShareGPT + XML tool calls)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from opentraces_schema.models import (
    Metrics,
    Observation,
    Outcome,
    Step,
    TokenUsage,
    ToolCall,
    TraceRecord,
)

from opentraces.parsers.hermes import HermesParser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hermes_row(
    conversations: list[dict] | None = None,
    **overrides,
) -> dict:
    """Build a dict matching the carnice dataset schema."""
    row = {
        "conversations": conversations or _make_simple_conversation(),
        "metadata": {"model": "z-ai/glm-5", "timestamp": "2026-03-15T10:00:00Z"},
        "source_row": {"original_prompt": "Write a hello world program", "task_source": "user"},
        "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "estimated_cost_usd": 0.005},
        "completed": True,
        "partial": False,
    }
    row.update(overrides)
    return row


def _make_simple_conversation() -> list[dict]:
    """Minimal valid Hermes conversation: system + user + assistant."""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Write a hello world program."},
        {"role": "assistant", "content": "Here is a hello world program:\n```python\nprint('hello world')\n```"},
    ]


def _make_tool_conversation() -> list[dict]:
    """Conversation with tool calls and responses."""
    return [
        {"role": "system", "content": "You are a coding assistant."},
        {"role": "user", "content": "Create a file called hello.py"},
        {
            "role": "assistant",
            "content": (
                '<think>I need to write a file.</think>'
                'I will create the file.\n'
                '<tool_call>{"name": "write_file", "arguments": {"path": "hello.py", "content": "print(\'hello\')"}, "tool_call_id": "tc_0"}</tool_call>'
            ),
        },
        {
            "role": "tool",
            "content": '<tool_response>{"tool_call_id": "tc_0", "name": "write_file", "content": "File written successfully"}</tool_response>',
        },
        {"role": "assistant", "content": "Done! I created hello.py."},
    ]


def _make_source_info() -> dict:
    return {
        "dataset_id": "test-org/test-dataset",
        "revision": "abc123def456",
        "subset": "high_quality",
        "split": "train",
    }


# ---------------------------------------------------------------------------
# map_record tests
# ---------------------------------------------------------------------------

class TestMapRecord:
    def test_valid_row(self):
        parser = HermesParser()
        row = _make_hermes_row()
        record = parser.map_record(row, 0, _make_source_info())

        assert record is not None
        assert isinstance(record, TraceRecord)
        assert record.agent.name == "hermes-agent"
        assert record.agent.model == "z-ai/glm-5"
        assert record.task.description == "Write a hello world program"
        assert record.task.source == "user"
        assert len(record.steps) >= 2  # user + assistant
        assert record.tool_definitions == []

    def test_malformed_row(self):
        parser = HermesParser()
        record = parser.map_record({"no_conversations": True}, 0)
        assert record is None

    def test_empty_conversations(self):
        parser = HermesParser()
        record = parser.map_record({"conversations": []}, 0)
        assert record is None

    def test_system_only(self):
        parser = HermesParser()
        row = _make_hermes_row(conversations=[
            {"role": "system", "content": "System prompt only"},
        ])
        record = parser.map_record(row, 0)
        assert record is None  # no user/assistant steps

    def test_session_id_stable(self):
        parser = HermesParser()
        info = _make_source_info()
        r1 = parser.map_record(_make_hermes_row(), 5, info)
        r2 = parser.map_record(_make_hermes_row(), 5, info)
        assert r1.session_id == r2.session_id

    def test_session_id_includes_revision(self):
        parser = HermesParser()
        info1 = _make_source_info()
        info2 = {**_make_source_info(), "revision": "zzz999aaa111"}
        r1 = parser.map_record(_make_hermes_row(), 0, info1)
        r2 = parser.map_record(_make_hermes_row(), 0, info2)
        assert r1.session_id != r2.session_id

    def test_step_fidelity_marked(self):
        parser = HermesParser()
        record = parser.map_record(_make_hermes_row(), 0, _make_source_info())
        assert record.metadata["step_fidelity"] == "conversation_turn"
        assert "step_fidelity_note" in record.metadata

    def test_outcome_inferred_from_completed(self):
        """P1: completed=True -> outcome.success=True with inferred confidence."""
        parser = HermesParser()
        record = parser.map_record(_make_hermes_row(), 0)
        assert record.outcome.success is True
        assert record.outcome.signal_confidence == "inferred"
        assert record.outcome.signal_source == "source_metadata"

    def test_outcome_none_when_partial(self):
        """Ambiguous partial traces get success=None."""
        parser = HermesParser()
        row = _make_hermes_row(completed=True, partial=True)
        record = parser.map_record(row, 0)
        assert record.outcome.success is None

    def test_outcome_false_when_not_completed(self):
        parser = HermesParser()
        row = _make_hermes_row(completed=False)
        record = parser.map_record(row, 0)
        assert record.outcome.success is False
        assert record.outcome.signal_confidence == "inferred"

    def test_metrics_from_source(self):
        parser = HermesParser()
        record = parser.map_record(_make_hermes_row(), 0)
        assert record.metrics.total_input_tokens == 1000
        assert record.metrics.total_output_tokens == 500
        assert record.metrics.estimated_cost_usd == 0.005

    def test_provenance_no_raw_data(self):
        parser = HermesParser()
        record = parser.map_record(_make_hermes_row(), 0, _make_source_info())
        assert "source_row" not in record.metadata
        assert "source_quality_metadata" not in record.metadata
        assert record.metadata["source_dataset"] == "test-org/test-dataset"
        assert record.metadata["source_dataset_revision"] == "abc123def456"

    def test_per_step_model_propagated(self):
        """P2: Session model propagated to every agent step."""
        parser = HermesParser()
        record = parser.map_record(_make_hermes_row(), 0)
        agent_steps = [s for s in record.steps if s.role == "agent"]
        assert all(s.model == "z-ai/glm-5" for s in agent_steps)
        # User steps should NOT have model
        user_steps = [s for s in record.steps if s.role == "user"]
        assert all(s.model is None for s in user_steps)


# ---------------------------------------------------------------------------
# XML parsing tests
# ---------------------------------------------------------------------------

class TestParseThinking:
    def test_extraction(self):
        text = "Before <think>My reasoning here</think> After"
        cleaned, thinking = HermesParser.parse_thinking(text)
        assert thinking == "My reasoning here"
        assert "<think>" not in cleaned
        assert "Before" in cleaned
        assert "After" in cleaned

    def test_absent(self):
        text = "No thinking tags here."
        cleaned, thinking = HermesParser.parse_thinking(text)
        assert thinking is None
        assert cleaned == text


class TestParseToolCalls:
    def test_valid_single(self):
        text = 'Hello <tool_call>{"name": "terminal", "arguments": {"command": "ls"}, "tool_call_id": "tc_1"}</tool_call> world'
        cleaned, calls = HermesParser.parse_tool_calls(text, 0)
        assert len(calls) == 1
        assert calls[0].tool_name == "terminal"
        assert calls[0].input == {"command": "ls"}
        assert calls[0].tool_call_id == "tc_1"
        assert "<tool_call>" not in cleaned

    def test_valid_multiple(self):
        text = (
            '<tool_call>{"name": "read_file", "arguments": {"path": "a.py"}}</tool_call>'
            '<tool_call>{"name": "write_file", "arguments": {"path": "b.py", "content": "x"}}</tool_call>'
        )
        _, calls = HermesParser.parse_tool_calls(text, 0)
        assert len(calls) == 2

    def test_malformed_json(self):
        text = '<tool_call>not valid json</tool_call>'
        _, calls = HermesParser.parse_tool_calls(text, 0)
        assert len(calls) == 0  # skipped

    def test_deterministic_id(self):
        text = '<tool_call>{"name": "terminal", "arguments": {}}</tool_call>'
        _, calls = HermesParser.parse_tool_calls(text, 3)
        assert calls[0].tool_call_id == "tc_3_0"  # step_3, block_0

    def test_nested_in_think(self):
        """Tool call tag inside think block should still be extracted."""
        text = '<think>Planning...</think><tool_call>{"name": "terminal", "arguments": {"command": "echo hi"}}</tool_call>'
        cleaned_text, thinking = HermesParser.parse_thinking(text)
        _, calls = HermesParser.parse_tool_calls(cleaned_text, 0)
        # After think removal, tool_call should still be findable
        assert len(calls) == 1


class TestParseToolResponses:
    def test_linked(self):
        text = '<tool_response>{"tool_call_id": "tc_1", "name": "terminal", "content": "file1.py file2.py"}</tool_response>'
        obs = HermesParser.parse_tool_responses(text)
        assert len(obs) == 1
        assert obs[0].source_call_id == "tc_1"
        assert obs[0].content == "file1.py file2.py"
        assert obs[0].error is None

    def test_unlinked(self):
        text = '<tool_response>{"content": "some output"}</tool_response>'
        obs = HermesParser.parse_tool_responses(text)
        assert len(obs) == 1
        assert obs[0].source_call_id == "unknown"
        assert obs[0].error == "unlinked_response"

    def test_malformed_json(self):
        text = '<tool_response>not json here</tool_response>'
        obs = HermesParser.parse_tool_responses(text)
        assert len(obs) == 1
        assert obs[0].error == "unlinked_response"


# ---------------------------------------------------------------------------
# Conversation -> Steps tests
# ---------------------------------------------------------------------------

class TestConversationsToSteps:
    def test_full_conversation(self):
        parser = HermesParser()
        convos = _make_simple_conversation()
        steps, sys_prompts, failures = parser._conversations_to_steps(convos)

        assert len(steps) == 2  # user + assistant (system is a prompt, not a step)
        assert len(sys_prompts) == 1
        assert steps[0].role == "user"
        assert steps[1].role == "agent"
        assert failures == 0

    def test_tool_response_folding(self):
        """FIX-4: Tool responses fold onto the preceding assistant step."""
        parser = HermesParser()
        convos = _make_tool_conversation()
        steps, _, _ = parser._conversations_to_steps(convos)

        # Should have: user, agent (with tool calls + obs), agent (final response)
        agent_steps = [s for s in steps if s.role == "agent"]
        assert len(agent_steps) == 2

        # First agent step should have the tool call AND the observation
        first_agent = agent_steps[0]
        assert len(first_agent.tool_calls) == 1
        assert first_agent.tool_calls[0].tool_name == "Write"  # normalized
        assert len(first_agent.observations) == 1
        assert first_agent.observations[0].source_call_id == "tc_0"

    def test_thinking_extracted(self):
        parser = HermesParser()
        convos = _make_tool_conversation()
        steps, _, _ = parser._conversations_to_steps(convos)

        agent_steps = [s for s in steps if s.role == "agent"]
        first_agent = agent_steps[0]
        assert first_agent.reasoning_content == "I need to write a file."


# ---------------------------------------------------------------------------
# Tool normalization tests
# ---------------------------------------------------------------------------

class TestNormalization:
    def test_known_tools(self):
        parser = HermesParser()
        canonical, args, original = parser._normalize_tool_call(
            "terminal", {"command": "ls"},
        )
        assert canonical == "Bash"
        assert original == "terminal"
        assert args == {"command": "ls"}

    def test_write_file_mapping(self):
        parser = HermesParser()
        canonical, args, original = parser._normalize_tool_call(
            "write_file", {"path": "test.py", "content": "hello"},
        )
        assert canonical == "Write"
        assert args == {"file_path": "test.py", "content": "hello"}

    def test_patch_arg_mapping(self):
        parser = HermesParser()
        canonical, args, _ = parser._normalize_tool_call(
            "patch", {"path": "f.py", "original": "old", "replacement": "new"},
        )
        assert canonical == "Edit"
        assert args == {"file_path": "f.py", "old_string": "old", "new_string": "new"}

    def test_unknown_tool_passthrough(self):
        parser = HermesParser()
        canonical, args, original = parser._normalize_tool_call(
            "custom_tool", {"x": 1},
        )
        assert canonical == "custom_tool"
        assert original == "custom_tool"
        assert args == {"x": 1}


# ---------------------------------------------------------------------------
# Import from JSONL file test
# ---------------------------------------------------------------------------

class TestImportTraces:
    def test_from_jsonl(self, tmp_path: Path):
        parser = HermesParser()
        jsonl_file = tmp_path / "test.jsonl"
        rows = [_make_hermes_row() for _ in range(3)]
        jsonl_file.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

        records = parser.import_traces(jsonl_file)
        assert len(records) == 3
        assert all(isinstance(r, TraceRecord) for r in records)

    def test_limit(self, tmp_path: Path):
        parser = HermesParser()
        jsonl_file = tmp_path / "test.jsonl"
        rows = [_make_hermes_row() for _ in range(10)]
        jsonl_file.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

        records = parser.import_traces(jsonl_file, max_records=3)
        assert len(records) == 3


# ---------------------------------------------------------------------------
# Pipeline integration tests
# ---------------------------------------------------------------------------

class TestPipelineIntegration:
    def test_process_imported_trace(self):
        """Integration: parse -> enrich -> security pipeline."""
        from opentraces.config import Config
        from opentraces.pipeline import process_imported_trace

        parser = HermesParser()
        row = _make_hermes_row(conversations=_make_tool_conversation())
        record = parser.map_record(row, 0, _make_source_info())
        assert record is not None

        cfg = Config()
        result = process_imported_trace(record, cfg)

        assert result.record.security.scanned is True
        assert result.record.metrics.total_steps > 0
        # Source metrics should be preserved (FIX-3)
        assert result.record.metrics.total_input_tokens == 1000

    def test_enrich_from_steps_runs(self):
        """Shared enrichment populates language_ecosystem and dependencies."""
        from opentraces.pipeline import _enrich_from_steps

        parser = HermesParser()
        row = _make_hermes_row(conversations=_make_tool_conversation())
        record = parser.map_record(row, 0, _make_source_info())
        assert record is not None

        _enrich_from_steps(record)
        # Attribution should be populated from the Write tool call
        assert record.attribution is not None
        assert len(record.attribution.files) > 0


# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------

class TestRegressions:
    def test_git_signals_case_insensitive(self):
        """The git_signals fix should accept both 'Bash' and 'bash'."""
        from opentraces.enrichment.git_signals import detect_commits_from_steps

        steps = [
            Step(
                step_index=0,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc_1",
                        tool_name="bash",  # lowercase
                        input={"command": "git commit -m 'test'"},
                    ),
                ],
                observations=[
                    Observation(
                        source_call_id="tc_1",
                        content="[main abc1234] test\n 1 file changed",
                    ),
                ],
            ),
        ]
        outcome = detect_commits_from_steps(steps)
        assert outcome.committed is True
        assert outcome.commit_sha == "abc1234"

    def test_importers_registry(self):
        """HermesParser is registered in the IMPORTERS registry."""
        from opentraces.parsers import get_importers
        importers = get_importers()
        assert "hermes" in importers
        instance = importers["hermes"]()
        assert instance.format_name == "hermes"

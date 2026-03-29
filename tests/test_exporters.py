"""Tests for the exporter architecture and ATIF v1.6 exporter."""

from __future__ import annotations

import json
import uuid

import pytest

from opentraces_schema import (
    Agent,
    Metrics,
    Observation,
    Step,
    TokenUsage,
    ToolCall,
    TraceRecord,
)
from opentraces_schema.version import SCHEMA_VERSION


def _make_minimal_record(**overrides) -> TraceRecord:
    """Create a minimal valid TraceRecord for export tests."""
    defaults = {
        "trace_id": str(uuid.uuid4()),
        "session_id": "test-session-001",
        "agent": Agent(name="claude-code", version="1.0.0", model="anthropic/claude-sonnet-4-20250514"),
        "steps": [
            Step(
                step_index=0,
                role="user",
                content="Fix the bug in auth.py",
            ),
            Step(
                step_index=1,
                role="agent",
                content="I'll look at auth.py.",
                reasoning_content="The user wants me to fix auth.py",
                model="anthropic/claude-sonnet-4-20250514",
                timestamp="2026-03-28T10:00:00Z",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc_001",
                        tool_name="Read",
                        input={"file_path": "/src/auth.py"},
                    ),
                ],
                observations=[
                    Observation(
                        source_call_id="tc_001",
                        content="def login():\n    pass",
                    ),
                ],
                token_usage=TokenUsage(
                    input_tokens=1000,
                    output_tokens=200,
                    cache_read_tokens=500,
                    cache_write_tokens=100,
                    prefix_reuse_tokens=400,
                ),
            ),
        ],
        "tool_definitions": [{"name": "Read", "description": "Read a file"}],
    }
    defaults.update(overrides)
    return TraceRecord(**defaults)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestProtocolConformance:

    def test_atif_exporter_satisfies_protocol(self):
        from opentraces.exporters.base import FormatExporter
        from opentraces.exporters.atif import ATIFExporter

        assert isinstance(ATIFExporter(), FormatExporter)


# ---------------------------------------------------------------------------
# ATIF Exporter
# ---------------------------------------------------------------------------

class TestATIFExporter:

    def test_produces_valid_jsonl(self):
        from opentraces.exporters.atif import ATIFExporter

        record = _make_minimal_record()
        exporter = ATIFExporter()
        lines = list(exporter.export_traces([record]))

        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["schema_version"] == "ATIF-v1.6"
        assert parsed["session_id"] == "test-session-001"
        assert "agent" in parsed
        assert "steps" in parsed

    def test_agent_mapping(self):
        from opentraces.exporters.atif import ATIFExporter

        record = _make_minimal_record()
        exporter = ATIFExporter()
        lines = list(exporter.export_traces([record]))
        parsed = json.loads(lines[0])

        assert parsed["agent"]["name"] == "claude-code"
        assert parsed["agent"]["version"] == "1.0.0"
        assert parsed["agent"]["model_name"] == "anthropic/claude-sonnet-4-20250514"
        assert len(parsed["agent"]["tool_definitions"]) == 1

    def test_step_renumbering_starts_at_1(self):
        """ATIF step_id is 1-indexed regardless of source step_index."""
        from opentraces.exporters.atif import ATIFExporter

        record = _make_minimal_record()
        exporter = ATIFExporter()
        lines = list(exporter.export_traces([record]))
        parsed = json.loads(lines[0])

        assert parsed["steps"][0]["step_id"] == 1
        assert parsed["steps"][1]["step_id"] == 2

    def test_tool_call_mapping(self):
        """tool_name -> function_name, input -> arguments."""
        from opentraces.exporters.atif import ATIFExporter

        record = _make_minimal_record()
        exporter = ATIFExporter()
        lines = list(exporter.export_traces([record]))
        parsed = json.loads(lines[0])

        agent_step = parsed["steps"][1]
        assert "tool_calls" in agent_step
        tc = agent_step["tool_calls"][0]
        assert tc["tool_call_id"] == "tc_001"
        assert tc["function_name"] == "Read"
        assert tc["arguments"] == {"file_path": "/src/auth.py"}

    def test_observation_singular_wrapper(self):
        """observations[] -> observation.results[] (singular wrapper)."""
        from opentraces.exporters.atif import ATIFExporter

        record = _make_minimal_record()
        exporter = ATIFExporter()
        lines = list(exporter.export_traces([record]))
        parsed = json.loads(lines[0])

        agent_step = parsed["steps"][1]
        assert "observation" in agent_step
        assert "results" in agent_step["observation"]
        result = agent_step["observation"]["results"][0]
        assert result["source_call_id"] == "tc_001"
        assert "login" in result["content"]

    def test_zero_observations_omits_field(self):
        """Step with no observations should not have observation key."""
        from opentraces.exporters.atif import ATIFExporter

        record = _make_minimal_record()
        # User step has no observations
        exporter = ATIFExporter()
        lines = list(exporter.export_traces([record]))
        parsed = json.loads(lines[0])

        user_step = parsed["steps"][0]
        assert "observation" not in user_step

    def test_content_none_step(self):
        """Step with content=None (pure tool call) should omit message."""
        from opentraces.exporters.atif import ATIFExporter

        record = _make_minimal_record(
            steps=[
                Step(
                    step_index=0,
                    role="agent",
                    content=None,
                    tool_calls=[
                        ToolCall(tool_call_id="tc_x", tool_name="Bash", input={"cmd": "ls"}),
                    ],
                    observations=[
                        Observation(source_call_id="tc_x", content="file.py"),
                    ],
                ),
            ]
        )
        exporter = ATIFExporter()
        lines = list(exporter.export_traces([record]))
        parsed = json.loads(lines[0])

        step = parsed["steps"][0]
        assert "message" not in step
        assert "tool_calls" in step

    def test_dangling_tool_call_exported(self):
        """Observation with error='no_result' should export as error content."""
        from opentraces.exporters.atif import ATIFExporter

        record = _make_minimal_record(
            steps=[
                Step(
                    step_index=0,
                    role="agent",
                    content="Trying something",
                    tool_calls=[
                        ToolCall(tool_call_id="tc_dangle", tool_name="Read", input={}),
                    ],
                    observations=[
                        Observation(source_call_id="tc_dangle", error="no_result"),
                    ],
                ),
            ]
        )
        exporter = ATIFExporter()
        lines = list(exporter.export_traces([record]))
        parsed = json.loads(lines[0])

        result = parsed["steps"][0]["observation"]["results"][0]
        assert "no_result" in result["content"]

    def test_token_usage_mapping(self):
        """Token usage maps to ATIF metrics (partial: drops prefix_reuse)."""
        from opentraces.exporters.atif import ATIFExporter

        record = _make_minimal_record()
        exporter = ATIFExporter()
        lines = list(exporter.export_traces([record]))
        parsed = json.loads(lines[0])

        metrics = parsed["steps"][1]["metrics"]
        assert metrics["prompt_tokens"] == 1000
        assert metrics["completion_tokens"] == 200
        assert metrics["cached_tokens"] == 500
        # prefix_reuse_tokens and cache_write_tokens are dropped
        assert "prefix_reuse_tokens" not in metrics

    def test_reasoning_content_preserved(self):
        from opentraces.exporters.atif import ATIFExporter

        record = _make_minimal_record()
        exporter = ATIFExporter()
        lines = list(exporter.export_traces([record]))
        parsed = json.loads(lines[0])

        assert parsed["steps"][1]["reasoning_content"] == "The user wants me to fix auth.py"

    def test_field_coverage_categories(self):
        from opentraces.exporters.atif import ATIFExporter

        exporter = ATIFExporter()
        coverage = exporter.field_coverage()

        assert coverage["steps"] == "full"
        assert coverage["token_usage"] == "partial"
        assert coverage["attribution"] == "dropped"
        assert coverage["security"] == "dropped"

    def test_empty_input_produces_no_output(self):
        from opentraces.exporters.atif import ATIFExporter

        exporter = ATIFExporter()
        lines = list(exporter.export_traces([]))
        assert lines == []

    def test_multiple_records(self):
        from opentraces.exporters.atif import ATIFExporter

        records = [_make_minimal_record() for _ in range(3)]
        exporter = ATIFExporter()
        lines = list(exporter.export_traces(records))
        assert len(lines) == 3

        for line in lines:
            parsed = json.loads(line)
            assert parsed["schema_version"] == "ATIF-v1.6"

    def test_round_trip_realistic_record(self):
        """A realistic record with all fields populated survives export."""
        from opentraces.exporters.atif import ATIFExporter

        record = _make_minimal_record(
            timestamp_start="2026-03-28T10:00:00Z",
            timestamp_end="2026-03-28T10:05:00Z",
            tool_definitions=[
                {"name": "Read", "description": "Read files"},
                {"name": "Edit", "description": "Edit files"},
                {"name": "Bash", "description": "Run commands"},
            ],
            steps=[
                Step(step_index=0, role="system", content="You are a helpful assistant."),
                Step(step_index=1, role="user", content="Fix the login bug"),
                Step(
                    step_index=2, role="agent", content="Looking at auth.py",
                    reasoning_content="Need to check the login function",
                    model="anthropic/claude-sonnet-4-20250514",
                    timestamp="2026-03-28T10:00:01Z",
                    tool_calls=[
                        ToolCall(tool_call_id="tc1", tool_name="Read", input={"file_path": "auth.py"}),
                        ToolCall(tool_call_id="tc2", tool_name="Read", input={"file_path": "tests/test_auth.py"}),
                    ],
                    observations=[
                        Observation(source_call_id="tc1", content="def login(): ..."),
                        Observation(source_call_id="tc2", content="def test_login(): ..."),
                    ],
                    token_usage=TokenUsage(input_tokens=5000, output_tokens=500, cache_read_tokens=3000),
                ),
                Step(
                    step_index=3, role="agent", content="I'll fix the token check",
                    tool_calls=[
                        ToolCall(tool_call_id="tc3", tool_name="Edit", input={"file_path": "auth.py", "old": "pass", "new": "return True"}),
                    ],
                    observations=[
                        Observation(source_call_id="tc3", content="File edited successfully"),
                    ],
                    token_usage=TokenUsage(input_tokens=6000, output_tokens=300),
                ),
            ],
        )

        exporter = ATIFExporter()
        lines = list(exporter.export_traces([record]))
        assert len(lines) == 1

        parsed = json.loads(lines[0])
        assert parsed["schema_version"] == "ATIF-v1.6"
        assert len(parsed["steps"]) == 4
        assert parsed["steps"][0]["step_id"] == 1
        assert parsed["steps"][3]["step_id"] == 4
        assert len(parsed["agent"]["tool_definitions"]) == 3

        # Check multi-observation step
        step3 = parsed["steps"][2]
        assert len(step3["tool_calls"]) == 2
        assert len(step3["observation"]["results"]) == 2
        assert step3["metrics"]["prompt_tokens"] == 5000


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

class TestRegistries:

    def test_parsers_registry_has_claude_code(self):
        from opentraces.parsers import get_parsers
        parsers = get_parsers()
        assert "claude-code" in parsers

    def test_exporters_registry_has_atif(self):
        from opentraces.exporters import get_exporters
        exporters = get_exporters()
        assert "atif" in exporters

    def test_import_alias_resolution(self):
        from opentraces.parsers import resolve_import_format
        assert resolve_import_format("nonexistent") is None

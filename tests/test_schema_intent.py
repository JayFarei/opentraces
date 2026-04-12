"""Tests for the Intent session-level block (schema 0.3.0, plan 038 phase 1)."""

from __future__ import annotations

import json

import pytest

from opentraces_schema import (
    SCHEMA_VERSION,
    Agent,
    Intent,
    TraceRecord,
)


def _base_trace(**overrides) -> TraceRecord:
    defaults = dict(
        trace_id="trace-xyz",
        session_id="sess-1",
        agent=Agent(name="claude-code"),
    )
    defaults.update(overrides)
    return TraceRecord(**defaults)


class TestIntentModel:
    def test_all_fields_optional(self):
        intent = Intent()
        assert intent.title is None
        assert intent.summary is None
        assert intent.source is None
        assert intent.model is None

    def test_source_is_closed_enum(self):
        for valid in ("llm_hook", "post_processor", "user"):
            Intent(source=valid)
        with pytest.raises(Exception):
            Intent(source="llm-hook")  # hyphen not allowed
        with pytest.raises(Exception):
            Intent(source="made_up")


class TestTraceRecordIntent:
    def test_intent_absent_by_default(self):
        record = _base_trace()
        assert record.intent is None
        as_json = json.loads(record.model_dump_json())
        assert as_json["intent"] is None

    def test_intent_round_trip(self):
        populated = Intent(
            title="Refactor agent modules",
            summary="Moved parsers and hooks under agents/<name>/ and rewired imports.",
            source="llm_hook",
            model="anthropic/claude-haiku-4-5-20251001",
        )
        record = _base_trace(intent=populated)
        round_tripped = TraceRecord.model_validate_json(record.model_dump_json())
        assert round_tripped.intent == populated
        # Source enum preserved exactly
        assert round_tripped.intent.source == "llm_hook"


class TestBackwardCompatibility:
    def test_pre_intent_trace_loads_cleanly(self):
        """A trace written against 0.2.x (no intent field) must load with intent absent."""
        legacy_payload = {
            "schema_version": "0.2.0",
            "trace_id": "legacy-1",
            "session_id": "legacy-sess",
            "agent": {"name": "claude-code"},
        }
        record = TraceRecord.model_validate(legacy_payload)
        assert record.intent is None

    def test_unknown_field_inside_intent_is_ignored(self):
        """Forward-compat: extra fields on Intent are silently dropped (Pydantic default)."""
        payload = {
            "trace_id": "t",
            "session_id": "s",
            "agent": {"name": "claude-code"},
            "intent": {"title": "x", "future_field": "ignore_me"},
        }
        record = TraceRecord.model_validate(payload)
        assert record.intent is not None
        assert record.intent.title == "x"
        assert not hasattr(record.intent, "future_field")


class TestContentHashExcludesIntent:
    def test_adding_or_changing_intent_does_not_change_hash(self):
        record_without = _base_trace()
        record_with = _base_trace(
            intent=Intent(title="summary", source="llm_hook"),
        )
        record_with_other = _base_trace(
            intent=Intent(title="different summary", source="post_processor"),
        )
        assert record_without.compute_content_hash() == record_with.compute_content_hash()
        assert record_with.compute_content_hash() == record_with_other.compute_content_hash()


class TestJsonlShardRoundTrip:
    """Simulates the full upload shard path: to_jsonl_line -> file -> parse back.

    HF Hub's sharded-upload writes one line per trace via TraceRecord.to_jsonl_line
    (see src/opentraces/upload/hf_hub.py). This test mixes Intent.source values
    across a shard and asserts every line round-trips with provenance preserved.
    """

    def test_mixed_source_shard_round_trip(self, tmp_path):
        traces = [
            _base_trace(trace_id="t1", intent=None),
            _base_trace(trace_id="t2", intent=Intent(title="a", source="llm_hook", model="x/y")),
            _base_trace(trace_id="t3", intent=Intent(title="b", source="post_processor", model="ollama/llama3.2")),
            _base_trace(trace_id="t4", intent=Intent(title="c", summary="hand edited", source="user")),
        ]

        shard = tmp_path / "traces_00000.jsonl"
        with shard.open("w") as f:
            for t in traces:
                f.write(t.to_jsonl_line() + "\n")

        parsed: list[TraceRecord] = []
        with shard.open("r") as f:
            for line in f:
                parsed.append(TraceRecord.model_validate_json(line))

        assert len(parsed) == 4
        assert parsed[0].intent is None
        assert parsed[1].intent.source == "llm_hook"
        assert parsed[1].intent.model == "x/y"
        assert parsed[2].intent.source == "post_processor"
        assert parsed[3].intent.source == "user"
        assert parsed[3].intent.summary == "hand edited"
        # Every row validates against the current schema version.
        for original, got in zip(traces, parsed):
            assert got.schema_version == SCHEMA_VERSION
            # intent is excluded from the hash; trace identity is preserved.
            assert got.compute_content_hash() == original.compute_content_hash()


class TestSchemaVersionBump:
    def test_version_is_0_3_0(self):
        assert SCHEMA_VERSION == "0.3.0"

"""Tests for the preservation comparator (quality/preservation.py)."""

from __future__ import annotations

import pytest

from opentraces_schema.models import (
    Agent,
    Attribution,
    AttributionFile,
    Observation,
    Outcome,
    Step,
    TokenUsage,
    ToolCall,
    TraceRecord,
)
from opentraces.quality.preservation import (
    PreservationReport,
    compare_preservation,
)
from opentraces.quality.raw_reader import RawSessionSummary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(**kwargs) -> TraceRecord:
    """Create a minimal TraceRecord with overrides."""
    defaults = dict(
        trace_id="test-trace",
        session_id="test-session",
        agent=Agent(name="claude-code"),
        steps=[],
    )
    defaults.update(kwargs)
    return TraceRecord(**defaults)


def _make_step(
    index: int = 0,
    role: str = "agent",
    *,
    tool_calls: list[ToolCall] | None = None,
    observations: list[Observation] | None = None,
    reasoning_content: str | None = None,
    token_usage: TokenUsage | None = None,
    timestamp: str | None = None,
    call_type: str | None = None,
) -> Step:
    return Step(
        step_index=index,
        role=role,
        tool_calls=tool_calls or [],
        observations=observations or [],
        reasoning_content=reasoning_content,
        token_usage=token_usage or TokenUsage(),
        timestamp=timestamp,
        call_type=call_type,
    )


def _make_raw(**kwargs) -> RawSessionSummary:
    """Create a RawSessionSummary with overrides."""
    return RawSessionSummary(**kwargs)


# ---------------------------------------------------------------------------
# Tests: perfect preservation
# ---------------------------------------------------------------------------

class TestPerfectPreservation:
    """When parsed counts match raw counts, all ratios should be 1.0."""

    def test_all_categories_one(self):
        steps = [
            _make_step(
                0,
                "user",
                timestamp="2026-01-01T00:00:00Z",
            ),
            _make_step(
                1,
                "agent",
                tool_calls=[ToolCall(tool_call_id="tc1", tool_name="Read")],
                observations=[Observation(source_call_id="tc1", content="ok")],
                reasoning_content="I should read the file",
                token_usage=TokenUsage(input_tokens=100, output_tokens=50),
                timestamp="2026-01-01T00:00:01Z",
            ),
        ]

        record = _make_record(steps=steps)
        raw = _make_raw(
            user_messages=1,
            assistant_messages=1,
            tool_use_blocks=1,
            tool_result_blocks=1,
            thinking_blocks_total=1,
            thinking_blocks_with_content=1,
            usage_entries=1,
            timestamps=2,
        )

        report = compare_preservation(record, raw)

        assert report.ratios["messages"] == 1.0
        assert report.ratios["tool_calls"] == 1.0
        assert report.ratios["tool_results"] == 1.0
        assert report.ratios["thinking"] == 1.0
        assert report.ratios["token_usage"] == 1.0
        assert report.ratios["timestamps"] == 1.0
        assert report.ratios["subagents"] == 1.0
        assert report.overall == 1.0
        assert report.signal_losses == []
        assert report.impossible_signals == []


# ---------------------------------------------------------------------------
# Tests: signal loss
# ---------------------------------------------------------------------------

class TestSignalLoss:
    """When parsed has fewer signals than raw, ratios drop and losses appear."""

    def test_tool_results_loss(self):
        steps = [
            _make_step(
                0,
                "agent",
                tool_calls=[
                    ToolCall(tool_call_id="tc1", tool_name="Read"),
                    ToolCall(tool_call_id="tc2", tool_name="Grep"),
                ],
                observations=[
                    Observation(source_call_id="tc1", content="ok"),
                    # tc2 observation missing
                ],
            ),
        ]
        record = _make_record(steps=steps)
        raw = _make_raw(
            assistant_messages=1,
            tool_use_blocks=2,
            tool_result_blocks=3,
        )

        report = compare_preservation(record, raw)

        # tool_results: 1 observation vs 3 raw blocks
        assert report.ratios["tool_results"] < 1.0
        assert report.ratios["tool_results"] == pytest.approx(1 / 3, abs=0.01)

        loss_cats = [sl.category for sl in report.signal_losses]
        assert "tool_results" in loss_cats
        loss = next(sl for sl in report.signal_losses if sl.category == "tool_results")
        assert loss.raw_count == 3
        assert loss.parsed_count == 1

    def test_token_usage_loss(self):
        # 2 agent steps but only 1 with tokens
        steps = [
            _make_step(
                0,
                "agent",
                token_usage=TokenUsage(input_tokens=100, output_tokens=50),
            ),
            _make_step(1, "agent"),  # no tokens
        ]
        record = _make_record(steps=steps)
        raw = _make_raw(
            assistant_messages=2,
            usage_entries=3,
        )

        report = compare_preservation(record, raw)
        assert report.ratios["token_usage"] == pytest.approx(1 / 3, abs=0.01)

        loss_cats = [sl.category for sl in report.signal_losses]
        assert "token_usage" in loss_cats

    def test_timestamp_loss(self):
        steps = [
            _make_step(0, "agent", timestamp="2026-01-01T00:00:00Z"),
            _make_step(1, "agent"),  # no timestamp
        ]
        record = _make_record(steps=steps)
        raw = _make_raw(
            assistant_messages=2,
            timestamps=4,
        )

        report = compare_preservation(record, raw)
        assert report.ratios["timestamps"] == pytest.approx(1 / 4, abs=0.01)

        loss_cats = [sl.category for sl in report.signal_losses]
        assert "timestamps" in loss_cats


# ---------------------------------------------------------------------------
# Tests: thinking / encrypted thinking
# ---------------------------------------------------------------------------

class TestThinkingPreservation:
    """Special handling for encrypted thinking blocks."""

    def test_encrypted_thinking_gives_half(self):
        """When raw has thinking blocks but all are encrypted, score is 0.5."""
        record = _make_record(steps=[_make_step(0, "agent")])
        raw = _make_raw(
            assistant_messages=1,
            thinking_blocks_total=5,
            thinking_blocks_with_content=0,
        )

        report = compare_preservation(record, raw)
        assert report.ratios["thinking"] == 0.5

    def test_no_thinking_blocks_gives_one(self):
        """When raw has 0 thinking blocks, nothing to preserve, score is 1.0."""
        record = _make_record(steps=[_make_step(0, "agent")])
        raw = _make_raw(assistant_messages=1)

        report = compare_preservation(record, raw)
        assert report.ratios["thinking"] == 1.0

    def test_thinking_content_preserved(self):
        """When raw has thinking with content and parsed has reasoning, score is 1.0."""
        steps = [
            _make_step(0, "agent", reasoning_content="Let me think about this..."),
        ]
        record = _make_record(steps=steps)
        raw = _make_raw(
            assistant_messages=1,
            thinking_blocks_total=1,
            thinking_blocks_with_content=1,
        )

        report = compare_preservation(record, raw)
        assert report.ratios["thinking"] == 1.0

    def test_thinking_content_lost(self):
        """Raw has thinking content but parsed has none -> signal loss."""
        steps = [_make_step(0, "agent")]
        record = _make_record(steps=steps)
        raw = _make_raw(
            assistant_messages=1,
            thinking_blocks_total=3,
            thinking_blocks_with_content=2,
        )

        report = compare_preservation(record, raw)
        assert report.ratios["thinking"] == 0.0

        loss_cats = [sl.category for sl in report.signal_losses]
        assert "thinking" in loss_cats


# ---------------------------------------------------------------------------
# Tests: subagent preservation
# ---------------------------------------------------------------------------

class TestSubagentPreservation:
    def test_no_subagents_anywhere(self):
        """Neither raw nor parsed has subagents -> 1.0."""
        record = _make_record(steps=[_make_step(0, "agent")])
        raw = _make_raw(assistant_messages=1, subagent_tool_calls=0)

        report = compare_preservation(record, raw)
        assert report.ratios["subagents"] == 1.0

    def test_subagent_match(self):
        steps = [
            _make_step(0, "agent", call_type="subagent"),
            _make_step(1, "agent", call_type="subagent"),
        ]
        record = _make_record(steps=steps)
        raw = _make_raw(assistant_messages=2, subagent_tool_calls=2)

        report = compare_preservation(record, raw)
        assert report.ratios["subagents"] == 1.0

    def test_subagent_partial(self):
        steps = [_make_step(0, "agent", call_type="subagent")]
        record = _make_record(steps=steps)
        raw = _make_raw(assistant_messages=1, subagent_tool_calls=3)

        report = compare_preservation(record, raw)
        assert report.ratios["subagents"] == pytest.approx(1 / 3, abs=0.01)


# ---------------------------------------------------------------------------
# Tests: impossible signal detection
# ---------------------------------------------------------------------------

class TestImpossibleSignals:
    def test_committed_but_no_bash(self):
        record = _make_record(
            steps=[_make_step(0, "agent")],
            outcome=Outcome(committed=True),
        )
        raw = _make_raw(assistant_messages=1, tool_use_blocks=0)

        report = compare_preservation(record, raw)
        assert len(report.impossible_signals) >= 1
        assert any("committed" in s for s in report.impossible_signals)

    def test_committed_with_bash_no_flag(self):
        steps = [
            _make_step(
                0,
                "agent",
                tool_calls=[ToolCall(tool_call_id="tc1", tool_name="Bash")],
            ),
        ]
        record = _make_record(
            steps=steps,
            outcome=Outcome(committed=True),
        )
        raw = _make_raw(assistant_messages=1, tool_use_blocks=1)

        report = compare_preservation(record, raw)
        assert not any("committed" in s for s in report.impossible_signals)

    def test_attribution_but_no_edit_write(self):
        record = _make_record(
            steps=[_make_step(0, "agent")],
            attribution=Attribution(files=[AttributionFile(path="foo.py")]),
        )
        raw = _make_raw(assistant_messages=1, tool_use_blocks=0)

        report = compare_preservation(record, raw)
        assert len(report.impossible_signals) >= 1
        assert any("attribution" in s for s in report.impossible_signals)

    def test_no_impossible_when_clean(self):
        record = _make_record(steps=[_make_step(0, "agent")])
        raw = _make_raw(assistant_messages=1)

        report = compare_preservation(record, raw)
        assert report.impossible_signals == []


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_trace_and_empty_raw(self):
        record = _make_record(steps=[])
        raw = _make_raw()

        report = compare_preservation(record, raw)
        # All ratios should be 1.0 (nothing to preserve, 0/0 -> 1.0)
        for cat, ratio in report.ratios.items():
            assert ratio >= 0.5, f"{cat} ratio unexpectedly low: {ratio}"
        assert report.signal_losses == []
        assert report.impossible_signals == []

    def test_empty_raw_nonempty_trace(self):
        """Parsed has more than raw (sub-agent inlining can do this)."""
        steps = [
            _make_step(0, "user"),
            _make_step(1, "agent"),
            _make_step(2, "agent"),
        ]
        record = _make_record(steps=steps)
        raw = _make_raw()  # everything is 0

        report = compare_preservation(record, raw)
        # message ratio capped at 1.0
        assert report.ratios["messages"] == 1.0

    def test_overall_is_weighted(self):
        """Verify overall is a proper weighted average, not simple mean."""
        steps = [
            _make_step(
                0,
                "agent",
                tool_calls=[ToolCall(tool_call_id="tc1", tool_name="Read")],
                observations=[Observation(source_call_id="tc1", content="ok")],
                token_usage=TokenUsage(input_tokens=100, output_tokens=50),
                timestamp="2026-01-01T00:00:00Z",
            ),
        ]
        record = _make_record(steps=steps)
        raw = _make_raw(
            assistant_messages=1,
            tool_use_blocks=1,
            tool_result_blocks=1,
            usage_entries=1,
            timestamps=1,
        )

        report = compare_preservation(record, raw)
        # All categories should be 1.0 since raw=0 for thinking/subagents
        # means those also return 1.0
        assert report.overall == pytest.approx(1.0, abs=0.01)

    def test_report_dataclass_fields(self):
        """Verify PreservationReport has expected fields."""
        report = PreservationReport()
        assert isinstance(report.ratios, dict)
        assert isinstance(report.overall, float)
        assert isinstance(report.signal_losses, list)
        assert isinstance(report.impossible_signals, list)

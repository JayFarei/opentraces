"""Regression test for plan 046: session token totals must surface all four
token buckets and must not double-count mirrored subagent events.
"""

from pathlib import Path

from opentraces.capture.claude_code.parse import ClaudeCodeParser
from opentraces.core.pipeline import _merge_parser_and_step_metrics
from opentraces.enrichment.metrics import compute_metrics
from opentraces_schema import Metrics, Step, TokenUsage


def test_metrics_surfaces_all_four_token_buckets():
    """compute_metrics must populate input/output/cache_read/cache_creation."""
    steps = [
        Step(
            step_index=1,
            role="agent",
            token_usage=TokenUsage(
                input_tokens=100,
                output_tokens=200,
                cache_read_tokens=5000,
                cache_write_tokens=800,
            ),
            model="claude-opus-4-6",
        ),
    ]
    m = compute_metrics(steps)
    assert m.total_input_tokens == 100
    assert m.total_output_tokens == 200
    assert m.total_cache_read_tokens == 5000
    assert m.total_cache_creation_tokens == 800
    # cost must include all four buckets — non-zero proves the aggregator wired them in
    assert m.estimated_cost_usd is not None and m.estimated_cost_usd > 0


def _assistant_line(uuid: str, usage: dict, sid: str = "sess-1", rid: str = "req-1") -> dict:
    return {
        "type": "assistant",
        "sessionId": sid,
        "uuid": uuid,
        "requestId": rid,
        "timestamp": "2026-04-15T10:00:00Z",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "ok"}],
            "usage": usage,
        },
    }


def test_duplicate_assistant_request_counted_once(tmp_path: Path):
    """When the same (sessionId, uuid, requestId) event is mirrored into both
    the parent session JSONL and a subagent JSONL, it must contribute tokens
    exactly once. See lazyusage claude-parser.ts for prior-art pattern.
    """
    usage = {
        "input_tokens": 100,
        "output_tokens": 200,
        "cache_read_input_tokens": 5000,
        "cache_creation_input_tokens": 800,
    }
    lines = [
        {
            "type": "user",
            "sessionId": "sess-1",
            "timestamp": "2026-04-15T09:59:00Z",
            "message": {"role": "user", "content": "hi"},
        },
        _assistant_line("u-dup", usage),
        _assistant_line("u-dup", usage),  # exact duplicate — must be dropped
    ]
    parser = ClaudeCodeParser()
    tool_result_map = parser._build_tool_result_map(lines)
    steps, _ = parser._parse_steps(
        lines, tool_result_map, tmp_path / "fake.jsonl", depth=0,
    )
    m = compute_metrics(steps)
    assert m.total_input_tokens == 100, f"duplicate inflated input to {m.total_input_tokens}"
    assert m.total_output_tokens == 200, f"duplicate inflated output to {m.total_output_tokens}"
    assert m.total_cache_read_tokens == 5000
    assert m.total_cache_creation_tokens == 800


def test_pipeline_preserves_parser_aggregate_usage_when_steps_have_no_usage():
    parser_metrics = Metrics(
        total_steps=0,
        total_input_tokens=1000,
        total_output_tokens=120,
        total_cache_read_tokens=200,
    )
    step_metrics = Metrics(total_steps=3, total_duration_s=8.5)

    merged = _merge_parser_and_step_metrics(parser_metrics, step_metrics)

    assert merged.total_steps == 3
    assert merged.total_input_tokens == 1000
    assert merged.total_output_tokens == 120
    assert merged.total_cache_read_tokens == 200
    assert merged.total_duration_s == 8.5
    assert merged.cache_hit_rate == 0.1667


def test_pipeline_uses_step_totals_when_per_step_usage_exists_but_keeps_runtime_cost():
    parser_metrics = Metrics(
        total_input_tokens=9999,
        total_output_tokens=9999,
        total_duration_s=42.0,
        estimated_cost_usd=0.123456,
    )
    step_metrics = Metrics(
        total_steps=2,
        total_input_tokens=100,
        total_output_tokens=20,
        total_cache_read_tokens=50,
        total_duration_s=7.0,
        estimated_cost_usd=0.001,
    )

    merged = _merge_parser_and_step_metrics(parser_metrics, step_metrics)

    assert merged.total_steps == 2
    assert merged.total_input_tokens == 100
    assert merged.total_output_tokens == 20
    assert merged.total_cache_read_tokens == 50
    assert merged.total_duration_s == 42.0
    assert merged.estimated_cost_usd == 0.123456
    assert merged.cache_hit_rate == 0.3333

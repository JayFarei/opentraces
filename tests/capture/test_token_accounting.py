"""Regression test for plan 046: session token totals must surface all four
token buckets and must not double-count mirrored subagent events.
"""

from pathlib import Path

from opentraces.capture.claude_code.parse import ClaudeCodeParser
from opentraces.enrichment.metrics import compute_metrics
from opentraces_schema import Step, TokenUsage


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

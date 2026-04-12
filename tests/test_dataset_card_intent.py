"""Dataset card Intent-coverage section (plan 038 phase 3)."""

from __future__ import annotations

from opentraces_schema import Agent, Intent, Task, TraceRecord

from opentraces.publish.huggingface.dataset_card import _compute_stats, _render_stats_section


def _t(intent: Intent | None) -> TraceRecord:
    return TraceRecord(
        trace_id="t",
        session_id="s",
        task=Task(description="d"),
        agent=Agent(name="claude-code", model="m"),
        intent=intent,
    )


def test_compute_stats_counts_intent_coverage():
    traces = [
        _t(None),
        _t(Intent(title="a", source="llm_hook")),
        _t(Intent(title="b", source="llm_hook")),
        _t(Intent(title="c", source="post_processor")),
        _t(Intent(title="d", source="user")),
    ]
    stats = _compute_stats(traces)
    assert stats["intent_coverage"]["rows_with_intent"] == 4
    assert stats["intent_coverage"]["coverage_pct"] == 80.0
    assert stats["intent_coverage"]["by_source"] == {
        "llm_hook": 2,
        "post_processor": 1,
        "user": 1,
    }


def test_render_stats_section_documents_intent_and_source_enum():
    traces = [_t(Intent(title="x", source="llm_hook"))]
    stats = _compute_stats(traces)
    rendered = _render_stats_section(stats)
    assert "Intent Coverage" in rendered
    assert "source=`llm_hook`" in rendered
    # filter recipe / field documentation
    assert "intent" in rendered


def test_no_traces_no_crash():
    stats = _compute_stats([])
    assert stats["intent_coverage"]["rows_with_intent"] == 0
    _render_stats_section(stats)  # must not crash on empty

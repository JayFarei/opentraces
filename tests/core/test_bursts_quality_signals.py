"""Cluster F D11/D12 — per-burst quality signals.

D11: ``intent.commit_message_quality`` classifies the commit's prose
into one of four tiers (``bare`` / ``terse`` / ``descriptive`` /
``detailed``) plus structural metrics (subject_length, body_length,
has_conventional_prefix, paragraph_count).

D12: ``quality_signals`` counts error_signal / test_run / tool_call
nodes whose step_index falls within the burst's step_range — surfacing
how iteration-heavy the burst was.
"""

from __future__ import annotations

from opentraces.core.bursts import _commit_message_quality, _augment_quality_signals, Burst
from opentraces_schema import TraceMapNode


def test_commit_message_quality_tiers() -> None:
    """All four tiers + the conventional-prefix flag must be detected."""

    bare = _commit_message_quality("fix typo", None)
    assert bare["tier"] == "bare"
    assert bare["body_length"] == 0
    assert bare["has_conventional_prefix"] is False
    assert bare["paragraph_count"] == 0

    terse = _commit_message_quality(
        "feat(api): add stub", "Closes #42."
    )
    assert terse["tier"] == "terse"
    assert terse["has_conventional_prefix"] is True
    assert terse["paragraph_count"] == 1
    assert terse["body_length"] < 140

    desc_body = "This change re-routes the cache layer through the new pool. " * 4
    descriptive = _commit_message_quality("refactor(cache): re-route", desc_body)
    assert descriptive["tier"] == "descriptive"
    assert descriptive["has_conventional_prefix"] is True
    assert 140 <= descriptive["body_length"] <= 500
    assert descriptive["paragraph_count"] == 1

    detailed_body = (
        "First paragraph explains the why.\n\n"
        "Second paragraph explains the how, including a long discussion of "
        "edge cases and rollout considerations." + " "
    )
    detailed = _commit_message_quality(
        "fix(scheduler): respect deadlines",
        detailed_body,
    )
    assert detailed["tier"] == "detailed"
    assert detailed["paragraph_count"] >= 2

    # Long single-paragraph body also reaches ``detailed``.
    long_body = "single block " * 60  # ~720 chars, no double newline
    long_detailed = _commit_message_quality("docs: long block", long_body)
    assert long_detailed["tier"] == "detailed"
    assert long_detailed["body_length"] > 500
    assert long_detailed["paragraph_count"] == 1


def test_error_signal_count_within_step_range() -> None:
    """``quality_signals`` counts error_signal / test_run / tool_call nodes
    whose step_index falls inside the burst's [start, end] range.
    """
    nodes: list[TraceMapNode] = []
    # error_signal in range
    nodes.append(
        TraceMapNode(
            node_id="n1",
            trace_id="t1",
            unit_id="u1",
            action_type="error_signal",
            step_index=5,
        )
    )
    # error_signal outside range
    nodes.append(
        TraceMapNode(
            node_id="n2",
            trace_id="t1",
            unit_id="u2",
            action_type="error_signal",
            step_index=50,
        )
    )
    # test_run in range
    nodes.append(
        TraceMapNode(
            node_id="n3",
            trace_id="t1",
            unit_id="u3",
            action_type="test_run",
            step_index=8,
        )
    )
    # tool_call in range
    nodes.append(
        TraceMapNode(
            node_id="n4",
            trace_id="t1",
            unit_id="u4",
            action_type="tool_call",
            step_index=10,
        )
    )

    burst = Burst(
        step_range=[1, 11],
        intent_user_step=None,
        intent_text=None,
    )
    _augment_quality_signals(burst, nodes=nodes)
    qs = burst.quality_signals
    assert qs["error_signal_count"] == 1
    assert qs["test_run_count"] == 1
    assert qs["tool_call_count"] == 1
    # 1 tool_call across span = 11 ⇒ density ≈ 0.0909
    assert 0.08 < qs["tool_call_density"] < 0.10

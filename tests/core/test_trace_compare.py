"""Unit tests for deterministic two-trace comparison (plan 086)."""

from __future__ import annotations

import pytest
from opentraces_schema import Agent, Metrics, SecurityMetadata, Step, TraceRecord

from opentraces.core import trace_compare
from opentraces.core.trace_compare import compare_traces


def _record(trace_id, *, metrics=None, security=None, steps=None) -> TraceRecord:
    record = TraceRecord(
        trace_id=trace_id,
        session_id=f"sess-{trace_id}",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "test"},
        steps=steps or [Step(step_index=1, role="user", content="go")],
    )
    if metrics is not None:
        record.metrics = metrics
    if security is not None:
        record.security = security
    return record


def test_metrics_delta():
    a = _record("ta", metrics=Metrics(total_steps=10, total_input_tokens=1000, estimated_cost_usd=0.05))
    b = _record("tb", metrics=Metrics(total_steps=6, total_input_tokens=400, estimated_cost_usd=0.02))
    out = compare_traces(a, b, include_quality=False)
    m = out["delta"]["metrics"]
    assert m["total_input_tokens"] == {"a": 1000, "b": 400, "delta": -600}
    assert m["estimated_cost_usd"]["delta"] == pytest.approx(-0.03)
    assert m["total_steps"]["delta"] == -4


def test_null_side_yields_null_delta():
    a = _record("ta", metrics=Metrics(total_steps=5, cache_hit_rate=None))
    b = _record("tb", metrics=Metrics(total_steps=5, cache_hit_rate=0.5))
    out = compare_traces(a, b, include_quality=False)
    assert out["delta"]["metrics"]["cache_hit_rate"]["delta"] is None


def test_security_delta():
    a = _record("ta", metrics=Metrics(total_steps=1), security=SecurityMetadata(redactions_applied=3))
    b = _record("tb", metrics=Metrics(total_steps=1), security=SecurityMetadata(redactions_applied=1))
    out = compare_traces(a, b, include_quality=False)
    assert out["delta"]["security"]["redactions_applied"]["delta"] == -2


def test_no_quality_skips_assess_trace(monkeypatch):
    a = _record("ta", metrics=Metrics(total_steps=1))
    b = _record("tb", metrics=Metrics(total_steps=1))

    def _boom(*args, **kwargs):
        raise AssertionError("assess_trace must not run under include_quality=False")

    monkeypatch.setattr("opentraces.quality.engine.assess_trace", _boom)
    out = compare_traces(a, b, include_quality=False)
    assert out["quality_included"] is False
    assert out["delta"]["quality"] is None


def test_quality_block_present_when_enabled():
    a = _record("ta", metrics=Metrics(total_steps=1))
    b = _record("tb", metrics=Metrics(total_steps=1))
    out = compare_traces(a, b, include_quality=True)
    q = out["delta"]["quality"]
    assert q is not None
    assert set(q["personas"]) == {"conformance", "training", "rl", "analytics", "domain"}
    assert set(q["overall_utility"]) == {"a", "b", "delta"}


def test_bursts_unavailable_without_map():
    # No Trace Index in this unit context → both burst aggregates unavailable.
    a = _record("ta", metrics=Metrics(total_steps=1))
    b = _record("tb", metrics=Metrics(total_steps=1))
    out = compare_traces(a, b, include_quality=False)
    bursts = out["delta"]["bursts"]
    assert bursts["available_a"] is False
    assert bursts["available_b"] is False
    assert bursts["burst_count"]["delta"] is None


def test_envelope_shape_and_determinism():
    a = _record("ta", metrics=Metrics(total_steps=2))
    b = _record("tb", metrics=Metrics(total_steps=3))
    out1 = compare_traces(a, b, include_quality=False)
    out2 = compare_traces(a, b, include_quality=False)
    assert out1 == out2
    assert out1["schema_version"] == "opentraces.trace_compare.v1"
    assert out1["status"] == "ok"
    assert out1["fidelity"] == {"a": "record", "b": "record"}
    assert set(out1["delta"]) == {"metrics", "quality", "bursts", "signals", "security"}


def test_compare_reports_per_side_otel_fidelity():
    a = _record("ta", metrics=Metrics(total_steps=2))
    b = _record("tb", metrics=Metrics(total_steps=3))
    b.context_tree_summary = {"capture_methods": ["otel"]}
    out = compare_traces(a, b, include_quality=False)
    assert out["fidelity"] == {"a": "record", "b": "otel"}

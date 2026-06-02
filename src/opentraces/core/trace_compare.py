"""Deterministic two-trace comparison (plan 086, extracted from tracebase run-compare).

Computes ``{a, b, delta}`` triples between two traces over Metrics, deterministic
quality persona scores, change-burst / error signals, and security metadata. This
is a pure, derive-on-demand projection: it never re-implements scoring (it reuses
``enrichment.metrics.compute_metrics`` and ``quality.engine.assess_trace`` with the
judge disabled) and writes nothing back to any record. Both traces are pinned to
the SAME burst gap so burst-count deltas are comparable (no adaptive path).

Emits the frozen ``opentraces.trace_compare.v1`` envelope.
"""

from __future__ import annotations

from typing import Any

from opentraces_schema import TraceRecord

from .bursts import DEFAULT_BURST_GAP

TRACE_COMPARE_SCHEMA_VERSION = "opentraces.trace_compare.v1"

# Fixed persona ordering for the quality block (mirrors quality/engine defaults).
_PERSONA_NAMES = ["conformance", "training", "rl", "analytics", "domain"]

_METRIC_FIELDS = [
    "total_steps",
    "total_input_tokens",
    "total_output_tokens",
    "total_cache_read_tokens",
    "total_cache_creation_tokens",
    "total_duration_s",
    "cache_hit_rate",
    "estimated_cost_usd",
]


def _triple(a: float | int | None, b: float | int | None) -> dict[str, Any]:
    delta: float | int | None = None
    if a is not None and b is not None:
        delta = round(b - a, 6) if isinstance(b - a, float) else b - a
    return {"a": a, "b": b, "delta": delta}


def _metrics_for(record: TraceRecord):
    from ..enrichment.metrics import compute_metrics

    metrics = record.metrics
    if metrics is None or metrics.total_steps == 0:
        model = record.agent.model if record.agent else None
        metrics = compute_metrics(record.steps, model_fallback=model)
    return metrics


def _detect_fidelity(record: TraceRecord) -> str:
    summary = getattr(record, "context_tree_summary", None)
    if isinstance(summary, dict):
        methods = summary.get("capture_methods") or []
        if isinstance(methods, (list, tuple)) and "otel" in methods:
            return "otel"
    return "record"


def _quality_for(record: TraceRecord):
    from ..quality.engine import assess_trace

    return assess_trace(record, raw_session_path=None, enable_judge=False)


def _burst_aggregate(record: TraceRecord, gap: int) -> dict[str, Any] | None:
    """Aggregate burst/error signals for one trace, or None if no Trace Map."""
    from .bursts import detect_bursts
    from .trace_index import get_trace_map

    # Degrade-never-crash: a missing index returns None; a malformed/locked
    # index DB (or any projection failure) is treated the same as "no map"
    # rather than aborting the whole comparison.
    try:
        trace_map = get_trace_map(record.trace_id)
        if trace_map is None:
            return None
        bursts = detect_bursts(trace_map, gap=gap, trace_record=record, commit_lookup=False)
    except Exception:
        return None
    files = sum(sum(b.unique_files.values()) for b in bursts)
    patches = sum(len(b.patches) for b in bursts)
    sig = {"error_signal_count": 0, "test_run_count": 0, "tool_call_count": 0}
    for b in bursts:
        for key in sig:
            sig[key] += int(b.quality_signals.get(key, 0) or 0)
    return {"burst_count": len(bursts), "files": files, "patches": patches, "signals": sig}


def compare_traces(
    record_a: TraceRecord,
    record_b: TraceRecord,
    *,
    include_quality: bool = True,
    burst_gap: int = DEFAULT_BURST_GAP,
) -> dict[str, Any]:
    """Compare two traces, returning the frozen opentraces.trace_compare.v1 envelope."""

    ma, mb = _metrics_for(record_a), _metrics_for(record_b)
    metrics_delta = {
        field: _triple(getattr(ma, field), getattr(mb, field)) for field in _METRIC_FIELDS
    }

    quality_block: dict[str, Any] | None = None
    if include_quality:
        qa, qb = _quality_for(record_a), _quality_for(record_b)
        personas: dict[str, Any] = {}
        for name in _PERSONA_NAMES:
            psa, psb = qa.persona_scores.get(name), qb.persona_scores.get(name)
            va = None if psa is None or psa.skipped else psa.total_score
            vb = None if psb is None or psb.skipped else psb.total_score
            entry = _triple(va, vb)
            entry["available"] = psa is not None and psb is not None
            personas[name] = entry
        quality_block = {
            "personas": personas,
            "overall_utility": _triple(qa.overall_utility, qb.overall_utility),
        }

    agg_a = _burst_aggregate(record_a, burst_gap)
    agg_b = _burst_aggregate(record_b, burst_gap)

    def _agg(key: str, sub: str | None = None):
        def pick(agg):
            if agg is None:
                return None
            return agg["signals"][sub] if sub else agg[key]

        return _triple(pick(agg_a), pick(agg_b))

    bursts_block = {
        "available_a": agg_a is not None,
        "available_b": agg_b is not None,
        "burst_count": _agg("burst_count"),
        "files": _agg("files"),
        "patches": _agg("patches"),
    }
    signals_block = {
        "error_signal_count": _agg("signals", "error_signal_count"),
        "test_run_count": _agg("signals", "test_run_count"),
        "tool_call_count": _agg("signals", "tool_call_count"),
    }

    sa, sb = record_a.security, record_b.security
    security_block = {
        "flags_reviewed": _triple(
            sa.flags_reviewed if sa else None, sb.flags_reviewed if sb else None
        ),
        "redactions_applied": _triple(
            sa.redactions_applied if sa else None, sb.redactions_applied if sb else None
        ),
    }

    return {
        "schema_version": TRACE_COMPARE_SCHEMA_VERSION,
        "status": "ok",
        "trace_a": record_a.trace_id,
        "trace_b": record_b.trace_id,
        "fidelity": {
            "a": _detect_fidelity(record_a),
            "b": _detect_fidelity(record_b),
        },
        "burst_gap": burst_gap,
        "quality_included": include_quality,
        "delta": {
            "metrics": metrics_delta,
            "quality": quality_block,
            "bursts": bursts_block,
            "signals": signals_block,
            "security": security_block,
        },
    }

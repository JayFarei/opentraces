"""Analytics/Observability persona rubric for trace quality assessment.

Evaluates traces through the lens of an Analytics consumer.
Checks focus on cost metrics, cache efficiency, timestamps, token
breakdowns, and internal consistency, which differentiate opentraces
from traces.com (trace-level aggregates only).
"""

from __future__ import annotations

from opentraces_schema import TraceRecord

from ..types import CheckDef, CheckResult, PersonaDef


def _a1_cache_hit_rate(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """A1: cache_hit_rate computed and in [0.0, 1.0] (weight 1.0).

    'Architectural fingerprint' per Kobe Chen's research.
    """
    rate = record.metrics.cache_hit_rate
    if rate is not None and 0.0 <= rate <= 1.0:
        return CheckResult(
            passed=True, score=1.0,
            evidence=f"cache_hit_rate={rate:.3f}",
        )
    if rate is None:
        return CheckResult(passed=False, score=0.0, evidence="cache_hit_rate=None")
    return CheckResult(
        passed=False, score=0.0,
        evidence=f"cache_hit_rate={rate} (out of range)",
    )


def _a2_estimated_cost(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """A2: estimated_cost_usd > 0 (weight 0.9)."""
    cost = record.metrics.estimated_cost_usd
    if cost is not None and cost > 0:
        return CheckResult(passed=True, score=1.0, evidence=f"estimated_cost_usd=${cost:.4f}")
    return CheckResult(passed=False, score=0.0, evidence=f"estimated_cost_usd={cost!r}")


def _a3_total_duration(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """A3: total_duration_s > 0 (weight 0.8)."""
    dur = record.metrics.total_duration_s
    if dur is not None and dur > 0:
        return CheckResult(passed=True, score=1.0, evidence=f"total_duration_s={dur:.1f}")
    return CheckResult(passed=False, score=0.0, evidence=f"total_duration_s={dur!r}")


def _a4_step_timestamps(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """A4: Timestamps on >80% of steps (weight 0.7).

    Per-step timeline, traces.com only has trace-level timestamps.
    """
    if not record.steps:
        return CheckResult(passed=True, score=1.0, evidence="No steps")

    with_ts = sum(1 for s in record.steps if s.timestamp and s.timestamp.strip())
    ratio = with_ts / len(record.steps)
    passed = ratio >= 0.8
    return CheckResult(
        passed=passed,
        score=round(ratio, 3),
        evidence=f"{with_ts}/{len(record.steps)} steps have timestamps ({ratio:.0%})",
    )


def _a5_token_breakdown(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """A5: Token breakdown per step populated on agent steps (weight 0.8).

    input + output populated on agent steps (cache_read may legitimately be 0).
    """
    agent_steps = [s for s in record.steps if s.role == "agent"]
    if not agent_steps:
        return CheckResult(passed=True, score=1.0, evidence="No agent steps")

    fully_populated = sum(
        1 for s in agent_steps
        if (s.token_usage.input_tokens > 0
            and s.token_usage.output_tokens > 0)
    )
    ratio = fully_populated / len(agent_steps)
    passed = ratio >= 0.8
    return CheckResult(
        passed=passed,
        score=round(ratio, 3),
        evidence=f"{fully_populated}/{len(agent_steps)} agent steps have input+output tokens ({ratio:.0%})",
    )


def _a6_agent_model_identified(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """A6: Agent model identified on steps (weight 0.6).

    Model field on agent steps for model distribution analytics.
    """
    agent_steps = [s for s in record.steps if s.role == "agent"]
    if not agent_steps:
        return CheckResult(passed=True, score=1.0, evidence="No agent steps")

    with_model = sum(1 for s in agent_steps if s.model and s.model.strip())
    ratio = with_model / len(agent_steps)
    passed = ratio >= 0.5
    return CheckResult(
        passed=passed,
        score=round(ratio, 3),
        evidence=f"{with_model}/{len(agent_steps)} agent steps have model field ({ratio:.0%})",
    )


def _a7_total_steps_consistent(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """A7: total_steps matches len(steps) (weight 0.5).

    Internal consistency check.
    """
    actual = len(record.steps)
    reported = record.metrics.total_steps
    if reported == actual:
        return CheckResult(
            passed=True, score=1.0,
            evidence=f"total_steps={reported} matches len(steps)={actual}",
        )
    return CheckResult(
        passed=False, score=0.0,
        evidence=f"total_steps={reported} != len(steps)={actual}",
    )


def _a8_warmup_distinction(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """A8: Warmup vs real step distinction via call_type (weight 0.4).

    Accurate step count analytics require distinguishing warmup from real steps.
    """
    if not record.steps:
        return CheckResult(passed=True, score=1.0, evidence="No steps")

    with_call_type = sum(1 for s in record.steps if s.call_type is not None)
    ratio = with_call_type / len(record.steps)
    passed = ratio >= 0.5
    return CheckResult(
        passed=passed,
        score=round(ratio, 3),
        evidence=f"{with_call_type}/{len(record.steps)} steps have call_type set ({ratio:.0%})",
    )


ANALYTICS_PERSONA = PersonaDef(
    name="analytics",
    description="Analytics/Observability consumer: evaluates trace utility for cost modeling and session analytics",
    checks=[
        CheckDef(name="A1: Cache hit rate computed", category="analytics", weight=1.0, check=_a1_cache_hit_rate),
        CheckDef(name="A2: Estimated cost > 0", category="analytics", weight=0.9, check=_a2_estimated_cost),
        CheckDef(name="A3: Total duration > 0", category="analytics", weight=0.8, check=_a3_total_duration),
        CheckDef(name="A4: Step timestamps", category="analytics", weight=0.7, check=_a4_step_timestamps),
        CheckDef(name="A5: Token breakdown per step", category="analytics", weight=0.8, check=_a5_token_breakdown),
        CheckDef(name="A6: Agent model on steps", category="analytics", weight=0.6, check=_a6_agent_model_identified),
        CheckDef(name="A7: Total steps consistent", category="analytics", weight=0.5, check=_a7_total_steps_consistent),
        CheckDef(name="A8: Warmup/real distinction", category="analytics", weight=0.4, check=_a8_warmup_distinction),
    ],
)

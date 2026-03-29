"""RL/RLHF persona rubric for trace quality assessment.

Evaluates traces through the lens of an RL/RLHF consumer.
Checks focus on outcome signals (reward proxies), cost signals,
sub-agent hierarchy, and model identification, which are unique
to opentraces vs ADP/traces.com.
"""

from __future__ import annotations

from opentraces_schema import TraceRecord

from ..types import CheckDef, CheckResult, PersonaDef


def _rl1_committed_explicitly_set(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """RL1: outcome.committed explicitly set (weight 1.0).

    The primary reward proxy. Check if enrichment actually ran and set this
    field, rather than relying on the default False.
    """
    # If committed is True, it was definitely set explicitly
    if record.outcome.committed is True:
        return CheckResult(
            passed=True, score=1.0,
            evidence="outcome.committed=True (explicitly set)",
        )

    # If committed is False but we have a commit_sha or patch, something is wrong
    if record.outcome.commit_sha or record.outcome.patch:
        return CheckResult(
            passed=False, score=0.3,
            evidence="outcome.committed=False but commit_sha/patch present (inconsistent)",
        )

    # Check if the raw data shows enrichment set the outcome
    if raw_data and raw_data.get("outcome", {}).get("signal_source") != "deterministic":
        return CheckResult(
            passed=True, score=0.8,
            evidence="outcome.committed=False with non-default signal_source (enrichment ran)",
        )

    # Default False could be genuine (no commit) or uninitialised
    return CheckResult(
        passed=False, score=0.0,
        evidence="outcome.committed=False (may be default, enrichment may not have run)",
    )


def _rl2_signal_confidence_set(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """RL2: signal_confidence is derived or annotated (weight 1.0).

    RL consumers need to know confidence to weight samples.
    'derived' is the schema default, but we check that enrichment
    actively produced this value rather than leaving the default.
    """
    conf = record.outcome.signal_confidence
    # If committed is True, enrichment must have run, so the confidence is real
    if record.outcome.committed is True:
        return CheckResult(
            passed=True, score=1.0,
            evidence=f"signal_confidence={conf!r} with committed=True (enrichment confirmed)",
        )

    if conf == "annotated":
        return CheckResult(
            passed=True, score=1.0,
            evidence=f"signal_confidence={conf!r} (human annotation)",
        )

    if conf == "derived" and record.outcome.success is not None:
        return CheckResult(
            passed=True, score=0.8,
            evidence=f"signal_confidence={conf!r} with success={record.outcome.success} (enrichment likely ran)",
        )

    return CheckResult(
        passed=False, score=0.0,
        evidence=f"signal_confidence={conf!r} but no evidence enrichment actively set it",
    )


def _rl3_patch_when_committed(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """RL3: patch present when committed=True (weight 0.9).

    Ground truth diff for reward attribution.
    """
    if not record.outcome.committed:
        return CheckResult(
            passed=True, score=1.0,
            evidence="committed=False, patch not required",
            note="N/A for uncommitted sessions",
        )

    if record.outcome.patch and record.outcome.patch.strip():
        return CheckResult(
            passed=True, score=1.0,
            evidence=f"Patch present ({len(record.outcome.patch)} chars)",
        )

    return CheckResult(
        passed=False, score=0.0,
        evidence="committed=True but no patch present",
    )


def _rl4_per_step_token_usage(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """RL4: Per-step token_usage on >80% of agent steps (weight 0.8).

    Enables cost-penalized reward functions.
    """
    agent_steps = [s for s in record.steps if s.role == "agent"]
    if not agent_steps:
        return CheckResult(passed=True, score=1.0, evidence="No agent steps")

    with_tokens = sum(
        1 for s in agent_steps
        if s.token_usage.input_tokens > 0 or s.token_usage.output_tokens > 0
    )
    ratio = with_tokens / len(agent_steps)
    passed = ratio >= 0.8
    return CheckResult(
        passed=passed,
        score=round(ratio, 3),
        evidence=f"{with_tokens}/{len(agent_steps)} agent steps have token_usage ({ratio:.0%})",
    )


def _rl5_estimated_cost(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """RL5: estimated_cost_usd > 0 (weight 0.7)."""
    cost = record.metrics.estimated_cost_usd
    if cost is not None and cost > 0:
        return CheckResult(
            passed=True, score=1.0,
            evidence=f"estimated_cost_usd=${cost:.4f}",
        )
    return CheckResult(
        passed=False, score=0.0,
        evidence=f"estimated_cost_usd={cost!r}",
    )


def _rl6_subagent_hierarchy(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """RL6: Sub-agent hierarchy intact when subagents exist (weight 0.7).

    All call_type='subagent' steps should have parent_step set.
    """
    subagent_steps = [s for s in record.steps if s.call_type == "subagent"]
    if not subagent_steps:
        return CheckResult(
            passed=True, score=1.0,
            evidence="No subagent steps in trace",
            note="N/A for single-agent sessions",
        )

    with_parent = sum(1 for s in subagent_steps if s.parent_step is not None)
    ratio = with_parent / len(subagent_steps)
    passed = ratio >= 0.9
    return CheckResult(
        passed=passed,
        score=round(ratio, 3),
        evidence=f"{with_parent}/{len(subagent_steps)} subagent steps have parent_step",
    )


def _rl7_outcome_success(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """RL7: outcome.success explicitly set (weight 0.4).

    Bonus signal, not required per zero-required-annotation principle.
    """
    if record.outcome.success is not None:
        return CheckResult(
            passed=True, score=1.0,
            evidence=f"outcome.success={record.outcome.success}",
        )
    return CheckResult(
        passed=False, score=0.0,
        evidence="outcome.success=None (not set)",
    )


def _rl8_model_on_agent_steps(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """RL8: model field populated on agent steps (weight 0.3).

    Multi-model orchestration research signal.
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
        evidence=f"{with_model}/{len(agent_steps)} agent steps have model field",
    )


RL_PERSONA = PersonaDef(
    name="rl",
    description="RL/RLHF consumer: evaluates trace utility for reinforcement learning from human feedback",
    checks=[
        CheckDef(name="RL1: Committed explicitly set", category="rl", weight=1.0, check=_rl1_committed_explicitly_set),
        CheckDef(name="RL2: Signal confidence set", category="rl", weight=1.0, check=_rl2_signal_confidence_set),
        CheckDef(name="RL3: Patch when committed", category="rl", weight=0.9, check=_rl3_patch_when_committed),
        CheckDef(name="RL4: Per-step token usage", category="rl", weight=0.8, check=_rl4_per_step_token_usage),
        CheckDef(name="RL5: Estimated cost > 0", category="rl", weight=0.7, check=_rl5_estimated_cost),
        CheckDef(name="RL6: Sub-agent hierarchy", category="rl", weight=0.7, check=_rl6_subagent_hierarchy),
        CheckDef(name="RL7: Outcome success set", category="rl", weight=0.4, check=_rl7_outcome_success),
        CheckDef(name="RL8: Model on agent steps", category="rl", weight=0.3, check=_rl8_model_on_agent_steps),
    ],
)

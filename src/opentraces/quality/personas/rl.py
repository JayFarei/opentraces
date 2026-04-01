"""RL/RLHF persona rubric for trace quality assessment.

Evaluates traces through the lens of an RL/RLHF consumer.
Checks focus on outcome signals (reward proxies), cost signals,
sub-agent hierarchy, and model identification, which are unique
to opentraces vs ADP/traces.com.
"""

from __future__ import annotations

from opentraces_schema import TraceRecord

from ..types import CheckDef, CheckResult, PersonaDef


def _rl1_grounded_outcome_signal(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """RL1: grounded outcome signal present, appropriate to execution context (weight 1.0).

    Devtime agents: committed=True is the ground truth reward proxy (code reached git).
    Runtime agents: terminal_state or reward from environment is the ground truth.
    Checks for the right signal type based on execution_context.
    """
    ctx = record.execution_context

    if ctx == "runtime" or (ctx is None and record.outcome.terminal_state is not None):
        # Runtime path: check for terminal_state or numeric reward
        if record.outcome.reward is not None:
            return CheckResult(
                passed=True, score=1.0,
                evidence=f"reward={record.outcome.reward} from {record.outcome.reward_source!r} (ground truth)",
            )
        if record.outcome.terminal_state == "goal_reached":
            return CheckResult(
                passed=True, score=0.8,
                evidence=f"terminal_state=goal_reached (inferred from source metadata)",
            )
        if record.outcome.terminal_state in ("interrupted", "abandoned", "error"):
            return CheckResult(
                passed=True, score=0.6,
                evidence=f"terminal_state={record.outcome.terminal_state!r} (negative outcome, still grounded)",
            )
        return CheckResult(
            passed=False, score=0.0,
            evidence="runtime trace with no terminal_state or reward signal",
        )

    # Devtime path (ctx == "devtime" or unset with no terminal_state)
    if record.outcome.committed is True:
        return CheckResult(
            passed=True, score=1.0,
            evidence="outcome.committed=True (git commit ground truth)",
        )
    if record.outcome.commit_sha or record.outcome.patch:
        return CheckResult(
            passed=False, score=0.3,
            evidence="outcome.committed=False but commit_sha/patch present (inconsistent)",
        )
    # Explicit failure signal (success=False) is grounded, same as runtime failures
    if record.outcome.success is False:
        return CheckResult(
            passed=True, score=0.6,
            evidence="outcome.success=False (explicit failure signal, grounded)",
        )
    return CheckResult(
        passed=False, score=0.0,
        evidence="outcome.committed=False (no git commit signal)",
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

    # Runtime: reward from RL environment is ground truth, counts as derived
    if record.outcome.reward is not None and record.outcome.reward_source:
        return CheckResult(
            passed=True, score=0.9,
            evidence=f"reward={record.outcome.reward} from {record.outcome.reward_source!r} (environment ground truth)",
        )

    # Runtime: terminal_state with inferred confidence (source metadata)
    if conf == "inferred" and record.outcome.terminal_state is not None:
        return CheckResult(
            passed=True, score=0.6,
            evidence=f"signal_confidence={conf!r} with terminal_state={record.outcome.terminal_state!r} (source metadata)",
        )

    # Generic inferred with success signal
    if conf == "inferred" and record.outcome.success is not None:
        return CheckResult(
            passed=True, score=0.5,
            evidence=f"signal_confidence={conf!r} with success={record.outcome.success} (source metadata, not enrichment-derived)",
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
    Skip for conversation-turn traces: source only provides session-level usage,
    not per-API-call breakdowns.
    """
    if record.metadata.get("step_fidelity") == "conversation_turn":
        return CheckResult(
            passed=False, score=0.0,
            evidence="N/A: conversation_turn source provides session-level tokens only",
            skipped=True,
        )
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
    """RL7: outcome signal explicitly set (weight 0.4).

    Bonus signal. Accepts success (any context), terminal_state, or reward
    as valid explicitly-set outcome signals.
    """
    if record.outcome.success is not None:
        return CheckResult(
            passed=True, score=1.0,
            evidence=f"outcome.success={record.outcome.success}",
        )
    if record.outcome.terminal_state is not None:
        return CheckResult(
            passed=True, score=0.8,
            evidence=f"outcome.terminal_state={record.outcome.terminal_state!r}",
        )
    if record.outcome.reward is not None:
        return CheckResult(
            passed=True, score=1.0,
            evidence=f"outcome.reward={record.outcome.reward}",
        )
    return CheckResult(
        passed=False, score=0.0,
        evidence="No outcome signal set (success=None, terminal_state=None, reward=None)",
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
        CheckDef(name="RL1: Grounded outcome signal", category="rl", weight=1.0, check=_rl1_grounded_outcome_signal),
        CheckDef(name="RL2: Signal confidence set", category="rl", weight=1.0, check=_rl2_signal_confidence_set),
        CheckDef(name="RL3: Patch when committed", category="rl", weight=0.9, check=_rl3_patch_when_committed),
        CheckDef(name="RL4: Per-step token usage", category="rl", weight=0.8, check=_rl4_per_step_token_usage),
        CheckDef(name="RL5: Estimated cost > 0", category="rl", weight=0.7, check=_rl5_estimated_cost),
        CheckDef(name="RL6: Sub-agent hierarchy", category="rl", weight=0.7, check=_rl6_subagent_hierarchy),
        CheckDef(name="RL7: Outcome success set", category="rl", weight=0.4, check=_rl7_outcome_success),
        CheckDef(name="RL8: Model on agent steps", category="rl", weight=0.3, check=_rl8_model_on_agent_steps),
    ],
)

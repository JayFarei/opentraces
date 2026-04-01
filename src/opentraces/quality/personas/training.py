"""Training/SFT persona rubric for trace quality assessment.

Evaluates traces through the lens of a Training/SFT consumer.
Checks are grounded in ADP (Agent Data Protocol) empirical requirements:
alternating roles, tool_call/observation pairing, reasoning coverage,
and data cleanliness for supervised fine-tuning pipelines.
"""

from __future__ import annotations

from opentraces_schema import TraceRecord

from ..types import CheckDef, CheckResult, PersonaDef


def _t1_alternating_roles(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """T1: Alternating user/agent role pattern (weight 1.0).

    ADP requires alternating action/observation sequences. System steps
    are allowed anywhere but user and agent should alternate.

    Q3: For conversation-turn agents (step_fidelity=conversation_turn),
    consecutive same-role steps are structural (e.g. user prompt followed
    by user-provided tool context). Use a relaxed 50% threshold instead
    of 90%.
    """
    steps = record.steps
    if len(steps) < 2:
        return CheckResult(
            passed=True, score=1.0,
            evidence=f"{len(steps)} steps, too few to check alternation",
        )

    # Filter out system steps AND subagent steps (they're nested execution,
    # not top-level conversational turns, and break alternation at boundaries).
    # For agents without call_type taxonomy, skip the subagent filter.
    fidelity = record.metadata.get("step_fidelity")
    if fidelity == "conversation_turn":
        non_system = [s for s in steps if s.role != "system"]
    else:
        non_system = [s for s in steps if s.role != "system" and s.call_type != "subagent"]
    if len(non_system) < 2:
        return CheckResult(
            passed=True, score=1.0,
            evidence="Fewer than 2 non-system steps",
        )

    violations = 0
    for i in range(1, len(non_system)):
        if non_system[i].role == non_system[i - 1].role:
            violations += 1

    total_transitions = len(non_system) - 1
    if total_transitions == 0:
        return CheckResult(passed=True, score=1.0, evidence="Single non-system step")

    ratio = 1.0 - (violations / total_transitions)
    # Conversation-turn agents naturally have consecutive same-role steps
    threshold = 0.5 if fidelity == "conversation_turn" else 0.9
    passed = ratio >= threshold
    return CheckResult(
        passed=passed,
        score=round(ratio, 3),
        evidence=f"{violations} alternation violations in {total_transitions} transitions",
    )


def _t2_tool_call_observation_pairing(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """T2: Every tool_call has a matching observation (weight 1.0).

    All tool_call_ids should have matching source_call_ids in observations.
    """
    all_call_ids: set[str] = set()
    all_source_ids: set[str] = set()

    for step in record.steps:
        for tc in step.tool_calls:
            all_call_ids.add(tc.tool_call_id)
        for obs in step.observations:
            all_source_ids.add(obs.source_call_id)

    if not all_call_ids:
        return CheckResult(
            passed=True, score=1.0,
            evidence="No tool calls in trace",
        )

    unmatched = all_call_ids - all_source_ids
    ratio = 1.0 - (len(unmatched) / len(all_call_ids))
    passed = ratio >= 0.95
    return CheckResult(
        passed=passed,
        score=round(max(ratio, 0.0), 3),
        evidence=f"{len(unmatched)} of {len(all_call_ids)} tool_calls lack matching observations",
    )


def _t3_no_dangling_observations(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """T3: No dangling observations (weight 0.9).

    All observations' source_call_ids should link to valid tool_calls.
    """
    all_call_ids: set[str] = set()
    all_source_ids: set[str] = set()

    for step in record.steps:
        for tc in step.tool_calls:
            all_call_ids.add(tc.tool_call_id)
        for obs in step.observations:
            all_source_ids.add(obs.source_call_id)

    if not all_source_ids:
        return CheckResult(passed=True, score=1.0, evidence="No observations in trace")

    dangling = all_source_ids - all_call_ids
    ratio = 1.0 - (len(dangling) / len(all_source_ids))
    passed = ratio >= 0.95
    return CheckResult(
        passed=passed,
        score=round(max(ratio, 0.0), 3),
        evidence=f"{len(dangling)} of {len(all_source_ids)} observations have no matching tool_call",
    )


def _t4_system_prompts_deduplicated(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """T4: System prompts deduplicated (weight 0.7).

    system_prompts dict should be non-empty, indicating deduplication occurred.
    """
    count = len(record.system_prompts)
    if count > 0:
        return CheckResult(
            passed=True, score=1.0,
            evidence=f"{count} deduplicated system prompts in top-level map",
        )
    return CheckResult(
        passed=False, score=0.0,
        evidence="system_prompts dict is empty",
    )


def _t5_agent_steps_have_content(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """T5: Content fields non-empty on agent steps (weight 0.8).

    Agent steps should have content or tool_calls (not both empty).
    """
    agent_steps = [s for s in record.steps if s.role == "agent"]
    if not agent_steps:
        return CheckResult(passed=True, score=1.0, evidence="No agent steps")

    with_content = sum(
        1 for s in agent_steps
        if (s.content and s.content.strip()) or s.tool_calls
    )
    ratio = with_content / len(agent_steps)
    passed = ratio >= 0.9
    return CheckResult(
        passed=passed,
        score=round(ratio, 3),
        evidence=f"{with_content}/{len(agent_steps)} agent steps have content or tool_calls",
    )


def _t6_reasoning_coverage(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """T6: Reasoning coverage >= 80% of agent steps with tool calls (weight 0.8).

    ADP quality gate: >=80% of tool calls should be paired with reasoning text.
    """
    agent_steps_with_tools = [
        s for s in record.steps
        if s.role == "agent" and s.tool_calls
    ]
    if not agent_steps_with_tools:
        return CheckResult(passed=True, score=1.0, evidence="No agent steps with tool calls")

    readable = 0
    redacted = 0
    for s in agent_steps_with_tools:
        if s.reasoning_content and s.reasoning_content.strip():
            if s.reasoning_content.startswith("[redacted"):
                redacted += 1
            else:
                readable += 1

    # Readable reasoning is full credit, redacted is 0.5 credit
    # (model DID reason, but content is not available for SFT)
    effective = readable + (redacted * 0.5)
    ratio = effective / len(agent_steps_with_tools)
    passed = ratio >= 0.8
    return CheckResult(
        passed=passed,
        score=round(min(ratio, 1.0), 3),
        evidence=f"{readable} readable + {redacted} redacted / {len(agent_steps_with_tools)} agent+tool steps ({ratio:.0%} effective)",
        note="ADP quality gate: >=80% reasoning coverage. Redacted thinking counts as 0.5.",
    )


def _t7_reasoning_content_present(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """T7: Reasoning content present, partial credit 0.5 for encrypted (weight 0.5).

    Any reasoning_content present in the trace. Encrypted thinking (present
    but marked as encrypted) gets partial credit of 0.5.
    """
    agent_steps = [s for s in record.steps if s.role == "agent"]
    if not agent_steps:
        return CheckResult(passed=True, score=1.0, evidence="No agent steps")

    full_reasoning = 0
    encrypted_reasoning = 0
    for s in agent_steps:
        if s.reasoning_content and s.reasoning_content.strip():
            if s.reasoning_content.startswith("[encrypted"):
                encrypted_reasoning += 1
            else:
                full_reasoning += 1

    total = len(agent_steps)
    score = (full_reasoning + 0.5 * encrypted_reasoning) / total
    any_present = full_reasoning > 0 or encrypted_reasoning > 0
    return CheckResult(
        passed=any_present,
        score=round(min(score, 1.0), 3),
        evidence=f"{full_reasoning} full + {encrypted_reasoning} encrypted reasoning in {total} agent steps",
    )


def _t8_task_description_present(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """T8: Task description present (weight 0.6)."""
    desc = record.task.description
    if desc and desc.strip():
        return CheckResult(passed=True, score=1.0, evidence=f"Task description: {desc[:80]!r}")
    return CheckResult(passed=False, score=0.0, evidence="task.description is empty or None")


def _t9_outcome_signals_present(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """T9: Outcome signals present (weight 0.7).

    Devtime: outcome.committed=True or outcome.success is not None.
    Runtime: also accepts terminal_state or reward as valid outcome signals.
    """
    parts = []
    if record.outcome.committed is True:
        parts.append("committed=True")
    if record.outcome.success is not None:
        parts.append(f"success={record.outcome.success}")
    if record.outcome.terminal_state is not None:
        parts.append(f"terminal_state={record.outcome.terminal_state}")
    if record.outcome.reward is not None:
        parts.append(f"reward={record.outcome.reward}")

    if parts:
        return CheckResult(passed=True, score=1.0, evidence=f"Outcome signals: {', '.join(parts)}")
    return CheckResult(
        passed=False, score=0.0,
        evidence="No outcome signals (committed=False, success=None, terminal_state=None, reward=None)",
    )


def _t10_warmup_steps_labeled(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """T10: Warmup steps labeled (weight 0.5).

    Steps that are warmup should have call_type='warmup'. If no warmup steps
    exist, this passes (nothing to label).

    Q2: Skip for conversation-turn agents (no warmup/subagent taxonomy).
    """
    if not record.steps:
        return CheckResult(passed=True, score=1.0, evidence="No steps")

    fidelity = record.metadata.get("step_fidelity")
    if fidelity == "conversation_turn":
        return CheckResult(
            passed=False, score=0.0,
            evidence="N/A: conversation_turn fidelity has no warmup taxonomy",
            skipped=True,
        )

    steps_with_call_type = [s for s in record.steps if s.call_type is not None]
    warmup_steps = [s for s in record.steps if s.call_type == "warmup"]

    # If no steps have call_type set, labeling was not applied
    if not steps_with_call_type:
        return CheckResult(
            passed=False, score=0.0,
            evidence="No steps have call_type set (warmup labeling not applied)",
        )

    # call_type labeling is active but no warmup steps detected
    if not warmup_steps:
        return CheckResult(
            passed=True, score=0.5,
            evidence=f"{len(steps_with_call_type)} labeled steps but no warmup steps (may be legitimate for short sessions)",
        )

    # Warmup steps exist: check they are at the beginning (before main/subagent steps)
    non_warmup_types = {"main", "subagent"}
    first_non_warmup_idx = None
    last_warmup_idx = None
    for i, step in enumerate(record.steps):
        if step.call_type in non_warmup_types and first_non_warmup_idx is None:
            first_non_warmup_idx = i
        if step.call_type == "warmup":
            last_warmup_idx = i

    if first_non_warmup_idx is not None and last_warmup_idx is not None:
        if last_warmup_idx < first_non_warmup_idx:
            # Warmup steps are all before main/subagent steps
            return CheckResult(
                passed=True, score=1.0,
                evidence=f"{len(warmup_steps)} warmup steps at beginning, before main/subagent steps",
            )
        else:
            # Warmup steps appear after main/subagent steps
            return CheckResult(
                passed=False, score=0.3,
                evidence=f"Warmup steps mis-labeled: last warmup at index {last_warmup_idx}, first main/subagent at {first_non_warmup_idx}",
            )

    # Warmup steps exist but no main/subagent steps to compare against
    return CheckResult(
        passed=True, score=1.0,
        evidence=f"{len(warmup_steps)} warmup steps, no main/subagent steps to order against",
    )


TRAINING_PERSONA = PersonaDef(
    name="training",
    description="Training/SFT consumer: evaluates trace utility for supervised fine-tuning pipelines",
    checks=[
        CheckDef(name="T1: Alternating user/agent roles", category="training", weight=1.0, check=_t1_alternating_roles),
        CheckDef(name="T2: Tool call/observation pairing", category="training", weight=1.0, check=_t2_tool_call_observation_pairing),
        CheckDef(name="T3: No dangling observations", category="training", weight=0.9, check=_t3_no_dangling_observations),
        CheckDef(name="T4: System prompts deduplicated", category="training", weight=0.7, check=_t4_system_prompts_deduplicated),
        CheckDef(name="T5: Agent steps have content", category="training", weight=0.8, check=_t5_agent_steps_have_content),
        CheckDef(name="T6: Reasoning coverage >= 80%", category="training", weight=0.8, check=_t6_reasoning_coverage),
        CheckDef(name="T7: Reasoning content present", category="training", weight=0.5, check=_t7_reasoning_content_present),
        CheckDef(name="T8: Task description present", category="training", weight=0.6, check=_t8_task_description_present),
        CheckDef(name="T9: Outcome signals present", category="training", weight=0.7, check=_t9_outcome_signals_present),
        CheckDef(name="T10: Warmup steps labeled", category="training", weight=0.5, check=_t10_warmup_steps_labeled),
    ],
)

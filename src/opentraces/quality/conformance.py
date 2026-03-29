"""Conformance persona: structural checks extracted from test_e2e_dogfood.py.

Each check function takes (record, raw_data) and returns a CheckResult.
The CONFORMANCE_PERSONA assembles them all into a PersonaDef.
score_trace() is the backward-compatible convenience wrapper.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from opentraces_schema import TraceRecord, SCHEMA_VERSION

from .types import (
    CheckDef,
    CheckResult,
    PersonaDef,
    RubricItem,
    RubricReport,
)


# ---------------------------------------------------------------------------
# Schema conformance checks
# ---------------------------------------------------------------------------

def _c1_schema_version(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """S1: schema_version present and correct."""
    passed = record.schema_version == SCHEMA_VERSION
    return CheckResult(
        passed=passed,
        score=1.0 if passed else 0.0,
        evidence=f"schema_version={record.schema_version}",
    )


def _c2_trace_id_format(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """S2: trace_id is a valid UUID-like string."""
    passed = len(record.trace_id) >= 32 and "-" in record.trace_id
    return CheckResult(
        passed=passed,
        score=1.0 if len(record.trace_id) >= 32 else 0.0,
        evidence=f"trace_id={record.trace_id[:12]}...",
    )


def _c3_content_hash(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """S3: content_hash computed and is 64-char hex."""
    has_hash = record.content_hash is not None and len(record.content_hash) == 64
    return CheckResult(
        passed=has_hash,
        score=1.0 if has_hash else 0.0,
        evidence=f"content_hash={'present' if has_hash else 'missing'}",
    )


def _c4_agent_name(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """S4: agent.name is 'claude-code'."""
    passed = record.agent.name == "claude-code"
    return CheckResult(
        passed=passed,
        score=1.0 if passed else 0.0,
        evidence=f"agent.name={record.agent.name}",
    )


def _c5_timestamps(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """S5: timestamps present."""
    has_start = record.timestamp_start is not None
    has_end = record.timestamp_end is not None
    return CheckResult(
        passed=has_start and has_end,
        score=(0.5 if has_start else 0.0) + (0.5 if has_end else 0.0),
        evidence=f"start={'yes' if has_start else 'no'}, end={'yes' if has_end else 'no'}",
    )


# ---------------------------------------------------------------------------
# Parser: step structure checks
# ---------------------------------------------------------------------------

def _c6_step_count(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """P1: has at least 2 steps."""
    step_count = len(record.steps)
    return CheckResult(
        passed=step_count >= 2,
        score=1.0 if step_count >= 2 else 0.0,
        evidence=f"steps={step_count}",
    )


def _c7_step_roles(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """P2: steps use correct role values (user/agent, not human/assistant)."""
    roles = {s.role for s in record.steps}
    bad_roles = roles - {"user", "agent", "system"}
    return CheckResult(
        passed=len(bad_roles) == 0,
        score=1.0 if len(bad_roles) == 0 else 0.0,
        evidence=f"roles={roles}",
    )


def _c8_step_index_monotonic(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """P3: step_index values are unique and monotonically increasing."""
    indices = [s.step_index for s in record.steps]
    unique_indices = len(set(indices)) == len(indices)
    monotonic = all(indices[i] <= indices[i+1] for i in range(len(indices)-1)) if len(indices) > 1 else True
    return CheckResult(
        passed=unique_indices and monotonic,
        score=(0.5 if unique_indices else 0.0) + (0.5 if monotonic else 0.0),
        evidence=f"unique={unique_indices}, monotonic={monotonic}, count={len(indices)}",
    )


def _c9_tool_call_ids(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """P4: tool_calls have tool_call_id."""
    tool_calls = [tc for s in record.steps for tc in s.tool_calls]
    tc_with_id = sum(1 for tc in tool_calls if tc.tool_call_id)
    tc_score = tc_with_id / max(len(tool_calls), 1)
    return CheckResult(
        passed=tc_score == 1.0 or len(tool_calls) == 0,
        score=tc_score,
        evidence=f"{tc_with_id}/{len(tool_calls)} have IDs",
    )


def _c10_observations_linked(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """P5: observations linked to tool_calls via source_call_id."""
    observations = [o for s in record.steps for o in s.observations]
    obs_linked = sum(1 for o in observations if o.source_call_id)
    obs_score = obs_linked / max(len(observations), 1)
    return CheckResult(
        passed=obs_score == 1.0 or len(observations) == 0,
        score=obs_score,
        evidence=f"{obs_linked}/{len(observations)} linked",
    )


def _c11_call_type(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """P6: call_type assigned to agent steps."""
    agent_steps = [s for s in record.steps if s.role == "agent"]
    typed = sum(1 for s in agent_steps if s.call_type in ("main", "subagent", "warmup"))
    type_score = typed / max(len(agent_steps), 1)
    return CheckResult(
        passed=type_score > 0.8,
        score=type_score,
        evidence=f"{typed}/{len(agent_steps)} typed",
    )


def _c12_subagent_parent_step(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """P7: sub-agent steps have parent_step links."""
    subagent_steps = [s for s in record.steps if s.call_type == "subagent"]
    sub_linked = sum(1 for s in subagent_steps if s.parent_step is not None)
    sub_score = sub_linked / max(len(subagent_steps), 1) if subagent_steps else 1.0
    return CheckResult(
        passed=sub_score > 0.8 or len(subagent_steps) == 0,
        score=sub_score,
        evidence=f"{sub_linked}/{len(subagent_steps)} linked" if subagent_steps else "no subagents",
    )


def _c13_token_usage(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """P8: token_usage populated on agent steps."""
    agent_steps = [s for s in record.steps if s.role == "agent"]
    agent_with_tokens = sum(
        1 for s in agent_steps
        if s.token_usage.input_tokens > 0 or s.token_usage.output_tokens > 0
        or s.token_usage.cache_read_tokens > 0
    )
    token_score = agent_with_tokens / max(len(agent_steps), 1)
    return CheckResult(
        passed=token_score > 0.5,
        score=token_score,
        evidence=f"{agent_with_tokens}/{len(agent_steps)} have tokens",
    )


def _c14_snippets(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """P9: snippets extracted (at least some)."""
    total_snippets = sum(len(s.snippets) for s in record.steps)
    return CheckResult(
        passed=total_snippets > 0,
        score=min(total_snippets / 5.0, 1.0),
        evidence=f"{total_snippets} snippets",
    )


def _c15_task_description(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """P10: task.description populated from first user message."""
    return CheckResult(
        passed=bool(record.task.description),
        score=1.0 if record.task.description else 0.0,
        evidence=f"{'present' if record.task.description else 'missing'}",
    )


def _c16_reasoning_content(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """P11: reasoning_content captured from thinking blocks."""
    reasoning_steps = sum(1 for s in record.steps if s.reasoning_content)
    return CheckResult(
        passed=reasoning_steps > 0,
        score=min(reasoning_steps / 3.0, 1.0),
        evidence=f"{reasoning_steps} steps with reasoning",
    )


# ---------------------------------------------------------------------------
# Enrichment checks
# ---------------------------------------------------------------------------

def _c17_vcs_type(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """E1: environment.vcs populated."""
    has_vcs = record.environment.vcs.type in ("git", "none")
    return CheckResult(
        passed=has_vcs,
        score=1.0 if has_vcs else 0.0,
        evidence=f"vcs.type={record.environment.vcs.type}",
    )


def _c18_metrics_total_steps(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """E2: metrics computed."""
    has_metrics = record.metrics.total_steps > 0
    return CheckResult(
        passed=has_metrics,
        score=1.0 if has_metrics else 0.0,
        evidence=f"total_steps={record.metrics.total_steps}",
    )


def _c19_cache_hit_rate(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """E3: cache_hit_rate computed."""
    has_cache = record.metrics.cache_hit_rate is not None
    return CheckResult(
        passed=has_cache,
        score=1.0 if has_cache else 0.0,
        evidence=f"cache_hit_rate={record.metrics.cache_hit_rate}",
    )


def _c20_estimated_cost(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """E4: estimated_cost_usd computed."""
    has_cost = record.metrics.estimated_cost_usd is not None and record.metrics.estimated_cost_usd > 0
    return CheckResult(
        passed=has_cost,
        score=1.0 if has_cost else 0.0,
        evidence=f"cost=${record.metrics.estimated_cost_usd}" if has_cost else "missing",
    )


def _c21_duration(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """E5: total_duration_s computed."""
    has_duration = record.metrics.total_duration_s is not None and record.metrics.total_duration_s > 0
    return CheckResult(
        passed=has_duration,
        score=1.0 if has_duration else 0.0,
        evidence=f"duration={record.metrics.total_duration_s}s" if has_duration else "missing",
    )


def _c22_outcome_signal(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """E6: outcome signals present."""
    passed = record.outcome.signal_confidence in ("derived", "inferred", "annotated")
    return CheckResult(
        passed=passed,
        score=1.0 if passed else 0.0,
        evidence=f"signal_confidence={record.outcome.signal_confidence}",
    )


# ---------------------------------------------------------------------------
# Security checks
# ---------------------------------------------------------------------------

def _c23_security_scanned(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """SEC1: security scan was applied."""
    passed = record.security.scanned
    return CheckResult(
        passed=passed,
        score=1.0 if passed else 0.0,
        evidence=f"scanned={record.security.scanned}",
    )


def _c24_no_secrets(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """SEC2: no raw secrets in output (spot check common patterns)."""
    serialized = json.dumps(raw_data)
    actual_secrets = []
    for pattern, label in [
        (r"sk-ant-api\w{20,}", "anthropic_key"),
        (r"sk-proj-\w{20,}", "openai_key"),
        (r"ghp_[A-Za-z0-9]{36,}", "github_token"),
        (r"AKIA[A-Z0-9]{16}", "aws_key"),
        (r"-----BEGIN\s+\w+\s+PRIVATE\s+KEY-----", "private_key"),
    ]:
        if re.search(pattern, serialized):
            actual_secrets.append(label)
    return CheckResult(
        passed=len(actual_secrets) == 0,
        score=1.0 if len(actual_secrets) == 0 else 0.0,
        evidence=f"{'clean' if not actual_secrets else f'FOUND: {actual_secrets}'}",
    )


def _c25_paths_anonymized(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """SEC3: paths anonymized (no raw /Users/<username>/)."""
    serialized = json.dumps(raw_data)
    username = os.environ.get("USER", "")
    has_raw_path = f"/Users/{username}/" in serialized if username else False
    return CheckResult(
        passed=not has_raw_path,
        score=1.0 if not has_raw_path else 0.0,
        evidence=f"{'anonymized' if not has_raw_path else 'RAW PATHS FOUND'}",
        note="Paths should be stripped to project-relative" if has_raw_path else "",
    )


# ---------------------------------------------------------------------------
# Structure checks
# ---------------------------------------------------------------------------

def _c26_jsonl_valid(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """ST1: JSONL serialization is valid single-line JSON."""
    jsonl_line = record.to_jsonl_line()
    is_single_line = "\n" not in jsonl_line
    is_valid_json = True
    try:
        json.loads(jsonl_line)
    except json.JSONDecodeError:
        is_valid_json = False
    return CheckResult(
        passed=is_single_line and is_valid_json,
        score=(0.5 if is_single_line else 0.0) + (0.5 if is_valid_json else 0.0),
        evidence=f"single_line={is_single_line}, valid_json={is_valid_json}",
    )


def _c27_hash_deterministic(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """ST2: content_hash is deterministic (same content = same hash)."""
    hash1 = record.compute_content_hash()
    hash2 = record.compute_content_hash()
    return CheckResult(
        passed=hash1 == hash2,
        score=1.0 if hash1 == hash2 else 0.0,
        evidence=f"hash1==hash2: {hash1 == hash2}",
    )


def _c28_system_prompts_deduped(record: TraceRecord, raw_data: dict | None) -> CheckResult:
    """ST3: system_prompts deduplicated and references valid."""
    sp_keys = set(record.system_prompts.keys())

    # Collect all system_prompt_hash references from steps
    refs = []
    for step in record.steps:
        h = getattr(step, "system_prompt_hash", None)
        if h:
            refs.append(h)

    if not refs:
        # No steps reference system prompts, dedup not applicable
        return CheckResult(
            passed=True, score=1.0,
            evidence=f"{len(sp_keys)} system prompts, no step references (dedup N/A)",
        )

    valid = sum(1 for r in refs if r in sp_keys)
    ratio = valid / len(refs)
    passed = ratio >= 1.0
    return CheckResult(
        passed=passed,
        score=round(ratio, 3),
        evidence=f"{valid}/{len(refs)} system_prompt_hash references resolve to system_prompts entries",
    )


# ---------------------------------------------------------------------------
# Conformance persona: all checks assembled
# ---------------------------------------------------------------------------

CONFORMANCE_PERSONA = PersonaDef(
    name="conformance",
    description="Structural conformance checks derived from the opentraces schema spec.",
    checks=[
        # Schema
        CheckDef("schema_version present", "schema", 1.0, _c1_schema_version),
        CheckDef("trace_id is UUID format", "schema", 1.0, _c2_trace_id_format),
        CheckDef("content_hash is SHA-256", "schema", 1.0, _c3_content_hash),
        CheckDef("agent.name = claude-code", "schema", 1.0, _c4_agent_name),
        CheckDef("timestamps present", "schema", 0.8, _c5_timestamps),
        # Parser
        CheckDef("step count >= 2", "parser", 1.0, _c6_step_count),
        CheckDef("step roles are user/agent (not human/assistant)", "parser", 1.0, _c7_step_roles),
        CheckDef("step_index unique and monotonic", "parser", 1.0, _c8_step_index_monotonic),
        CheckDef("tool_calls have tool_call_id", "parser", 0.9, _c9_tool_call_ids),
        CheckDef("observations linked via source_call_id", "parser", 0.9, _c10_observations_linked),
        CheckDef("agent steps have call_type", "parser", 0.8, _c11_call_type),
        CheckDef("subagent steps have parent_step", "parser", 0.8, _c12_subagent_parent_step),
        CheckDef("agent steps have token_usage", "parser", 0.9, _c13_token_usage),
        CheckDef("snippets extracted from tool results", "parser", 0.6, _c14_snippets),
        CheckDef("task.description from first user message", "parser", 0.7, _c15_task_description),
        CheckDef("reasoning_content from thinking blocks", "parser", 0.6, _c16_reasoning_content),
        # Enrichment
        CheckDef("environment.vcs type discriminator set", "enrichment", 0.8, _c17_vcs_type),
        CheckDef("metrics.total_steps > 0", "enrichment", 0.9, _c18_metrics_total_steps),
        CheckDef("metrics.cache_hit_rate computed", "enrichment", 0.7, _c19_cache_hit_rate),
        CheckDef("metrics.estimated_cost_usd > 0", "enrichment", 0.7, _c20_estimated_cost),
        CheckDef("metrics.total_duration_s > 0", "enrichment", 0.6, _c21_duration),
        CheckDef("outcome.signal_confidence set", "enrichment", 0.7, _c22_outcome_signal),
        # Security
        CheckDef("security.scanned", "security", 0.8, _c23_security_scanned),
        CheckDef("no real secrets in serialized output", "security", 1.0, _c24_no_secrets),
        CheckDef("paths anonymized (no raw /Users/<name>/)", "security", 0.8, _c25_paths_anonymized),
        # Structure
        CheckDef("JSONL output is valid single-line JSON", "structure", 1.0, _c26_jsonl_valid),
        CheckDef("content_hash is deterministic", "structure", 0.8, _c27_hash_deterministic),
        CheckDef("system_prompts deduplicated to top-level map", "structure", 0.5, _c28_system_prompts_deduped),
    ],
)


def score_trace(record: TraceRecord, raw_data: dict | None) -> RubricReport:
    """Score a parsed trace against the full conformance rubric.

    Backward-compatible convenience wrapper that runs the conformance persona
    and returns a RubricReport.

    Args:
        record: The parsed TraceRecord.
        raw_data: The serialized dict (from to_jsonl_line).

    Returns:
        RubricReport with all rubric items scored.
    """
    report = RubricReport(
        trace_id=record.trace_id,
        session_id=record.session_id,
        task_description=(record.task.description or "")[:100],
    )

    for check_def in CONFORMANCE_PERSONA.checks:
        result = check_def.check(record, raw_data)
        report.items.append(RubricItem(
            name=check_def.name,
            category=check_def.category,
            weight=check_def.weight,
            passed=result.passed,
            score=result.score,
            evidence=result.evidence,
            note=result.note,
        ))

    return report

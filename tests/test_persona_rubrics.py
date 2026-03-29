"""Tests for persona rubrics (Unit 4).

Validates that each persona's checks produce correct scores
for synthetic traces with known characteristics.
"""

from __future__ import annotations

import json

from opentraces_schema.models import (
    Agent,
    Attribution,
    AttributionConversation,
    AttributionFile,
    AttributionRange,
    Environment,
    Metrics,
    Observation,
    Outcome,
    Snippet,
    Step,
    Task,
    TokenUsage,
    ToolCall,
    TraceRecord,
    VCS,
)
from opentraces.quality.types import CheckResult, PersonaDef
from opentraces.quality.personas import (
    ALL_PERSONAS,
    ANALYTICS_PERSONA,
    DOMAIN_PERSONA,
    RL_PERSONA,
    TRAINING_PERSONA,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_persona(persona: PersonaDef, record: TraceRecord) -> dict[str, CheckResult]:
    """Run all checks for a persona and return results keyed by check name."""
    raw_data = json.loads(record.to_jsonl_line())
    results = {}
    for check_def in persona.checks:
        results[check_def.name] = check_def.check(record, raw_data)
    return results


def _persona_score(persona: PersonaDef, record: TraceRecord) -> float:
    """Compute weighted score for a persona on a record (0-100)."""
    results = _run_persona(persona, record)
    total_weight = sum(c.weight for c in persona.checks)
    if total_weight == 0:
        return 0.0
    weighted = sum(
        results[c.name].score * c.weight
        for c in persona.checks
    )
    return round(weighted / total_weight * 100, 1)


def _make_agent() -> Agent:
    return Agent(name="claude-code", version="1.0.42", model="anthropic/claude-sonnet-4-20250514")


def _make_rich_trace() -> TraceRecord:
    """Create a trace with most fields populated for high scores."""
    steps = []
    # Step 0: user
    steps.append(Step(
        step_index=0,
        role="user",
        content="Please fix the bug in auth.py",
        timestamp="2026-03-28T10:00:00Z",
        call_type="main",
    ))
    # Step 1: agent with tool call + reasoning
    steps.append(Step(
        step_index=1,
        role="agent",
        content="I will read the file first.",
        reasoning_content="Let me analyze the auth module to find the bug.",
        model="anthropic/claude-sonnet-4-20250514",
        timestamp="2026-03-28T10:00:05Z",
        call_type="main",
        tool_calls=[
            ToolCall(tool_call_id="tc1", tool_name="Read", input={"file": "auth.py"}),
        ],
        token_usage=TokenUsage(input_tokens=500, output_tokens=200, cache_read_tokens=100),
        snippets=[
            Snippet(file_path="auth.py", language="python", start_line=1, end_line=10, text="def login():"),
        ],
    ))
    # Step 2: user (observation step)
    steps.append(Step(
        step_index=2,
        role="user",
        content="",
        timestamp="2026-03-28T10:00:06Z",
        call_type="main",
        observations=[
            Observation(source_call_id="tc1", content="def login():\n    pass"),
        ],
    ))
    # Step 3: agent with edit tool call + reasoning
    steps.append(Step(
        step_index=3,
        role="agent",
        content="I found the bug. Fixing now.",
        reasoning_content="The login function is missing validation. I need to add it.",
        model="anthropic/claude-sonnet-4-20250514",
        timestamp="2026-03-28T10:00:10Z",
        call_type="main",
        tool_calls=[
            ToolCall(tool_call_id="tc2", tool_name="Edit", input={"file": "auth.py", "content": "fixed"}),
        ],
        token_usage=TokenUsage(input_tokens=600, output_tokens=300, cache_read_tokens=200),
    ))
    # Step 4: user (observation for edit)
    steps.append(Step(
        step_index=4,
        role="user",
        content="",
        timestamp="2026-03-28T10:00:11Z",
        call_type="main",
        observations=[
            Observation(source_call_id="tc2", content="File edited successfully"),
        ],
    ))

    return TraceRecord(
        trace_id="test-rich-001",
        session_id="sess-rich-001",
        timestamp_start="2026-03-28T10:00:00Z",
        timestamp_end="2026-03-28T10:01:00Z",
        task=Task(description="Fix authentication bug in auth.py module", source="user_prompt", repository="owner/repo", base_commit="abc123"),
        agent=_make_agent(),
        environment=Environment(
            os="Darwin 24.6.0",
            shell="zsh",
            vcs=VCS(type="git", branch="main", base_commit="abc123"),
            language_ecosystem=["python"],
        ),
        system_prompts={"hash1": "You are Claude Code..."},
        steps=steps,
        outcome=Outcome(
            committed=True,
            success=True,
            signal_confidence="derived",
            patch="--- a/auth.py\n+++ b/auth.py\n@@ -1 +1 @@\n-pass\n+validate()",
            commit_sha="def456",
        ),
        dependencies=["flask", "pyjwt"],
        metrics=Metrics(
            total_steps=5,
            total_input_tokens=1100,
            total_output_tokens=500,
            total_duration_s=60.0,
            cache_hit_rate=0.45,
            estimated_cost_usd=0.012,
        ),
        attribution=Attribution(
            files=[
                AttributionFile(
                    path="auth.py",
                    conversations=[
                        AttributionConversation(
                            contributor={"type": "ai", "model_id": "anthropic/claude-sonnet-4-20250514"},
                            ranges=[AttributionRange(start_line=1, end_line=5)],
                        )
                    ],
                )
            ]
        ),
    )


def _make_no_commit_trace() -> TraceRecord:
    """Create a trace with no commits, good analytics but low RL."""
    steps = [
        Step(
            step_index=0,
            role="user",
            content="What does this codebase do?",
            timestamp="2026-03-28T11:00:00Z",
            call_type="main",
        ),
        Step(
            step_index=1,
            role="agent",
            content="Let me explore the codebase.",
            reasoning_content="I will read the main files to understand the architecture.",
            model="anthropic/claude-sonnet-4-20250514",
            timestamp="2026-03-28T11:00:05Z",
            call_type="main",
            tool_calls=[
                ToolCall(tool_call_id="tc1", tool_name="Read", input={"file": "README.md"}),
            ],
            token_usage=TokenUsage(input_tokens=400, output_tokens=150, cache_read_tokens=50),
        ),
        Step(
            step_index=2,
            role="user",
            content="",
            timestamp="2026-03-28T11:00:06Z",
            call_type="main",
            observations=[
                Observation(source_call_id="tc1", content="# My Project\nA web app"),
            ],
        ),
        Step(
            step_index=3,
            role="agent",
            content="This is a web application project. The README describes...",
            reasoning_content="Based on the README, this appears to be a web app.",
            model="anthropic/claude-sonnet-4-20250514",
            timestamp="2026-03-28T11:00:10Z",
            call_type="main",
            token_usage=TokenUsage(input_tokens=500, output_tokens=200, cache_read_tokens=100),
        ),
    ]

    return TraceRecord(
        trace_id="test-nocommit-001",
        session_id="sess-nocommit-001",
        timestamp_start="2026-03-28T11:00:00Z",
        timestamp_end="2026-03-28T11:01:00Z",
        task=Task(description="Explore and understand the codebase architecture", source="user_prompt"),
        agent=_make_agent(),
        environment=Environment(
            os="Darwin 24.6.0",
            shell="zsh",
            vcs=VCS(type="git", branch="main"),
            language_ecosystem=["python", "javascript"],
        ),
        system_prompts={"hash1": "You are Claude Code..."},
        steps=steps,
        outcome=Outcome(committed=False, signal_confidence="derived"),
        dependencies=["django", "react"],
        metrics=Metrics(
            total_steps=4,
            total_input_tokens=900,
            total_output_tokens=350,
            total_duration_s=70.0,
            cache_hit_rate=0.30,
            estimated_cost_usd=0.008,
        ),
    )


def _make_minimal_trace() -> TraceRecord:
    """Create a trace with minimal fields for edge case testing."""
    return TraceRecord(
        trace_id="test-minimal-001",
        session_id="sess-minimal-001",
        agent=Agent(name="claude-code"),
        steps=[],
        outcome=Outcome(),
        metrics=Metrics(),
    )


def _make_empty_steps_trace() -> TraceRecord:
    """Create a trace with empty agent steps (no content, no tool calls)."""
    steps = [
        Step(step_index=0, role="user", content="Hello"),
        Step(step_index=1, role="agent", content=""),
        Step(step_index=2, role="user", content="Do something"),
        Step(step_index=3, role="agent", content="", tool_calls=[]),
    ]
    return TraceRecord(
        trace_id="test-empty-steps-001",
        session_id="sess-empty-steps-001",
        agent=Agent(name="claude-code"),
        steps=steps,
        outcome=Outcome(),
        metrics=Metrics(total_steps=4),
    )


def _make_no_deps_trace() -> TraceRecord:
    """Create a trace with language_ecosystem but no dependencies."""
    return TraceRecord(
        trace_id="test-nodeps-001",
        session_id="sess-nodeps-001",
        agent=Agent(name="claude-code"),
        environment=Environment(language_ecosystem=["python"]),
        steps=[
            Step(step_index=0, role="user", content="Fix the code"),
            Step(step_index=1, role="agent", content="Done"),
        ],
        outcome=Outcome(),
        dependencies=[],
        metrics=Metrics(total_steps=2),
    )


# ---------------------------------------------------------------------------
# Test: ALL_PERSONAS exports
# ---------------------------------------------------------------------------

class TestPersonaExports:
    def test_all_personas_has_four(self):
        assert len(ALL_PERSONAS) == 4

    def test_all_personas_names(self):
        names = {p.name for p in ALL_PERSONAS}
        assert names == {"training", "rl", "analytics", "domain"}

    def test_each_persona_has_checks(self):
        for persona in ALL_PERSONAS:
            assert len(persona.checks) >= 5, f"{persona.name} has too few checks"

    def test_persona_constants_match_all(self):
        assert TRAINING_PERSONA in ALL_PERSONAS
        assert RL_PERSONA in ALL_PERSONAS
        assert ANALYTICS_PERSONA in ALL_PERSONAS
        assert DOMAIN_PERSONA in ALL_PERSONAS


# ---------------------------------------------------------------------------
# Test: Training persona
# ---------------------------------------------------------------------------

class TestTrainingPersona:
    def test_rich_trace_scores_high(self):
        record = _make_rich_trace()
        score = _persona_score(TRAINING_PERSONA, record)
        assert score >= 80.0, f"Rich trace should score >=80 on training, got {score}"

    def test_t1_alternating_roles(self):
        record = _make_rich_trace()
        results = _run_persona(TRAINING_PERSONA, record)
        assert results["T1: Alternating user/agent roles"].passed

    def test_t2_tool_call_pairing(self):
        record = _make_rich_trace()
        results = _run_persona(TRAINING_PERSONA, record)
        assert results["T2: Tool call/observation pairing"].passed

    def test_t3_no_dangling_observations(self):
        record = _make_rich_trace()
        results = _run_persona(TRAINING_PERSONA, record)
        assert results["T3: No dangling observations"].passed

    def test_t4_system_prompts(self):
        record = _make_rich_trace()
        results = _run_persona(TRAINING_PERSONA, record)
        assert results["T4: System prompts deduplicated"].passed

    def test_t5_agent_content(self):
        record = _make_rich_trace()
        results = _run_persona(TRAINING_PERSONA, record)
        assert results["T5: Agent steps have content"].passed

    def test_t6_reasoning_coverage(self):
        record = _make_rich_trace()
        results = _run_persona(TRAINING_PERSONA, record)
        r = results["T6: Reasoning coverage >= 80%"]
        assert r.passed
        assert r.score >= 0.8

    def test_t7_reasoning_present(self):
        record = _make_rich_trace()
        results = _run_persona(TRAINING_PERSONA, record)
        assert results["T7: Reasoning content present"].passed

    def test_t8_task_description(self):
        record = _make_rich_trace()
        results = _run_persona(TRAINING_PERSONA, record)
        assert results["T8: Task description present"].passed

    def test_t9_outcome_signals(self):
        record = _make_rich_trace()
        results = _run_persona(TRAINING_PERSONA, record)
        assert results["T9: Outcome signals present"].passed

    def test_t10_warmup_labeled(self):
        record = _make_rich_trace()
        results = _run_persona(TRAINING_PERSONA, record)
        assert results["T10: Warmup steps labeled"].passed

    def test_empty_steps_trace(self):
        record = _make_empty_steps_trace()
        results = _run_persona(TRAINING_PERSONA, record)
        # T5 should fail: empty agent steps
        assert not results["T5: Agent steps have content"].passed

    def test_minimal_trace_handles_gracefully(self):
        record = _make_minimal_trace()
        # Should not raise
        score = _persona_score(TRAINING_PERSONA, record)
        assert 0.0 <= score <= 100.0

    def test_t1_consecutive_same_role_fails(self):
        """Two consecutive agent steps should lower the score."""
        steps = [
            Step(step_index=0, role="user", content="Hello"),
            Step(step_index=1, role="agent", content="Hi"),
            Step(step_index=2, role="agent", content="More"),  # violation
            Step(step_index=3, role="user", content="OK"),
        ]
        record = TraceRecord(
            trace_id="t", session_id="s",
            agent=Agent(name="claude-code"),
            steps=steps,
        )
        results = _run_persona(TRAINING_PERSONA, record)
        r = results["T1: Alternating user/agent roles"]
        assert r.score < 1.0

    def test_t2_unmatched_tool_call(self):
        """Tool call without observation should lower pairing score."""
        steps = [
            Step(
                step_index=0, role="agent", content="x",
                tool_calls=[ToolCall(tool_call_id="tc_orphan", tool_name="Read", input={})],
            ),
        ]
        record = TraceRecord(
            trace_id="t", session_id="s",
            agent=Agent(name="claude-code"),
            steps=steps,
        )
        results = _run_persona(TRAINING_PERSONA, record)
        assert not results["T2: Tool call/observation pairing"].passed

    def test_t7_encrypted_reasoning_partial_credit(self):
        """Encrypted reasoning should give 0.5 credit."""
        steps = [
            Step(step_index=0, role="user", content="Hello"),
            Step(
                step_index=1, role="agent", content="Hi",
                reasoning_content="[encrypted thinking block]",
            ),
        ]
        record = TraceRecord(
            trace_id="t", session_id="s",
            agent=Agent(name="claude-code"),
            steps=steps,
        )
        results = _run_persona(TRAINING_PERSONA, record)
        r = results["T7: Reasoning content present"]
        assert r.passed
        assert 0.4 <= r.score <= 0.6  # 0.5 partial credit


# ---------------------------------------------------------------------------
# Test: RL persona
# ---------------------------------------------------------------------------

class TestRLPersona:
    def test_rich_trace_scores_high(self):
        record = _make_rich_trace()
        score = _persona_score(RL_PERSONA, record)
        assert score >= 70.0, f"Rich trace should score >=70 on RL, got {score}"

    def test_no_commit_trace_scores_low(self):
        record = _make_no_commit_trace()
        score = _persona_score(RL_PERSONA, record)
        assert score < 60.0, f"No-commit trace should score <60 on RL, got {score}"

    def test_rl1_committed(self):
        record = _make_rich_trace()
        results = _run_persona(RL_PERSONA, record)
        assert results["RL1: Committed explicitly set"].passed

    def test_rl1_not_committed(self):
        record = _make_no_commit_trace()
        results = _run_persona(RL_PERSONA, record)
        assert not results["RL1: Committed explicitly set"].passed

    def test_rl3_patch_when_committed(self):
        record = _make_rich_trace()
        results = _run_persona(RL_PERSONA, record)
        assert results["RL3: Patch when committed"].passed

    def test_rl3_no_patch_needed_when_not_committed(self):
        record = _make_no_commit_trace()
        results = _run_persona(RL_PERSONA, record)
        r = results["RL3: Patch when committed"]
        assert r.passed  # N/A

    def test_rl4_token_usage(self):
        record = _make_rich_trace()
        results = _run_persona(RL_PERSONA, record)
        assert results["RL4: Per-step token usage"].passed

    def test_rl6_no_subagents_passes(self):
        record = _make_rich_trace()
        results = _run_persona(RL_PERSONA, record)
        assert results["RL6: Sub-agent hierarchy"].passed

    def test_rl7_success_set(self):
        record = _make_rich_trace()
        results = _run_persona(RL_PERSONA, record)
        assert results["RL7: Outcome success set"].passed

    def test_rl7_success_not_set(self):
        record = _make_no_commit_trace()
        results = _run_persona(RL_PERSONA, record)
        assert not results["RL7: Outcome success set"].passed

    def test_minimal_trace_handles_gracefully(self):
        record = _make_minimal_trace()
        score = _persona_score(RL_PERSONA, record)
        assert 0.0 <= score <= 100.0

    def test_rl6_subagent_without_parent(self):
        """Subagent steps without parent_step should fail RL6."""
        steps = [
            Step(step_index=0, role="user", content="Hello"),
            Step(step_index=1, role="agent", content="x", call_type="subagent"),
        ]
        record = TraceRecord(
            trace_id="t", session_id="s",
            agent=Agent(name="claude-code"),
            steps=steps,
        )
        results = _run_persona(RL_PERSONA, record)
        assert not results["RL6: Sub-agent hierarchy"].passed


# ---------------------------------------------------------------------------
# Test: Analytics persona
# ---------------------------------------------------------------------------

class TestAnalyticsPersona:
    def test_rich_trace_scores_high(self):
        record = _make_rich_trace()
        score = _persona_score(ANALYTICS_PERSONA, record)
        assert score >= 75.0, f"Rich trace should score >=75 on analytics, got {score}"

    def test_no_commit_trace_still_scores_well(self):
        """Analytics doesn't care about commits, should still score well."""
        record = _make_no_commit_trace()
        score = _persona_score(ANALYTICS_PERSONA, record)
        assert score >= 60.0, f"No-commit trace should score >=60 on analytics, got {score}"

    def test_a1_cache_hit_rate(self):
        record = _make_rich_trace()
        results = _run_persona(ANALYTICS_PERSONA, record)
        assert results["A1: Cache hit rate computed"].passed

    def test_a1_no_cache_rate(self):
        record = _make_minimal_trace()
        results = _run_persona(ANALYTICS_PERSONA, record)
        assert not results["A1: Cache hit rate computed"].passed

    def test_a2_cost(self):
        record = _make_rich_trace()
        results = _run_persona(ANALYTICS_PERSONA, record)
        assert results["A2: Estimated cost > 0"].passed

    def test_a3_duration(self):
        record = _make_rich_trace()
        results = _run_persona(ANALYTICS_PERSONA, record)
        assert results["A3: Total duration > 0"].passed

    def test_a4_timestamps(self):
        record = _make_rich_trace()
        results = _run_persona(ANALYTICS_PERSONA, record)
        assert results["A4: Step timestamps"].passed

    def test_a5_token_breakdown(self):
        record = _make_rich_trace()
        results = _run_persona(ANALYTICS_PERSONA, record)
        assert results["A5: Token breakdown per step"].passed

    def test_a7_total_steps_consistent(self):
        record = _make_rich_trace()
        results = _run_persona(ANALYTICS_PERSONA, record)
        assert results["A7: Total steps consistent"].passed

    def test_a7_inconsistent_count(self):
        record = _make_rich_trace()
        record.metrics.total_steps = 999
        results = _run_persona(ANALYTICS_PERSONA, record)
        assert not results["A7: Total steps consistent"].passed

    def test_minimal_trace_handles_gracefully(self):
        record = _make_minimal_trace()
        score = _persona_score(ANALYTICS_PERSONA, record)
        assert 0.0 <= score <= 100.0


# ---------------------------------------------------------------------------
# Test: Domain persona
# ---------------------------------------------------------------------------

class TestDomainPersona:
    def test_rich_trace_scores_high(self):
        record = _make_rich_trace()
        score = _persona_score(DOMAIN_PERSONA, record)
        assert score >= 80.0, f"Rich trace should score >=80 on domain, got {score}"

    def test_d1_language_ecosystem(self):
        record = _make_rich_trace()
        results = _run_persona(DOMAIN_PERSONA, record)
        assert results["D1: Language ecosystem populated"].passed

    def test_d1_no_languages(self):
        record = _make_minimal_trace()
        results = _run_persona(DOMAIN_PERSONA, record)
        assert not results["D1: Language ecosystem populated"].passed

    def test_d2_dependencies(self):
        record = _make_rich_trace()
        results = _run_persona(DOMAIN_PERSONA, record)
        assert results["D2: Dependencies extracted"].passed

    def test_d2_no_deps_with_language(self):
        """Language ecosystem present but no dependencies should fail D2."""
        record = _make_no_deps_trace()
        results = _run_persona(DOMAIN_PERSONA, record)
        assert not results["D2: Dependencies extracted"].passed

    def test_d2_no_deps_no_language(self):
        """No language ecosystem and no dependencies is acceptable."""
        record = _make_minimal_trace()
        results = _run_persona(DOMAIN_PERSONA, record)
        assert results["D2: Dependencies extracted"].passed  # N/A

    def test_d3_task_meaningful(self):
        record = _make_rich_trace()
        results = _run_persona(DOMAIN_PERSONA, record)
        assert results["D3: Task description meaningful"].passed

    def test_d3_short_description(self):
        """Very short task description gets partial credit."""
        record = _make_minimal_trace()
        record.task.description = "Fix bug"
        results = _run_persona(DOMAIN_PERSONA, record)
        r = results["D3: Task description meaningful"]
        assert not r.passed
        assert r.score == 0.5  # partial credit for short

    def test_d4_vcs_info(self):
        record = _make_rich_trace()
        results = _run_persona(DOMAIN_PERSONA, record)
        assert results["D4: VCS info populated"].passed

    def test_d5_snippets_with_language(self):
        record = _make_rich_trace()
        results = _run_persona(DOMAIN_PERSONA, record)
        assert results["D5: Snippets with language tags"].passed

    def test_d6_attribution_when_edits(self):
        record = _make_rich_trace()
        results = _run_persona(DOMAIN_PERSONA, record)
        assert results["D6: Attribution when edits exist"].passed

    def test_d6_no_edits_passes(self):
        """No edits means attribution is not expected."""
        record = _make_no_commit_trace()
        results = _run_persona(DOMAIN_PERSONA, record)
        assert results["D6: Attribution when edits exist"].passed  # N/A

    def test_d7_agent_name_version(self):
        record = _make_rich_trace()
        results = _run_persona(DOMAIN_PERSONA, record)
        assert results["D7: Agent name + version"].passed

    def test_d7_name_only(self):
        """Agent with name but no version gets partial credit."""
        record = _make_minimal_trace()
        results = _run_persona(DOMAIN_PERSONA, record)
        r = results["D7: Agent name + version"]
        assert not r.passed
        assert r.score == 0.5

    def test_d8_environment_os(self):
        record = _make_rich_trace()
        results = _run_persona(DOMAIN_PERSONA, record)
        assert results["D8: Environment OS populated"].passed

    def test_d8_no_os(self):
        """D8 passes as N/A when OS is empty (not yet implemented in parser)."""
        record = _make_minimal_trace()
        results = _run_persona(DOMAIN_PERSONA, record)
        assert results["D8: Environment OS populated"].passed
        assert results["D8: Environment OS populated"].score == 1.0
        assert "not yet implemented" in results["D8: Environment OS populated"].evidence

    def test_minimal_trace_handles_gracefully(self):
        record = _make_minimal_trace()
        score = _persona_score(DOMAIN_PERSONA, record)
        assert 0.0 <= score <= 100.0

    def test_no_deps_trace_low_domain_score(self):
        """Trace with languages but no deps should score lower on domain."""
        record = _make_no_deps_trace()
        score = _persona_score(DOMAIN_PERSONA, record)
        rich_score = _persona_score(DOMAIN_PERSONA, _make_rich_trace())
        assert score < rich_score


# ---------------------------------------------------------------------------
# Test: Cross-persona divergence
# ---------------------------------------------------------------------------

class TestCrossPersonaDivergence:
    def test_no_commit_trace_low_rl_decent_analytics(self):
        """No-commit trace: low RL, decent analytics (expected divergence)."""
        record = _make_no_commit_trace()
        rl_score = _persona_score(RL_PERSONA, record)
        analytics_score = _persona_score(ANALYTICS_PERSONA, record)
        assert analytics_score > rl_score, (
            f"Analytics ({analytics_score}) should exceed RL ({rl_score}) for no-commit trace"
        )

    def test_rich_trace_all_personas_above_minimum(self):
        """Rich trace should score reasonably on all personas."""
        record = _make_rich_trace()
        for persona in ALL_PERSONAS:
            score = _persona_score(persona, record)
            assert score >= 50.0, f"{persona.name} scored {score} on rich trace, expected >=50"

    def test_minimal_trace_scores_vary(self):
        """Minimal trace should produce varying scores across personas."""
        record = _make_minimal_trace()
        scores = {p.name: _persona_score(p, record) for p in ALL_PERSONAS}
        # All should be valid numbers
        for name, score in scores.items():
            assert 0.0 <= score <= 100.0, f"{name}: {score}"

    def test_no_deps_trace_low_domain_ok_training(self):
        """Trace with languages but no deps: lower domain, training unaffected."""
        record = _make_no_deps_trace()
        domain_score = _persona_score(DOMAIN_PERSONA, record)
        training_score = _persona_score(TRAINING_PERSONA, record)
        # Domain should be lower than training for this case
        # (training doesn't care about deps)
        assert domain_score < training_score or domain_score < 50.0


# ---------------------------------------------------------------------------
# Test: Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_none_raw_data_accepted(self):
        """All checks should accept raw_data=None without crashing."""
        record = _make_rich_trace()
        for persona in ALL_PERSONAS:
            for check_def in persona.checks:
                result = check_def.check(record, None)
                assert isinstance(result, CheckResult)

    def test_empty_steps_all_personas(self):
        """Trace with no steps should not crash any persona."""
        record = _make_minimal_trace()
        for persona in ALL_PERSONAS:
            results = _run_persona(persona, record)
            for name, result in results.items():
                assert isinstance(result, CheckResult), f"{name} returned {type(result)}"

    def test_check_result_fields(self):
        """All check results should have required fields."""
        record = _make_rich_trace()
        for persona in ALL_PERSONAS:
            results = _run_persona(persona, record)
            for name, result in results.items():
                assert isinstance(result.passed, bool), f"{name}: passed not bool"
                assert isinstance(result.score, (int, float)), f"{name}: score not numeric"
                assert 0.0 <= result.score <= 1.0, f"{name}: score={result.score} out of range"
                assert isinstance(result.evidence, str), f"{name}: evidence not str"

    def test_persona_check_counts(self):
        """Verify expected check counts per persona."""
        assert len(TRAINING_PERSONA.checks) == 10
        assert len(RL_PERSONA.checks) == 8
        assert len(ANALYTICS_PERSONA.checks) == 8
        assert len(DOMAIN_PERSONA.checks) == 8

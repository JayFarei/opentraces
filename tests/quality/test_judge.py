"""Tests for the LLM judge module: brief loading, trace summarization, and judge logic."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from opentraces_schema.models import (
    Agent,
    Attribution,
    AttributionFile,
    Environment,
    Metrics,
    Observation,
    Outcome,
    Step,
    Task,
    TokenUsage,
    ToolCall,
    TraceRecord,
    VCS,
)

from opentraces.quality.judge import (
    JudgeDimension,
    PersonaBrief,
    _compute_judge_overall,
    _parse_judge_response,
    _select_representative_steps,
    _truncate,
    load_brief,
    run_judge,
    summarize_for_judge,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_rich_trace() -> TraceRecord:
    """A rich trace with multiple steps, tool calls, reasoning, and outcome."""
    return TraceRecord(
        trace_id="aaaa-bbbb-cccc-dddd",
        session_id="sess-1234",
        timestamp_start="2026-03-28T10:00:00Z",
        timestamp_end="2026-03-28T10:05:00Z",
        task=Task(description="Fix the authentication bug in login flow"),
        agent=Agent(name="claude-code", version="1.0.0", model="anthropic/claude-sonnet-4-20250514"),
        environment=Environment(
            vcs=VCS(type="git", branch="main"),
            language_ecosystem=["python"],
        ),
        dependencies=["django", "pytest"],
        steps=[
            Step(
                step_index=0,
                role="user",
                content="Fix the authentication bug in the login flow",
            ),
            Step(
                step_index=1,
                role="agent",
                content="I'll investigate the authentication issue.",
                reasoning_content="The user wants me to fix an auth bug. Let me first read the login view to understand the current flow.",
                call_type="warmup",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-1",
                        tool_name="Read",
                        input={"file_path": "/app/views/auth.py"},
                    ),
                ],
                observations=[
                    Observation(
                        source_call_id="tc-1",
                        content="class LoginView:\n    def post(self, request):\n        ...",
                    ),
                ],
                token_usage=TokenUsage(
                    input_tokens=1000,
                    output_tokens=200,
                    cache_read_tokens=500,
                ),
                timestamp="2026-03-28T10:00:30Z",
            ),
            Step(
                step_index=2,
                role="agent",
                content="I found the bug. The token validation is missing.",
                reasoning_content="The login view doesn't validate the CSRF token. I need to add the @csrf_protect decorator.",
                call_type="main",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-2",
                        tool_name="Edit",
                        input={"file_path": "/app/views/auth.py", "old_string": "def post", "new_string": "@csrf_protect\ndef post"},
                    ),
                ],
                observations=[
                    Observation(
                        source_call_id="tc-2",
                        content="File edited successfully",
                    ),
                ],
                token_usage=TokenUsage(
                    input_tokens=1500,
                    output_tokens=300,
                    cache_read_tokens=800,
                ),
                timestamp="2026-03-28T10:02:00Z",
            ),
            Step(
                step_index=3,
                role="agent",
                content="Running the tests to verify the fix.",
                reasoning_content="Let me run the auth tests to make sure the fix works.",
                call_type="main",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-3",
                        tool_name="Bash",
                        input={"command": "pytest tests/test_auth.py -v"},
                    ),
                ],
                observations=[
                    Observation(
                        source_call_id="tc-3",
                        content="3 passed, 0 failed",
                    ),
                ],
                token_usage=TokenUsage(
                    input_tokens=2000,
                    output_tokens=100,
                    cache_read_tokens=1200,
                ),
                timestamp="2026-03-28T10:03:00Z",
            ),
            Step(
                step_index=4,
                role="agent",
                content="The fix is verified. All tests pass.",
                call_type="main",
                token_usage=TokenUsage(
                    input_tokens=500,
                    output_tokens=50,
                ),
                timestamp="2026-03-28T10:04:00Z",
            ),
        ],
        outcome=Outcome(
            success=True,
            committed=True,
            commit_sha="abc123",
            signal_confidence="derived",
            patch="@@ -1,3 +1,4 @@\n+@csrf_protect\n def post(self, request):",
        ),
        metrics=Metrics(
            total_steps=5,
            total_input_tokens=5000,
            total_output_tokens=650,
            total_duration_s=240.0,
            cache_hit_rate=0.62,
            estimated_cost_usd=0.035,
        ),
        attribution=Attribution(
            files=[AttributionFile(path="/app/views/auth.py")],
        ),
    )


def _make_minimal_trace() -> TraceRecord:
    """A minimal trace with almost no data."""
    return TraceRecord(
        trace_id="min-trace-id-000000000000000000000000",
        session_id="min-sess",
        agent=Agent(name="claude-code"),
        steps=[
            Step(step_index=0, role="user", content="hello"),
            Step(step_index=1, role="agent", content="hi"),
        ],
    )


# ---------------------------------------------------------------------------
# Brief loading tests
# ---------------------------------------------------------------------------

class TestBriefLoading:

    def test_all_four_briefs_exist(self):
        for persona in ["training", "rl", "analytics", "domain"]:
            brief = load_brief(persona)
            assert brief is not None, f"Brief not found for {persona}"
            assert brief.persona == persona
            assert len(brief.dimensions) == 5

    def test_dimension_weights_sum_to_one(self):
        for persona in ["training", "rl", "analytics", "domain"]:
            brief = load_brief(persona)
            total = sum(d.weight for d in brief.dimensions)
            assert abs(total - 1.0) < 0.01, f"{persona} weights sum to {total}"

    def test_dimension_names_unique(self):
        for persona in ["training", "rl", "analytics", "domain"]:
            brief = load_brief(persona)
            names = [d.name for d in brief.dimensions]
            assert len(names) == len(set(names)), f"{persona} has duplicate dimension names"

    def test_all_dimensions_have_scoring_guide(self):
        for persona in ["training", "rl", "analytics", "domain"]:
            brief = load_brief(persona)
            for d in brief.dimensions:
                assert d.scoring, f"{persona}.{d.name} has no scoring guide"

    def test_nonexistent_brief_returns_none(self):
        assert load_brief("nonexistent") is None

    def test_brief_has_prose(self):
        for persona in ["training", "rl", "analytics", "domain"]:
            brief = load_brief(persona)
            assert len(brief.prose) > 100, f"{persona} prose is too short"


# ---------------------------------------------------------------------------
# Summarizer tests
# ---------------------------------------------------------------------------

class TestSummarizer:

    def test_rich_trace_summary_has_all_keys(self):
        record = _make_rich_trace()
        summary = summarize_for_judge(record)

        assert "task_description" in summary
        assert "agent" in summary
        assert "environment" in summary
        assert "step_overview" in summary
        assert "first_user_message" in summary
        assert "representative_steps" in summary
        assert "outcome" in summary
        assert "metrics" in summary
        assert "security_scanned" in summary
        assert "attribution" in summary

    def test_rich_trace_summary_token_estimate(self):
        record = _make_rich_trace()
        summary = summarize_for_judge(record)
        serialized = json.dumps(summary, indent=2, default=str)
        # Rough token estimate: ~4 chars per token
        token_est = len(serialized) / 4
        assert token_est < 4000, f"Summary too large: ~{token_est:.0f} tokens"

    def test_minimal_trace_produces_valid_summary(self):
        record = _make_minimal_trace()
        summary = summarize_for_judge(record)

        assert summary["task_description"] == ""
        assert summary["agent"]["name"] == "claude-code"
        assert summary["step_overview"]["total"] == 2
        # No environment, attribution, etc for minimal trace
        assert "environment" not in summary

    def test_encrypted_reasoning_noted(self):
        record = _make_rich_trace()
        record.steps[1].reasoning_content = "[encrypted thinking block content]"
        summary = summarize_for_judge(record)

        rep_steps = summary["representative_steps"]
        # Find the step with encrypted reasoning
        encrypted_found = any(
            s.get("reasoning") == "[encrypted thinking block]"
            for s in rep_steps
        )
        assert encrypted_found

    def test_deterministic_issues_included(self):
        record = _make_rich_trace()
        issues = ["T6 reasoning coverage at 65%", "A5 missing cache_read_tokens"]
        summary = summarize_for_judge(record, deterministic_issues=issues)
        assert summary["deterministic_issues"] == issues

    def test_none_fields_omitted(self):
        record = _make_minimal_trace()
        summary = summarize_for_judge(record)
        serialized = json.dumps(summary, default=str)
        # Should not contain literal "None" strings
        assert '"None"' not in serialized

    def test_step_overview_counts(self):
        record = _make_rich_trace()
        summary = summarize_for_judge(record)
        overview = summary["step_overview"]
        assert overview["total"] == 5
        assert overview["agent"] == 4
        assert overview["user"] == 1
        assert overview["with_tool_calls"] == 3
        assert overview["with_reasoning"] == 3
        assert overview["warmup_steps"] == 1


class TestRepresentativeSteps:

    def test_selects_first_and_last(self):
        record = _make_rich_trace()
        agent_steps = [s for s in record.steps if s.role == "agent"]
        selected = _select_representative_steps(record.steps)
        indices = [s.step_index for s in selected]
        assert agent_steps[0].step_index in indices
        assert agent_steps[-1].step_index in indices

    def test_caps_at_five(self):
        record = _make_rich_trace()
        # Add more agent steps
        for i in range(5, 15):
            record.steps.append(Step(
                step_index=i,
                role="agent",
                content=f"Step {i}",
                tool_calls=[ToolCall(tool_call_id=f"tc-{i}", tool_name="Read", input={})],
            ))
        selected = _select_representative_steps(record.steps)
        assert len(selected) <= 5

    def test_empty_steps_returns_empty(self):
        assert _select_representative_steps([]) == []

    def test_no_agent_steps_returns_empty(self):
        steps = [Step(step_index=0, role="user", content="hello")]
        assert _select_representative_steps(steps) == []


# ---------------------------------------------------------------------------
# Judge response parsing tests
# ---------------------------------------------------------------------------

class TestJudgeResponseParsing:

    def _get_training_brief(self) -> PersonaBrief:
        brief = load_brief("training")
        assert brief is not None
        return brief

    def test_valid_json_response(self):
        brief = self._get_training_brief()
        response = json.dumps({
            "dimensions": [
                {"name": "reasoning_quality", "score": 4, "rationale": "Good reasoning"},
                {"name": "demonstration_value", "score": 3, "rationale": "Adequate"},
                {"name": "task_clarity", "score": 5, "rationale": "Very clear"},
                {"name": "conversation_naturalness", "score": 4, "rationale": "Clean"},
                {"name": "tool_use_coherence", "score": 3, "rationale": "Ok"},
            ]
        })
        dims = _parse_judge_response(response, brief)
        assert len(dims) == 5
        assert dims[0].name == "reasoning_quality"
        assert dims[0].score == 4.0

    def test_markdown_wrapped_json(self):
        brief = self._get_training_brief()
        response = "```json\n" + json.dumps({
            "dimensions": [
                {"name": "reasoning_quality", "score": 4, "rationale": "Good"},
                {"name": "demonstration_value", "score": 3, "rationale": "Ok"},
                {"name": "task_clarity", "score": 5, "rationale": "Clear"},
                {"name": "conversation_naturalness", "score": 4, "rationale": "Clean"},
                {"name": "tool_use_coherence", "score": 3, "rationale": "Ok"},
            ]
        }) + "\n```"
        dims = _parse_judge_response(response, brief)
        assert len(dims) == 5

    def test_missing_dimensions_filled_with_neutral(self):
        brief = self._get_training_brief()
        response = json.dumps({
            "dimensions": [
                {"name": "reasoning_quality", "score": 4, "rationale": "Good"},
                # Only 1 of 5 dimensions
            ]
        })
        dims = _parse_judge_response(response, brief)
        assert len(dims) == 5
        # The four missing ones should default to 3.0
        missing = [d for d in dims if d.name != "reasoning_quality"]
        assert all(d.score == 3.0 for d in missing)

    def test_scores_clamped_to_range(self):
        brief = self._get_training_brief()
        response = json.dumps({
            "dimensions": [
                {"name": "reasoning_quality", "score": 10, "rationale": "Too high"},
                {"name": "demonstration_value", "score": -1, "rationale": "Too low"},
                {"name": "task_clarity", "score": 3, "rationale": "Normal"},
                {"name": "conversation_naturalness", "score": 4, "rationale": "Ok"},
                {"name": "tool_use_coherence", "score": 3, "rationale": "Ok"},
            ]
        })
        dims = _parse_judge_response(response, brief)
        scores = {d.name: d.score for d in dims}
        assert scores["reasoning_quality"] == 5.0  # clamped from 10
        assert scores["demonstration_value"] == 1.0  # clamped from -1

    def test_invalid_json_raises(self):
        brief = self._get_training_brief()
        with pytest.raises(json.JSONDecodeError):
            _parse_judge_response("not json at all", brief)


# ---------------------------------------------------------------------------
# Overall score computation tests
# ---------------------------------------------------------------------------

class TestOverallScore:

    def test_perfect_score(self):
        brief = load_brief("training")
        dims = [JudgeDimension(name=d.name, score=5.0) for d in brief.dimensions]
        assert _compute_judge_overall(dims, brief) == 100.0

    def test_minimum_score(self):
        brief = load_brief("training")
        dims = [JudgeDimension(name=d.name, score=1.0) for d in brief.dimensions]
        assert _compute_judge_overall(dims, brief) == 0.0

    def test_neutral_score(self):
        brief = load_brief("training")
        dims = [JudgeDimension(name=d.name, score=3.0) for d in brief.dimensions]
        score = _compute_judge_overall(dims, brief)
        assert score == 50.0

    def test_weighted_score(self):
        brief = load_brief("training")
        # Give high score to high-weight dim, low to low-weight
        dims = []
        for d in brief.dimensions:
            if d.name == "reasoning_quality":  # weight 0.3
                dims.append(JudgeDimension(name=d.name, score=5.0))
            else:
                dims.append(JudgeDimension(name=d.name, score=1.0))
        score = _compute_judge_overall(dims, brief)
        # reasoning_quality at 5 (100%) with weight 0.3, rest at 1 (0%) with weight 0.7
        # = 0.3 * 100 + 0.7 * 0 = 30.0
        assert score == 30.0


# ---------------------------------------------------------------------------
# run_judge tests (mocked API)
# ---------------------------------------------------------------------------

class TestRunJudge:

    def test_missing_brief_returns_skipped(self):
        result = run_judge("nonexistent", {})
        assert result.skipped
        assert "Brief not found" in result.skip_reason

    def test_missing_sdk_returns_skipped(self):
        """Simulate anthropic not installed."""
        with patch.dict("sys.modules", {"anthropic": None}):
            # Force re-import to fail
            result = run_judge("training", {})
            # The function catches ImportError internally
            assert result.skipped

    def test_successful_judge_call(self):
        """Test the full judge flow with a mocked API."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = json.dumps({
            "dimensions": [
                {"name": "reasoning_quality", "score": 4, "rationale": "Good"},
                {"name": "demonstration_value", "score": 3, "rationale": "Ok"},
                {"name": "task_clarity", "score": 5, "rationale": "Clear"},
                {"name": "conversation_naturalness", "score": 4, "rationale": "Clean"},
                {"name": "tool_use_coherence", "score": 3, "rationale": "Ok"},
            ]
        })

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_anthropic.AuthenticationError = Exception

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            record = _make_rich_trace()
            summary = summarize_for_judge(record)
            result = run_judge("training", summary, model="haiku")

        assert not result.skipped
        assert result.persona_name == "training"
        assert len(result.dimensions) == 5
        assert result.overall_score > 0
        assert result.model_used == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Truncation helper tests
# ---------------------------------------------------------------------------

class TestTruncate:

    def test_none_returns_empty(self):
        assert _truncate(None, 100) == ""

    def test_short_text_unchanged(self):
        assert _truncate("hello", 100) == "hello"

    def test_long_text_truncated(self):
        result = _truncate("a" * 200, 50)
        assert len(result) == 53  # 50 + "..."
        assert result.endswith("...")

    def test_whitespace_stripped(self):
        assert _truncate("  hello  ", 100) == "hello"


# ---------------------------------------------------------------------------
# Engine hybrid scoring integration tests
# ---------------------------------------------------------------------------

class TestHybridScoring:
    """Test that engine.assess_trace works with enable_judge=False (backward compat)."""

    def test_no_judge_backward_compatible(self):
        """assess_trace without judge produces same structure as before."""
        from opentraces.quality.engine import assess_trace

        record = _make_rich_trace()
        record.content_hash = record.compute_content_hash()
        assessment = assess_trace(record, enable_judge=False)

        assert assessment.trace_id == record.trace_id
        assert "conformance" in assessment.persona_scores
        assert "training" in assessment.persona_scores

        # No judge fields set
        for name, ps in assessment.persona_scores.items():
            assert ps.judge_score is None
            assert ps.judge_result is None
            assert ps.deterministic_score is None

    def test_judge_enabled_with_mock(self):
        """assess_trace with judge produces hybrid scores."""
        from opentraces.quality.engine import assess_trace

        mock_response = MagicMock()
        mock_response.content = [MagicMock()]

        # Build a response that works for any persona
        def make_response_text(persona_name):
            brief = load_brief(persona_name)
            if brief is None:
                return "{}"
            dims = [
                {"name": d.name, "score": 4, "rationale": "Good"}
                for d in brief.dimensions
            ]
            return json.dumps({"dimensions": dims})

        call_count = [0]
        persona_order = ["training", "rl", "analytics", "domain"]

        def mock_create(**kwargs):
            resp = MagicMock()
            resp.content = [MagicMock()]
            idx = min(call_count[0], len(persona_order) - 1)
            resp.content[0].text = make_response_text(persona_order[idx])
            call_count[0] += 1
            return resp

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = mock_create

        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_anthropic.AuthenticationError = Exception

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            record = _make_rich_trace()
            record.content_hash = record.compute_content_hash()
            assessment = assess_trace(record, enable_judge=True, judge_model="haiku")

        # Check that hybrid scores were computed for non-conformance personas
        for name in ["training", "rl", "analytics", "domain"]:
            ps = assessment.persona_scores.get(name)
            if ps is None:
                continue
            assert ps.deterministic_score is not None, f"{name} missing deterministic_score"
            assert ps.judge_score is not None, f"{name} missing judge_score"
            # Hybrid should be between deterministic and judge
            assert ps.total_score != ps.deterministic_score or ps.deterministic_score == ps.judge_score

        # Conformance should not have judge
        conf = assessment.persona_scores.get("conformance")
        if conf:
            assert conf.judge_score is None

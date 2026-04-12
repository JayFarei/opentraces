"""Tests for enrichment/intent.py (plan 038 phase 2).

All tests use a mocked model client; no real LLM calls.
"""

from __future__ import annotations

import json
from typing import Callable

import pytest

from opentraces_schema import Agent, Intent, Step, Task, TokenUsage, ToolCall, TraceRecord

from opentraces.enrichment.intent import (
    DEFAULT_MAX_INPUT_CHARS,
    build_prompt,
    enrich_intent,
    parse_model_response,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _trace(
    *,
    task_desc: str = "fix bug in login flow",
    model: str | None = "anthropic/claude-sonnet-4-5",
    steps: list[Step] | None = None,
    intent: Intent | None = None,
) -> TraceRecord:
    return TraceRecord(
        trace_id="t-1",
        session_id="s-1",
        task=Task(description=task_desc),
        agent=Agent(name="claude-code", model=model),
        steps=steps or [
            Step(step_index=0, role="user", content="The login form rejects valid emails."),
            Step(step_index=1, role="agent", content="Let me inspect the validator."),
        ],
        intent=intent,
    )


def _scripted_client(response: str, calls: list[tuple[str, str]] | None = None) -> Callable:
    """A model client that always returns the given response string."""
    def _client(model_id: str, prompt: str) -> str:
        if calls is not None:
            calls.append((model_id, prompt))
        return response
    return _client


def _json_client(title: str, summary: str, **extra):
    payload = {"title": title, "summary": summary, **extra}
    return _scripted_client(json.dumps(payload))


# ---------------------------------------------------------------------------
# build_prompt: structure + budgeting
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_prompt_contains_task_and_user_messages(self):
        trace = _trace()
        prompt = build_prompt(trace)
        assert "fix bug in login flow" in prompt
        assert "login form rejects valid emails" in prompt
        assert "JSON" in prompt

    def test_prompt_includes_tool_histogram(self):
        trace = _trace(steps=[
            Step(step_index=0, role="user", content="hi"),
            Step(step_index=1, role="agent", tool_calls=[
                ToolCall(tool_call_id="1", tool_name="Read"),
                ToolCall(tool_call_id="2", tool_name="Read"),
                ToolCall(tool_call_id="3", tool_name="Edit"),
            ]),
        ])
        prompt = build_prompt(trace)
        assert "Read:2" in prompt
        assert "Edit:1" in prompt

    def test_prompt_is_bounded_on_artificially_long_session(self):
        """Token budget is bounded regardless of step count."""
        huge_text = "x" * 10000
        steps: list[Step] = []
        for i in range(500):
            steps.append(Step(step_index=i * 2, role="user", content=huge_text))
            steps.append(Step(step_index=i * 2 + 1, role="agent", content=huge_text))
        trace = _trace(steps=steps)
        prompt = build_prompt(trace, max_input_chars=5000)
        assert len(prompt) <= 5000 + len("\n\n[prompt truncated to budget]")

    def test_prompt_default_bound_holds(self):
        steps = [Step(step_index=i, role="user", content="x" * 5000) for i in range(200)]
        trace = _trace(steps=steps)
        prompt = build_prompt(trace)
        assert len(prompt) <= DEFAULT_MAX_INPUT_CHARS + 50


# ---------------------------------------------------------------------------
# parse_model_response
# ---------------------------------------------------------------------------


class TestParseResponse:
    def test_bare_json(self):
        t, s = parse_model_response('{"title": "X", "summary": "Y"}')
        assert (t, s) == ("X", "Y")

    def test_fenced_json(self):
        text = 'Sure.\n```json\n{"title": "Refactor", "summary": "Moved files."}\n```\n'
        t, s = parse_model_response(text)
        assert t == "Refactor"
        assert s == "Moved files."

    def test_invalid_json_returns_none(self):
        assert parse_model_response("not json at all") == (None, None)

    def test_non_string_fields_ignored(self):
        t, s = parse_model_response('{"title": 42, "summary": null}')
        assert (t, s) == (None, None)

    def test_empty_response(self):
        assert parse_model_response("") == (None, None)


# ---------------------------------------------------------------------------
# enrich_intent: happy path
# ---------------------------------------------------------------------------


class TestEnrichHappyPath:
    def test_populates_intent_with_llm_hook_source(self):
        calls: list[tuple[str, str]] = []
        trace = _trace()
        client = _scripted_client(
            json.dumps({"title": "Fix login", "summary": "Inspected and repaired the email validator."}),
            calls,
        )
        result = enrich_intent(trace, client)
        assert result.intent is not None
        assert result.intent.title == "Fix login"
        assert result.intent.summary.startswith("Inspected")
        assert result.intent.source == "llm_hook"
        assert len(calls) == 1

    def test_intent_model_matches_agent_model(self):
        trace = _trace(model="anthropic/claude-haiku-4-5-20251001")
        client = _json_client("t", "s")
        result = enrich_intent(trace, client)
        assert result.intent.model == "anthropic/claude-haiku-4-5-20251001"

    def test_explicit_model_id_overrides_agent(self):
        trace = _trace(model="anthropic/claude-sonnet-4-5")
        client = _json_client("t", "s")
        result = enrich_intent(trace, client, model_id="anthropic/claude-haiku-4-5-20251001")
        assert result.intent.model == "anthropic/claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# enrich_intent: idempotency + force semantics
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_rerun_is_noop_without_force(self):
        calls: list[tuple[str, str]] = []
        trace = _trace(
            intent=Intent(title="pre-existing", summary="x", source="llm_hook", model="m"),
        )
        client = _scripted_client(json.dumps({"title": "new", "summary": "y"}), calls)
        result = enrich_intent(trace, client)
        assert result.intent.title == "pre-existing"
        assert calls == []  # model was never called

    def test_force_overwrites_machine_intent(self):
        trace = _trace(
            intent=Intent(title="old", summary="x", source="llm_hook", model="m"),
        )
        client = _json_client("new", "fresh summary")
        result = enrich_intent(trace, client, force=True)
        assert result.intent.title == "new"
        assert result.intent.summary == "fresh summary"


class TestUserProtection:
    def test_user_source_never_overwritten_even_with_force(self):
        trace = _trace(
            intent=Intent(title="hand-written", summary="by a human", source="user"),
        )
        calls: list[tuple[str, str]] = []
        client = _scripted_client(json.dumps({"title": "robot", "summary": "z"}), calls)
        result = enrich_intent(trace, client, force=True)
        assert result.intent.title == "hand-written"
        assert result.intent.source == "user"
        assert calls == []


# ---------------------------------------------------------------------------
# enrich_intent: failure modes are non-fatal
# ---------------------------------------------------------------------------


class TestFailureModesNonFatal:
    def test_model_client_raises_returns_trace_unchanged(self):
        trace = _trace()

        def boom(model_id: str, prompt: str) -> str:
            raise RuntimeError("simulated model outage")

        result = enrich_intent(trace, boom)
        assert result.intent is None

    def test_unparseable_response_returns_trace_unchanged(self):
        trace = _trace()
        client = _scripted_client("I cannot help with that.")
        result = enrich_intent(trace, client)
        assert result.intent is None

    def test_missing_model_id_returns_trace_unchanged(self):
        trace = _trace(model=None)
        client = _json_client("t", "s")
        result = enrich_intent(trace, client)
        assert result.intent is None


# ---------------------------------------------------------------------------
# Redaction invariant: module operates on what it's handed.
# ---------------------------------------------------------------------------


class TestRedactionInvariant:
    """The enrichment module has no access to pre-redaction data.

    It receives a TraceRecord which, by pipeline ordering, has already been
    through the security scrubber. This test demonstrates that the prompt
    reflects exactly what's on the TraceRecord — if the secret is already
    redacted in the trace, it's absent from the prompt. If the caller
    violated the ordering invariant and passed an unredacted trace, the
    module would leak — which is why pipeline ordering is enforced
    upstream, not here.
    """

    def test_prompt_mirrors_trace_content(self):
        redacted = _trace(steps=[
            Step(step_index=0, role="user", content="Deploy with token [REDACTED]"),
        ])
        prompt = build_prompt(redacted)
        assert "[REDACTED]" in prompt
        assert "ghp_realSecretThatShouldNotAppear" not in prompt

    def test_no_access_to_fields_outside_trace(self):
        """The module's only input is the TraceRecord and model client — there
        is no way it can reach pre-redaction data."""
        trace = _trace()
        captured: dict[str, str] = {}

        def capture(model_id: str, prompt: str) -> str:
            captured["prompt"] = prompt
            return json.dumps({"title": "t", "summary": "s"})

        enrich_intent(trace, capture)
        # Only TraceRecord-derived content appears in the prompt.
        assert "login form rejects valid emails" in captured["prompt"]


# ---------------------------------------------------------------------------
# Skill-driven sessions produce skill-aware summaries
# ---------------------------------------------------------------------------


class TestSkillDrivenSession:
    def test_summary_names_the_skill(self):
        """Given a /commit skill session, the summary references it.

        We don't control the LLM — we control the prompt. The prompt surfaces
        task.description and first-user-message, which is where skill names
        appear. So this test asserts the prompt carries the skill name; a
        half-decent model will pick it up.
        """
        trace = _trace(
            task_desc="/commit - commit staged changes",
            steps=[Step(step_index=0, role="user", content="/commit")],
        )
        prompt = build_prompt(trace)
        assert "/commit" in prompt

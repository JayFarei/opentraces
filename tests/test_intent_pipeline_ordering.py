"""Pipeline-ordering invariant: security redaction runs before intent summarization.

This test fails if anyone rewires the pipeline to summarize before
redacting (plan 038 phase 2, non-negotiable security invariant).
"""

from __future__ import annotations

import json

from opentraces.config import Config
from opentraces.enrichment.intent import enrich_intent
from opentraces.pipeline import process_imported_trace
from opentraces_schema import Agent, Step, Task, TraceRecord


SECRET = "ghp_RealSecretThatShouldBeRedactedBeforeTheModelSeesIt000"


def _trace_with_secret() -> TraceRecord:
    return TraceRecord(
        trace_id="t-sec",
        session_id="s-sec",
        task=Task(description=f"Deploy using {SECRET}"),
        agent=Agent(name="claude-code", model="anthropic/claude-sonnet-4-5"),
        steps=[
            Step(step_index=0, role="user", content=f"Run the deploy with token {SECRET}"),
            Step(step_index=1, role="agent", content=f"OK, using {SECRET}."),
        ],
    )


def test_security_runs_before_summarization_so_model_never_sees_secret():
    record = _trace_with_secret()
    processed = process_imported_trace(record, Config())
    assert processed.redaction_count > 0, "test setup bug: secret should trigger redaction"

    captured: dict[str, str] = {}

    def spy_client(model_id: str, prompt: str) -> str:
        captured["prompt"] = prompt
        return json.dumps({"title": "deploy", "summary": "deploy task"})

    enrich_intent(processed.record, spy_client)

    assert SECRET not in captured["prompt"], (
        "Redaction ordering invariant violated: the secret reached the "
        "summarizer prompt. Security must run before intent enrichment."
    )


def test_re_summarizing_still_safe_under_force():
    """Even with --force re-enrichment, the post-redaction trace is what's handed in."""
    record = _trace_with_secret()
    processed = process_imported_trace(record, Config())

    captured: list[str] = []

    def spy(model_id: str, prompt: str) -> str:
        captured.append(prompt)
        return json.dumps({"title": "t", "summary": "s"})

    enrich_intent(processed.record, spy)
    enrich_intent(processed.record, spy, force=True)

    for prompt in captured:
        assert SECRET not in prompt

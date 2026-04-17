"""Unit tests for the Tier 1.5 redaction helper.

``apply_trufflehog_redactions`` mirrors the field walk of the Tier 1
regex redactor but operates on exact raw-match strings handed back by
TruffleHog rather than regex span tuples.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from opentraces.security.scanner import apply_trufflehog_redactions


@dataclass
class _Finding:
    raw_match: str
    detector_name: str = "AWS"
    verified: bool = False
    source_file: str = "f"
    line_number: int = 1


def _make_record(*, description="", step_content="", tc_input_value="",
                 obs_content="", obs_output_summary="", obs_error="",
                 snippet="", patch="", outcome_description="", vcs_diff=""):
    from opentraces_schema import (
        Agent, Environment, Observation, Snippet, Step, Task, ToolCall, TraceRecord,
    )
    from opentraces_schema.models import VCS
    rec = TraceRecord(
        trace_id="t-redact-th",
        session_id="s-redact-th",
        agent=Agent(name="claude-code"),
        task=Task(description=description),
    )
    if step_content or tc_input_value or obs_content or snippet:
        step = Step(
            step_index=0, role="agent", content=step_content,
            timestamp="2026-04-15T10:00:00Z",
            tool_calls=[ToolCall(tool_call_id="tc-0", tool_name="Bash",
                                 input={"cmd": tc_input_value})]
            if tc_input_value else [],
            observations=[Observation(source_call_id="tc-0", content=obs_content,
                                      output_summary=obs_output_summary or None,
                                      error=obs_error or None)]
            if (obs_content or obs_output_summary or obs_error) else [],
            snippets=[Snippet(file_path="foo.py", text=snippet)] if snippet else [],
        )
        rec.steps = [step]
    if patch:
        rec.outcome.patch = patch
    if outcome_description:
        rec.outcome.description = outcome_description
    if vcs_diff:
        rec.environment = Environment(vcs=VCS(diff=vcs_diff))
    return rec


def test_task_description_match_is_redacted():
    rec = _make_record(description="here is my aws key AKIAIOSFODNN7EXAMPLE ok?")
    n = apply_trufflehog_redactions(rec, [_Finding(raw_match="AKIAIOSFODNN7EXAMPLE")])
    assert n == 1
    assert "AKIAIOSFODNN7EXAMPLE" not in rec.task.description
    assert "[REDACTED]" in rec.task.description


def test_multiple_fields_are_walked():
    rec = _make_record(
        description="first AKIAIOSFODNN7EXAMPLE",
        step_content="second AKIAIOSFODNN7EXAMPLE",
        tc_input_value="third AKIAIOSFODNN7EXAMPLE",
        obs_content="fourth AKIAIOSFODNN7EXAMPLE",
        obs_output_summary="fifth AKIAIOSFODNN7EXAMPLE",
        obs_error="sixth AKIAIOSFODNN7EXAMPLE",
        snippet="seventh AKIAIOSFODNN7EXAMPLE",
        patch="eighth AKIAIOSFODNN7EXAMPLE",
        outcome_description="ninth AKIAIOSFODNN7EXAMPLE",
        vcs_diff="tenth AKIAIOSFODNN7EXAMPLE",
    )
    n = apply_trufflehog_redactions(rec, [_Finding(raw_match="AKIAIOSFODNN7EXAMPLE")])
    assert n == 10
    # Every surface got rewritten.
    for text in (
        rec.task.description,
        rec.steps[0].content,
        rec.steps[0].tool_calls[0].input["cmd"],
        rec.steps[0].observations[0].content,
        rec.steps[0].observations[0].output_summary,
        rec.steps[0].observations[0].error,
        rec.steps[0].snippets[0].text,
        rec.outcome.patch,
        rec.outcome.description,
        rec.environment.vcs.diff,
    ):
        assert "AKIAIOSFODNN7EXAMPLE" not in text
        assert "[REDACTED]" in text


def test_longest_first_avoids_prefix_collision():
    """If TH hands us an AWS pair (access + secret key), redacting the
    shorter one first would corrupt the longer one. The helper sorts
    raw_matches by length desc."""
    long_match = "AKIAIOSFODNN7EXAMPLE_WITH_SUFFIX"
    short_match = "AKIAIOSFODNN7EXAMPLE"
    rec = _make_record(description=f"outer {long_match} inner {short_match}")
    n = apply_trufflehog_redactions(rec, [
        _Finding(raw_match=short_match),
        _Finding(raw_match=long_match),
    ])
    # Both secrets gone; result has two [REDACTED] tokens.
    assert long_match not in rec.task.description
    assert short_match not in rec.task.description
    assert rec.task.description.count("[REDACTED]") == 2
    assert n == 2


def test_no_findings_is_noop():
    rec = _make_record(description="nothing to see here")
    assert apply_trufflehog_redactions(rec, []) == 0
    assert rec.task.description == "nothing to see here"

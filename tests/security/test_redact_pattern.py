"""Tests for the content-targeted ``redact_pattern`` helper.

Covers both the pure function (``opentraces.core.inbox.redact_pattern``) and
the persistence wrapper (``opentraces.core.review.redact_pattern_and_persist``).
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from opentraces.core.inbox import redact_pattern
from opentraces.core.review import redact_pattern_and_persist


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sample_trace() -> dict:
    return {
        "trace_id": "trace-001",
        "steps": [
            {
                "content": "hello AKIAABCDEFGHIJKLMNOP world",
                "reasoning_content": "thinking about AKIAABCDEFGHIJKLMNOP key",
                "tool_calls": [{"input": "cmd: AKIAABCDEFGHIJKLMNOP"}],
                "observations": [{"stdout": "log AKIAABCDEFGHIJKLMNOP line"}],
                "snippets": [],
            },
            {
                "content": "second step AKIAABCDEFGHIJKLMNOP",
                "reasoning_content": None,
                "tool_calls": [],
                "observations": [{"stdout": "nothing here"}],
                "snippets": [],
            },
        ],
    }


# ---------------------------------------------------------------------------
# redact_pattern — pure function
# ---------------------------------------------------------------------------


def test_literal_match_across_all_steps_replaces_every_occurrence():
    trace = _sample_trace()
    out = redact_pattern(trace, "AKIAABCDEFGHIJKLMNOP")
    # Mutates in place and returns the trace
    assert out is trace
    for step in trace["steps"]:
        assert "AKIAABCDEFGHIJKLMNOP" not in (step.get("content") or "")
        assert "AKIAABCDEFGHIJKLMNOP" not in (step.get("reasoning_content") or "")
        for tc in step.get("tool_calls") or []:
            assert "AKIAABCDEFGHIJKLMNOP" not in (tc.get("input") or "")
        for obs in step.get("observations") or []:
            assert "AKIAABCDEFGHIJKLMNOP" not in (obs.get("stdout") or "")
    assert trace["steps"][0]["content"] == "hello [REDACTED] world"
    assert trace["steps"][1]["content"] == "second step [REDACTED]"


def test_regex_match_captures_aws_key_pattern():
    trace = _sample_trace()
    redact_pattern(trace, r"AKIA[0-9A-Z]{16}", regex=True)
    assert trace["steps"][0]["content"] == "hello [REDACTED] world"
    assert trace["steps"][1]["content"] == "second step [REDACTED]"


def test_field_scope_only_touches_content():
    trace = _sample_trace()
    redact_pattern(trace, "AKIAABCDEFGHIJKLMNOP", field="content")
    # content redacted
    assert trace["steps"][0]["content"] == "hello [REDACTED] world"
    # reasoning_content / tool_calls / observations untouched
    assert "AKIAABCDEFGHIJKLMNOP" in trace["steps"][0]["reasoning_content"]
    assert "AKIAABCDEFGHIJKLMNOP" in trace["steps"][0]["tool_calls"][0]["input"]
    assert "AKIAABCDEFGHIJKLMNOP" in trace["steps"][0]["observations"][0]["stdout"]


def test_step_scope_only_touches_specified_step():
    trace = _sample_trace()
    redact_pattern(trace, "AKIAABCDEFGHIJKLMNOP", step=1)
    # Step 0 untouched
    assert "AKIAABCDEFGHIJKLMNOP" in trace["steps"][0]["content"]
    # Step 1 redacted
    assert trace["steps"][1]["content"] == "second step [REDACTED]"


def test_field_and_step_combined_narrowest_scope():
    trace = _sample_trace()
    redact_pattern(trace, "AKIAABCDEFGHIJKLMNOP", field="content", step=0)
    assert trace["steps"][0]["content"] == "hello [REDACTED] world"
    # everything else untouched
    assert "AKIAABCDEFGHIJKLMNOP" in trace["steps"][0]["reasoning_content"]
    assert "AKIAABCDEFGHIJKLMNOP" in trace["steps"][1]["content"]


def test_dotted_field_path_walks_into_observations_stdout():
    trace = _sample_trace()
    redact_pattern(trace, "AKIAABCDEFGHIJKLMNOP", field="observations.stdout")
    assert trace["steps"][0]["observations"][0]["stdout"] == "log [REDACTED] line"
    # content untouched
    assert "AKIAABCDEFGHIJKLMNOP" in trace["steps"][0]["content"]


def test_idempotent_on_no_match():
    trace = _sample_trace()
    snapshot = deepcopy(trace)
    redact_pattern(trace, "NOT_IN_TRACE_AT_ALL")
    assert trace == snapshot


def test_empty_pattern_raises_value_error():
    trace = _sample_trace()
    with pytest.raises(ValueError):
        redact_pattern(trace, "")


def test_invalid_regex_raises_value_error():
    trace = _sample_trace()
    with pytest.raises(ValueError):
        redact_pattern(trace, "[unclosed", regex=True)


def test_out_of_range_step_raises_value_error():
    trace = _sample_trace()
    with pytest.raises(ValueError):
        redact_pattern(trace, "AKIAABCDEFGHIJKLMNOP", step=99)
    with pytest.raises(ValueError):
        redact_pattern(trace, "AKIAABCDEFGHIJKLMNOP", step=-1)


# ---------------------------------------------------------------------------
# redact_pattern_and_persist — persistence wrapper
# ---------------------------------------------------------------------------


def _write_trace(staging_dir: Path, trace: dict) -> Path:
    staging_dir.mkdir(parents=True, exist_ok=True)
    path = staging_dir / f"{trace['trace_id']}.jsonl"
    path.write_text(json.dumps(trace) + "\n")
    return path


def test_persist_writes_redacted_jsonl_atomically(tmp_path: Path):
    staging = tmp_path / "staging"
    trace = _sample_trace()
    path = _write_trace(staging, trace)

    result = redact_pattern_and_persist(staging, "trace-001", "AKIAABCDEFGHIJKLMNOP")
    assert result.ok, result.error

    on_disk = json.loads(path.read_text().splitlines()[0])
    assert on_disk["steps"][0]["content"] == "hello [REDACTED] world"
    assert on_disk["steps"][1]["content"] == "second step [REDACTED]"
    # No stray tempfiles left behind
    leftovers = list(staging.glob("*.tmp"))
    assert leftovers == []


def test_persist_idempotent_on_no_match(tmp_path: Path):
    staging = tmp_path / "staging"
    trace = _sample_trace()
    path = _write_trace(staging, trace)
    before = path.read_text()

    result = redact_pattern_and_persist(staging, "trace-001", "DOES_NOT_APPEAR")
    assert result.ok
    after = path.read_text()
    assert json.loads(after.splitlines()[0]) == json.loads(before.splitlines()[0])


def test_persist_not_found(tmp_path: Path):
    staging = tmp_path / "staging"
    staging.mkdir()
    result = redact_pattern_and_persist(staging, "missing", "whatever")
    assert not result.ok
    assert result.error_code == "NOT_FOUND"


def test_persist_regex_mode(tmp_path: Path):
    staging = tmp_path / "staging"
    trace = _sample_trace()
    path = _write_trace(staging, trace)

    result = redact_pattern_and_persist(
        staging, "trace-001", r"AKIA[0-9A-Z]{16}", regex=True,
    )
    assert result.ok
    on_disk = json.loads(path.read_text().splitlines()[0])
    assert on_disk["steps"][0]["content"] == "hello [REDACTED] world"


def test_persist_empty_pattern_returns_error(tmp_path: Path):
    staging = tmp_path / "staging"
    _write_trace(staging, _sample_trace())
    result = redact_pattern_and_persist(staging, "trace-001", "")
    assert not result.ok
    assert result.error_code == "INVALID_PATTERN"


def test_persist_out_of_range_step_returns_error(tmp_path: Path):
    staging = tmp_path / "staging"
    _write_trace(staging, _sample_trace())
    result = redact_pattern_and_persist(
        staging, "trace-001", "AKIAABCDEFGHIJKLMNOP", step=99,
    )
    assert not result.ok
    assert result.error_code == "OUT_OF_RANGE"

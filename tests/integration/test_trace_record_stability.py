"""Plan-043 phase-7 schema stability gate.

Plan 043 (Blame & Attribution) is contractually a read-only enrichment
feature: it writes per-commit attribution JSON into
``~/.opentraces/projects/<slug>/attributions/`` but MUST NOT mutate the
``TraceRecord`` schema. This test locks that in by loading a fixture of
known-good v0.2/v0.4 traces and asserting:

  1. Every line parses cleanly via ``TraceRecord.model_validate``.
  2. A deserialize → serialize → deserialize round-trip is stable.
  3. No required fields were added by plan 043 (captured as an explicit
     allowlist so drift here requires a deliberate schema bump).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from opentraces_schema import TraceRecord


FIXTURE = Path(__file__).parent.parent / "fixtures" / "trace_record_stability" / "v02_sample.jsonl"


def _iter_fixture():
    with open(FIXTURE) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            yield i, json.loads(line)


def test_fixture_exists() -> None:
    assert FIXTURE.is_file(), f"missing stability fixture: {FIXTURE}"


@pytest.mark.parametrize("lineno,payload", list(_iter_fixture()))
def test_trace_validates(lineno: int, payload: dict) -> None:
    """Every fixture record validates under the current TraceRecord schema."""
    tr = TraceRecord.model_validate(payload)
    assert tr.trace_id, f"line {lineno}: missing trace_id"


@pytest.mark.parametrize("lineno,payload", list(_iter_fixture()))
def test_roundtrip_stable(lineno: int, payload: dict) -> None:
    """deserialize → serialize → deserialize is byte-stable for the
    round-tripped dict. Plan 043 adds no fields; any drift here means a
    schema mutation slipped through."""
    first = TraceRecord.model_validate(payload).model_dump(mode="json")
    second = TraceRecord.model_validate(first).model_dump(mode="json")
    assert first == second, (
        f"line {lineno}: round-trip drifted. "
        f"A field changed between first and second deserialize."
    )


def test_plan_043_did_not_add_required_fields() -> None:
    """The set of fields that TraceRecord considers required today must be
    a subset of what was required before plan 043. We pin the pre-plan
    set here; plan 043 is forbidden from widening it."""
    required = {
        name
        for name, field in TraceRecord.model_fields.items()
        if field.is_required()
    }
    # Pre-plan-043 required fields (from schema v0.4.0). Plan 043 makes
    # no schema changes, so this set should be unchanged.
    # Pinned at the schema set from opentraces-schema v0.4.x, which is what
    # shipped before plan 043. Updating this set must only happen under an
    # explicit schema bump, not as a side effect of enrichment work.
    allowed = {"trace_id", "schema_version", "session_id", "agent"}
    extra = required - allowed
    assert not extra, (
        f"Plan 043 must not add required TraceRecord fields; found new: {extra}"
    )

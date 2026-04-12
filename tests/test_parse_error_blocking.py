"""Part C tests: parse-error blocking through the pipeline.

Malformed traces must never reach upload. A parser that encounters
errors mid-parse produces a ``ParseOutcome`` with non-empty
``errors``; the state machine moves the trace to ``BLOCKED`` with a
``block_reason``; uploads refuse blocked traces.
"""

from __future__ import annotations

import pytest

from opentraces.capture._base import ParseOutcome
from opentraces.core.state import (
    StateManager,
    TraceStagingEntry,
    TraceStatus,
)


class TestParseOutcome:
    def test_ok_outcome_has_record_and_no_errors(self) -> None:
        outcome = ParseOutcome(record=object(), errors=[])
        assert outcome.is_blocked() is False
        assert outcome.record is not None

    def test_outcome_with_errors_is_blocked(self) -> None:
        outcome = ParseOutcome(record=None, errors=["malformed step at offset 42"])
        assert outcome.is_blocked() is True
        assert outcome.block_reason() == "parse_error"

    def test_outcome_with_record_and_errors_is_still_blocked(self) -> None:
        # Partial parse with errors still blocks — "if any parse errors occurred".
        outcome = ParseOutcome(record=object(), errors=["truncated"])
        assert outcome.is_blocked() is True

    def test_default_outcome_is_empty(self) -> None:
        outcome = ParseOutcome()
        assert outcome.record is None
        assert outcome.errors == []
        assert outcome.is_blocked() is False


class TestBlockedState:
    def test_blocked_status_exists(self) -> None:
        assert TraceStatus.BLOCKED.value == "blocked"

    def test_block_reason_field(self) -> None:
        entry = TraceStagingEntry(
            trace_id="t1",
            session_id="s1",
            status=TraceStatus.BLOCKED,
            block_reason="parse_error",
        )
        assert entry.block_reason == "parse_error"


class TestStateManagerBlocking:
    def test_block_trace(self, tmp_path) -> None:
        sm = StateManager(tmp_path / "state.json")
        sm.set_trace_status("t1", TraceStatus.PARSED, session_id="s1")
        sm.block_trace("t1", reason="parse_error")

        entry = sm.get_trace("t1")
        assert entry is not None
        assert entry.status == TraceStatus.BLOCKED
        assert entry.block_reason == "parse_error"

    def test_blocked_traces_are_not_pending_upload(self, tmp_path) -> None:
        sm = StateManager(tmp_path / "state.json")
        sm.set_trace_status("good", TraceStatus.COMMITTED)
        sm.set_trace_status("bad", TraceStatus.PARSED)
        sm.block_trace("bad", reason="parse_error")

        pending = sm.get_pending_upload_traces()
        ids = {e.trace_id for e in pending}
        assert "good" in ids
        assert "bad" not in ids

    def test_get_blocked_traces(self, tmp_path) -> None:
        sm = StateManager(tmp_path / "state.json")
        sm.block_trace("t1", reason="parse_error")
        sm.block_trace("t2", reason="trufflehog_finding")
        sm.set_trace_status("t3", TraceStatus.STAGED)

        blocked = sm.get_blocked_traces()
        reasons = {e.trace_id: e.block_reason for e in blocked}
        assert reasons == {"t1": "parse_error", "t2": "trufflehog_finding"}

    def test_block_reason_persists_across_reload(self, tmp_path) -> None:
        state_path = tmp_path / "state.json"
        sm = StateManager(state_path)
        sm.block_trace("t1", reason="parse_error")

        sm2 = StateManager(state_path)
        entry = sm2.get_trace("t1")
        assert entry is not None
        assert entry.block_reason == "parse_error"
        assert entry.status == TraceStatus.BLOCKED

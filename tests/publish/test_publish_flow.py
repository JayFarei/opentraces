"""Tests for the per-remote upload tracking in core/publish_flow + cli/publish.

Step 3 of the CLI restructure (kb/plans/buzzing-dreaming-forest.md).

The mark_uploaded helper in core/publish_flow.py was previously
remote-agnostic — it only set status=UPLOADED on each trace_id. Per Step 2,
TraceStagingEntry now has uploaded_to: dict[str, str], and pending_for()
returns missing-on-remote sets. mark_uploaded must accept a remote_name
and call state.mark_uploaded_to() so the per-remote replay flow works.

The full push command in cli/publish.py is integration-tested elsewhere;
here we focus on the leaf helper + the round-trip through pending_for.
"""

from __future__ import annotations

import pytest

from opentraces.core.publish_flow import mark_uploaded
from opentraces.core.state import StateManager, TraceStatus


@pytest.fixture
def state(tmp_path):
    return StateManager(state_path=tmp_path / "state.json")


class TestMarkUploadedSignature:
    def test_mark_uploaded_accepts_remote_name(self, state) -> None:
        state.set_trace_status("a", TraceStatus.COMMITTED)
        # Should not raise — new keyword param.
        mark_uploaded(state, ["a"], remote_name="origin")

    def test_mark_uploaded_writes_uploaded_to(self, state) -> None:
        state.set_trace_status("a", TraceStatus.COMMITTED)
        mark_uploaded(state, ["a"], remote_name="origin")
        trace = state.get_trace("a")
        assert "origin" in trace.uploaded_to

    def test_mark_uploaded_sets_status_uploaded(self, state) -> None:
        state.set_trace_status("a", TraceStatus.COMMITTED)
        mark_uploaded(state, ["a"], remote_name="origin")
        assert state.get_trace("a").status == TraceStatus.UPLOADED

    def test_mark_uploaded_sets_uploaded_at(self, state) -> None:
        state.set_trace_status("a", TraceStatus.COMMITTED)
        mark_uploaded(state, ["a"], remote_name="origin")
        assert state.get_trace("a").uploaded_at is not None

    def test_mark_uploaded_handles_multiple_traces(self, state) -> None:
        for tid in ("a", "b", "c"):
            state.set_trace_status(tid, TraceStatus.COMMITTED)
        mark_uploaded(state, ["a", "b", "c"], remote_name="origin")
        for tid in ("a", "b", "c"):
            assert "origin" in state.get_trace(tid).uploaded_to


class TestRoundTripWithPendingFor:
    def test_origin_then_upstream_replays_all(self, state) -> None:
        """The end-to-end behavior the user's two-remote journey requires:
        push 5 traces to origin; switch to upstream; pending_for(upstream)
        returns all 5; push to upstream; both remotes are caught up."""
        for tid in ("a", "b", "c", "d", "e"):
            state.set_trace_status(tid, TraceStatus.COMMITTED)

        # First push: to origin.
        ids = [t.trace_id for t in state.pending_for("origin")]
        assert set(ids) == {"a", "b", "c", "d", "e"}
        mark_uploaded(state, ids, remote_name="origin")

        # No more pending on origin.
        assert state.pending_for("origin") == []

        # All 5 still pending on upstream (per-remote replay).
        upstream_ids = [t.trace_id for t in state.pending_for("upstream")]
        assert set(upstream_ids) == {"a", "b", "c", "d", "e"}

        # Push to upstream.
        mark_uploaded(state, upstream_ids, remote_name="upstream")

        # Both caught up.
        assert state.pending_for("origin") == []
        assert state.pending_for("upstream") == []

    def test_committed_pending_on_both_remotes_until_pushed(self, state) -> None:
        state.set_trace_status("a", TraceStatus.COMMITTED)
        assert any(t.trace_id == "a" for t in state.pending_for("origin"))
        assert any(t.trace_id == "a" for t in state.pending_for("upstream"))

        mark_uploaded(state, ["a"], remote_name="origin")
        assert all(t.trace_id != "a" for t in state.pending_for("origin"))
        assert any(t.trace_id == "a" for t in state.pending_for("upstream"))


class TestBackwardCompatNoRemoteName:
    """Older callers that call mark_uploaded(state, ids) without a remote
    name should still work — we keep the old single-remote behavior so
    in-flight call sites (cli/publish.py until step 3 is fully wired)
    don't crash. The trace gets status=UPLOADED but no uploaded_to entry."""

    def test_no_remote_name_falls_back_to_status_only(self, state) -> None:
        state.set_trace_status("a", TraceStatus.COMMITTED)
        mark_uploaded(state, ["a"])  # no remote_name
        trace = state.get_trace("a")
        assert trace.status == TraceStatus.UPLOADED
        # No remote name -> no uploaded_to entry. (Acceptable transitional
        # behavior; the cli layer will start passing remote_name.)
        assert trace.uploaded_to == {}

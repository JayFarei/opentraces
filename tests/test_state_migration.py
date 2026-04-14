"""Tests for TraceStagingEntry per-remote upload tracking + state migration.

Step 2 of the CLI restructure plan (kb/plans/buzzing-dreaming-forest.md).

Schema change:
- TraceStagingEntry gains uploaded_to: dict[str, str] (remote_name -> ISO8601 ts).
- StateManager gains STATE_SCHEMA_VERSION + _migrate() in _load().
- Existing UPLOADED traces are backfilled with
  uploaded_to = {<active_remote>: <existing uploaded_at or epoch>}.

New methods:
- pending_for(remote_name): COMMITTED OR (UPLOADED but not in uploaded_to[remote_name]).
- rename_remote(old, new): atomically rewrites every uploaded_to key.
- forget_remote(name): atomically drops the key from every uploaded_to.
- mark_uploaded_to(trace_id, remote_name): writes uploaded_to[remote] + uploaded_at.

New error: UnknownRemoteError raised when a public lookup names a remote that
the project doesn't know about (after rename/remove).
"""

from __future__ import annotations

import json
import time

import pytest

from opentraces.core.state import (
    STATE_SCHEMA_VERSION,
    StateManager,
    TraceStagingEntry,
    TraceStatus,
    UnknownRemoteError,
)


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "state.json"


def _legacy_v1_state(uploaded_at: str = "2026-03-15T10:00:00Z") -> dict:
    """Build a state dict in the pre-restructure v1 shape: no state_version,
    no uploaded_to on traces."""
    return {
        "processed_files": {},
        "traces": {
            "abc123": {
                "trace_id": "abc123",
                "session_id": "sess-1",
                "status": "uploaded",
                "uploaded_at": uploaded_at,
                "created_at": time.time(),
            },
            "def456": {
                "trace_id": "def456",
                "session_id": "sess-2",
                "status": "committed",
                "created_at": time.time(),
            },
            "ghi789": {
                "trace_id": "ghi789",
                "session_id": "sess-3",
                "status": "blocked",
                "block_reason": "sk-ant-... matched",
                "created_at": time.time(),
            },
        },
        "commit_groups": {},
    }


class TestSchemaVersionConstant:
    def test_state_schema_version_is_at_least_2(self) -> None:
        # Was implicit "1" pre-migration.
        assert int(STATE_SCHEMA_VERSION) >= 2


class TestTraceStagingEntryShape:
    def test_uploaded_to_default_empty_dict(self) -> None:
        entry = TraceStagingEntry(
            trace_id="x", session_id="s", status=TraceStatus.STAGED
        )
        assert entry.uploaded_to == {}

    def test_uploaded_to_accepts_dict(self) -> None:
        entry = TraceStagingEntry(
            trace_id="x",
            session_id="s",
            status=TraceStatus.UPLOADED,
            uploaded_at="2026-03-15T10:00:00Z",
            uploaded_to={"origin": "2026-03-15T10:00:00Z"},
        )
        assert entry.uploaded_to == {"origin": "2026-03-15T10:00:00Z"}


class TestMigration:
    def test_legacy_state_loads_without_error(self, state_path) -> None:
        state_path.write_text(json.dumps(_legacy_v1_state()))
        sm = StateManager(state_path=state_path)  # should not raise
        assert sm.get_trace("abc123") is not None

    def test_uploaded_trace_gets_uploaded_to_origin(self, state_path) -> None:
        ts = "2026-03-15T10:00:00Z"
        state_path.write_text(json.dumps(_legacy_v1_state(uploaded_at=ts)))

        sm = StateManager(state_path=state_path)
        trace = sm.get_trace("abc123")

        assert trace.status == TraceStatus.UPLOADED
        assert trace.uploaded_to == {"origin": ts}
        # Legacy uploaded_at preserved
        assert trace.uploaded_at == ts

    def test_committed_trace_unchanged_by_migration(self, state_path) -> None:
        state_path.write_text(json.dumps(_legacy_v1_state()))
        sm = StateManager(state_path=state_path)
        trace = sm.get_trace("def456")
        assert trace.uploaded_to == {}
        assert trace.uploaded_at is None

    def test_blocked_trace_unchanged_by_migration(self, state_path) -> None:
        state_path.write_text(json.dumps(_legacy_v1_state()))
        sm = StateManager(state_path=state_path)
        trace = sm.get_trace("ghi789")
        assert trace.status == TraceStatus.BLOCKED
        assert trace.block_reason == "sk-ant-... matched"
        assert trace.uploaded_to == {}

    def test_state_version_persisted_after_migration(self, state_path) -> None:
        state_path.write_text(json.dumps(_legacy_v1_state()))
        sm = StateManager(state_path=state_path)
        sm.save()
        on_disk = json.loads(state_path.read_text())
        assert on_disk.get("state_version") == STATE_SCHEMA_VERSION

    def test_uploaded_to_persisted_after_migration(self, state_path) -> None:
        ts = "2026-03-15T10:00:00Z"
        state_path.write_text(json.dumps(_legacy_v1_state(uploaded_at=ts)))
        sm = StateManager(state_path=state_path)
        sm.save()
        on_disk = json.loads(state_path.read_text())
        assert on_disk["traces"]["abc123"]["uploaded_to"] == {"origin": ts}

    def test_migration_is_idempotent(self, state_path) -> None:
        state_path.write_text(json.dumps(_legacy_v1_state()))
        first = StateManager(state_path=state_path)
        first.save()
        first_disk = state_path.read_text()

        second = StateManager(state_path=state_path)
        second.save()
        second_disk = state_path.read_text()

        # Already-migrated state should round-trip identically.
        assert json.loads(first_disk) == json.loads(second_disk)

    def test_uploaded_at_missing_falls_back_to_epoch(self, state_path) -> None:
        # Edge case: a trace marked UPLOADED with no uploaded_at field.
        legacy = _legacy_v1_state()
        del legacy["traces"]["abc123"]["uploaded_at"]
        state_path.write_text(json.dumps(legacy))

        sm = StateManager(state_path=state_path)
        trace = sm.get_trace("abc123")

        assert "origin" in trace.uploaded_to
        # epoch placeholder so the entry is well-formed
        assert trace.uploaded_to["origin"] == "1970-01-01T00:00:00Z"


class TestPendingFor:
    def test_committed_trace_pending_on_any_remote(self, state_path) -> None:
        sm = StateManager(state_path=state_path)
        sm.set_trace_status("a", TraceStatus.COMMITTED)
        pending = sm.pending_for("origin")
        assert any(t.trace_id == "a" for t in pending)

    def test_uploaded_to_origin_not_pending_on_origin(self, state_path) -> None:
        sm = StateManager(state_path=state_path)
        sm.set_trace_status(
            "a",
            TraceStatus.UPLOADED,
            uploaded_at="2026-03-15T10:00:00Z",
            uploaded_to={"origin": "2026-03-15T10:00:00Z"},
        )
        pending = sm.pending_for("origin")
        assert all(t.trace_id != "a" for t in pending)

    def test_uploaded_to_origin_is_pending_on_upstream(self, state_path) -> None:
        sm = StateManager(state_path=state_path)
        sm.set_trace_status(
            "a",
            TraceStatus.UPLOADED,
            uploaded_at="2026-03-15T10:00:00Z",
            uploaded_to={"origin": "2026-03-15T10:00:00Z"},
        )
        pending = sm.pending_for("upstream")
        assert any(t.trace_id == "a" for t in pending)

    def test_blocked_trace_never_pending(self, state_path) -> None:
        sm = StateManager(state_path=state_path)
        sm.block_trace("a", reason="bad secret")
        assert sm.pending_for("origin") == []
        assert sm.pending_for("upstream") == []

    def test_rejected_trace_never_pending(self, state_path) -> None:
        sm = StateManager(state_path=state_path)
        sm.set_trace_status("a", TraceStatus.REJECTED)
        assert sm.pending_for("origin") == []


class TestMarkUploadedTo:
    def test_mark_uploaded_to_writes_both_fields(self, state_path) -> None:
        sm = StateManager(state_path=state_path)
        sm.set_trace_status("a", TraceStatus.COMMITTED)

        sm.mark_uploaded_to("a", "origin")

        trace = sm.get_trace("a")
        assert trace.status == TraceStatus.UPLOADED
        assert "origin" in trace.uploaded_to
        assert trace.uploaded_at is not None

    def test_mark_uploaded_to_multiple_remotes_accumulates(self, state_path) -> None:
        sm = StateManager(state_path=state_path)
        sm.set_trace_status("a", TraceStatus.COMMITTED)

        sm.mark_uploaded_to("a", "origin")
        sm.mark_uploaded_to("a", "upstream")

        trace = sm.get_trace("a")
        assert "origin" in trace.uploaded_to
        assert "upstream" in trace.uploaded_to


class TestRenameRemote:
    def test_rename_walks_every_uploaded_to_key(self, state_path) -> None:
        sm = StateManager(state_path=state_path)
        sm.set_trace_status(
            "a",
            TraceStatus.UPLOADED,
            uploaded_at="2026-03-15T10:00:00Z",
            uploaded_to={"origin": "2026-03-15T10:00:00Z"},
        )
        sm.set_trace_status(
            "b",
            TraceStatus.UPLOADED,
            uploaded_at="2026-03-16T10:00:00Z",
            uploaded_to={"origin": "2026-03-16T10:00:00Z"},
        )

        sm.rename_remote("origin", "upstream")

        a = sm.get_trace("a")
        b = sm.get_trace("b")
        assert "origin" not in a.uploaded_to
        assert "upstream" in a.uploaded_to
        assert "origin" not in b.uploaded_to
        assert "upstream" in b.uploaded_to

    def test_rename_preserves_other_remotes(self, state_path) -> None:
        sm = StateManager(state_path=state_path)
        sm.set_trace_status(
            "a",
            TraceStatus.UPLOADED,
            uploaded_to={"origin": "ts1", "fork": "ts2"},
        )
        sm.rename_remote("origin", "upstream")
        a = sm.get_trace("a")
        assert a.uploaded_to == {"upstream": "ts1", "fork": "ts2"}

    def test_rename_unknown_remote_raises(self, state_path) -> None:
        sm = StateManager(state_path=state_path)
        with pytest.raises(UnknownRemoteError):
            sm.rename_remote("ghost", "upstream")


class TestForgetRemote:
    def test_forget_drops_the_key_from_every_trace(self, state_path) -> None:
        sm = StateManager(state_path=state_path)
        sm.set_trace_status("a", TraceStatus.UPLOADED, uploaded_to={"origin": "ts1"})
        sm.set_trace_status("b", TraceStatus.UPLOADED, uploaded_to={"origin": "ts2", "fork": "ts3"})

        sm.forget_remote("origin")

        a = sm.get_trace("a")
        b = sm.get_trace("b")
        assert a.uploaded_to == {}
        assert b.uploaded_to == {"fork": "ts3"}

    def test_forget_unknown_remote_raises(self, state_path) -> None:
        sm = StateManager(state_path=state_path)
        with pytest.raises(UnknownRemoteError):
            sm.forget_remote("ghost")


class TestUnknownRemoteErrorIsClassifyable:
    def test_unknown_remote_error_is_subclass_of_key_error(self) -> None:
        assert issubclass(UnknownRemoteError, KeyError)

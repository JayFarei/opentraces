"""Tests for the sessions/generations state schema (plan: live-session ingestion).

Introduces a top-level ``sessions`` dict in state.json keyed by Claude Code
session_id. Each session carries:

  - ``source_path``: absolute path to the session's JSONL
  - ``observed_size`` / ``observed_mtime``: last-seen file stats (drives the
    "has it grown since last ingest" check)
  - ``session_end_seen``: True once the SessionEnd hook has recorded exit
  - ``post_commit_triggered``: True after the git post-commit hook kicked a
    push for this session (either is a "treat as a natural boundary" signal
    for auto-review staleness)
  - ``generations``: ordered list of per-ingestion snapshots

Each generation records:

  - ``trace_id``: the staging-file key (cc_<session_id>_g<N>)
  - ``generation``: 1-based monotonic int
  - ``captured_size`` / ``captured_mtime``: file stats at capture time
  - ``schema_version`` / ``security_version``: versions in force at capture
  - ``status_at_capture``: the trace status when this generation was born
  - ``supersedes``: trace_id of the prior generation (None for gen 1)
  - ``supersedes_reason``: "resume" | "schema_bump" | "manual" | None
  - ``created_at``: ISO timestamp

Migration: schema v3 -> v4 just initialises an empty ``sessions`` dict.
"""

from __future__ import annotations

import json

import pytest

from opentraces.core.state import (
    STATE_SCHEMA_VERSION,
    GenerationRecord,
    SessionRecord,
    StateManager,
)


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "state.json"


# --------------------------------------------------------------------------- #
# Migration
# --------------------------------------------------------------------------- #

class TestSchemaVersion:
    def test_schema_version_is_v4(self) -> None:
        assert STATE_SCHEMA_VERSION == "4"

    def test_fresh_state_has_empty_sessions_dict(self, state_path) -> None:
        state = StateManager(state_path=state_path)
        state.save()
        raw = json.loads(state_path.read_text())
        assert raw["state_version"] == "4"
        assert raw["sessions"] == {}

    def test_v3_state_migrates_to_v4_with_empty_sessions(self, state_path) -> None:
        """An on-disk v3 state must load, migrate to v4, and retain its data."""
        state_path.write_text(json.dumps({
            "state_version": "3",
            "processed_files": {"/some/path.jsonl": {
                "file_path": "/some/path.jsonl",
                "inode": 1,
                "mtime": 1.0,
                "last_byte_offset": 0,
            }},
            "traces": {"tA": {"trace_id": "tA", "session_id": "sA",
                              "status": "inbox", "created_at": 1.0}},
            "commit_groups": {},
            "last_backfilled_commit": "abc",
            "last_backfill_at": "2026-01-01T00:00:00Z",
        }))

        state = StateManager(state_path=state_path)
        state.save()

        raw = json.loads(state_path.read_text())
        assert raw["state_version"] == "4"
        assert raw["sessions"] == {}
        # legacy fields preserved
        assert raw["traces"]["tA"]["trace_id"] == "tA"
        assert raw["last_backfilled_commit"] == "abc"


# --------------------------------------------------------------------------- #
# Upsert + retrieve
# --------------------------------------------------------------------------- #

class TestSessionUpsert:
    def test_get_missing_session_returns_none(self, state_path) -> None:
        state = StateManager(state_path=state_path)
        assert state.get_session("nonexistent") is None

    def test_upsert_session_creates_record(self, state_path) -> None:
        state = StateManager(state_path=state_path)
        state.upsert_session(
            session_id="sess-1",
            source_path="/abs/foo.jsonl",
            observed_size=1000,
            observed_mtime=123.4,
        )
        rec = state.get_session("sess-1")
        assert rec is not None
        assert rec.session_id == "sess-1"
        assert rec.source_path == "/abs/foo.jsonl"
        assert rec.observed_size == 1000
        assert rec.observed_mtime == pytest.approx(123.4)
        assert rec.session_end_seen is False
        assert rec.post_commit_triggered is False
        assert rec.generations == []

    def test_upsert_existing_session_updates_observed_fields(self, state_path) -> None:
        state = StateManager(state_path=state_path)
        state.upsert_session(
            session_id="sess-1",
            source_path="/abs/foo.jsonl",
            observed_size=1000,
            observed_mtime=100.0,
        )
        state.upsert_session(
            session_id="sess-1",
            source_path="/abs/foo.jsonl",
            observed_size=2500,
            observed_mtime=200.0,
        )
        rec = state.get_session("sess-1")
        assert rec.observed_size == 2500
        assert rec.observed_mtime == pytest.approx(200.0)

    def test_set_session_end_seen_flag(self, state_path) -> None:
        state = StateManager(state_path=state_path)
        state.upsert_session(
            session_id="sess-1", source_path="/p.jsonl",
            observed_size=1, observed_mtime=1.0,
        )
        state.set_session_flag("sess-1", "session_end_seen", True)
        assert state.get_session("sess-1").session_end_seen is True

    def test_set_post_commit_flag(self, state_path) -> None:
        state = StateManager(state_path=state_path)
        state.upsert_session(
            session_id="sess-1", source_path="/p.jsonl",
            observed_size=1, observed_mtime=1.0,
        )
        state.set_session_flag("sess-1", "post_commit_triggered", True)
        assert state.get_session("sess-1").post_commit_triggered is True


# --------------------------------------------------------------------------- #
# Generations
# --------------------------------------------------------------------------- #

class TestGenerations:
    def _seed(self, state_path):
        state = StateManager(state_path=state_path)
        state.upsert_session(
            session_id="sess-1", source_path="/p.jsonl",
            observed_size=500, observed_mtime=100.0,
        )
        return state

    def test_latest_generation_on_empty_session_is_none(self, state_path) -> None:
        state = self._seed(state_path)
        assert state.latest_generation("sess-1") is None

    def test_append_generation(self, state_path) -> None:
        state = self._seed(state_path)
        gen = GenerationRecord(
            trace_id="cc_sess-1_g1",
            generation=1,
            captured_size=500,
            captured_mtime=100.0,
            schema_version="0.4.0",
            security_version="0.4.0",
            status_at_capture="inbox",
            supersedes=None,
            supersedes_reason=None,
            created_at="2026-04-15T07:00:00Z",
        )
        state.append_generation("sess-1", gen)

        rec = state.get_session("sess-1")
        assert len(rec.generations) == 1
        assert rec.generations[0].trace_id == "cc_sess-1_g1"
        assert state.latest_generation("sess-1").trace_id == "cc_sess-1_g1"

    def test_append_second_generation_supersedes_first(self, state_path) -> None:
        state = self._seed(state_path)
        state.append_generation("sess-1", GenerationRecord(
            trace_id="cc_sess-1_g1", generation=1, captured_size=500,
            captured_mtime=100.0, schema_version="0.4.0",
            security_version="0.4.0", status_at_capture="uploaded",
            supersedes=None, supersedes_reason=None,
            created_at="2026-04-15T07:00:00Z",
        ))
        state.append_generation("sess-1", GenerationRecord(
            trace_id="cc_sess-1_g2", generation=2, captured_size=800,
            captured_mtime=200.0, schema_version="0.4.0",
            security_version="0.4.0", status_at_capture="inbox",
            supersedes="cc_sess-1_g1", supersedes_reason="resume",
            created_at="2026-04-15T09:00:00Z",
        ))

        latest = state.latest_generation("sess-1")
        assert latest.trace_id == "cc_sess-1_g2"
        assert latest.generation == 2
        assert latest.supersedes == "cc_sess-1_g1"
        assert latest.supersedes_reason == "resume"

    def test_generations_persist_across_reload(self, state_path) -> None:
        state = self._seed(state_path)
        state.append_generation("sess-1", GenerationRecord(
            trace_id="cc_sess-1_g1", generation=1, captured_size=500,
            captured_mtime=100.0, schema_version="0.4.0",
            security_version="0.4.0", status_at_capture="inbox",
            supersedes=None, supersedes_reason=None,
            created_at="2026-04-15T07:00:00Z",
        ))

        reloaded = StateManager(state_path=state_path)
        gen = reloaded.latest_generation("sess-1")
        assert gen is not None
        assert gen.trace_id == "cc_sess-1_g1"
        assert gen.schema_version == "0.4.0"

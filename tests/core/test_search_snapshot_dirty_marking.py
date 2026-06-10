"""Dirty-marking coverage for raw-trace-record mutation paths (issue #22 review).

The read-only search snapshot serves stale-forever results unless every path
that mutates raw trace records marks it dirty. These tests pin the paths the
PR-24 review found unmarked: redact, discard, and bucket remote pull.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.core.test_trace_search_snapshot import _trace, _write_trace
from opentraces.core.review import (
    discard_trace_state_only,
    redact_step_and_persist,
)
from opentraces.core.state import StateManager
from opentraces.core.trace_search_state import current_dirty_token


def _staging_record(staging_dir: Path, trace_id: str) -> Path:
    record = _trace(trace_id, description="Redaction probe with a token value")
    staging_dir.mkdir(parents=True, exist_ok=True)
    path = staging_dir / f"{trace_id}.jsonl"
    path.write_text(record.model_dump_json() + "\n")
    return path


def test_redact_step_marks_snapshot_dirty(tmp_path) -> None:
    staging = tmp_path / "staging"
    _staging_record(staging, "trace-redact")
    assert current_dirty_token() is None

    result = redact_step_and_persist(staging, "trace-redact", 1)

    assert result.ok, result.error
    assert current_dirty_token() is not None


def test_discard_trace_marks_snapshot_dirty(tmp_path) -> None:
    staging = tmp_path / "staging"
    staging_file = _staging_record(staging, "trace-discard")
    state = StateManager(state_path=tmp_path / "state.json")
    assert current_dirty_token() is None

    discard_trace_state_only(state, "trace-discard", staging_file=staging_file)

    assert not staging_file.exists()
    assert current_dirty_token() is not None


def test_fake_remote_pull_marks_snapshot_dirty(tmp_path) -> None:
    from opentraces.core import bucket_remote

    payload = bucket_remote._mark_pull_dirty(
        {"state": "pulled", "files_downloaded": 3}
    )
    assert payload["files_downloaded"] == 3
    assert current_dirty_token() is not None


def test_no_op_remote_pull_does_not_mark(tmp_path) -> None:
    from opentraces.core import bucket_remote

    bucket_remote._mark_pull_dirty({"state": "pulled", "files_downloaded": 0})
    assert current_dirty_token() is None

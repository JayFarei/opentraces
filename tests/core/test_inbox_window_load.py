"""Bug B (driver 2): load_trace_records must not materialise the whole staging
dir to serve a small ``since_iso`` window.

The post-commit hook calls ``load_trace_records(staging, since_iso=2h-ago)``. The
old implementation eagerly loaded every staging row into a dict list (~GBs on a
1000+ row inbox) and only then filtered — so a commit that matched zero recent
traces still paid the full materialisation. These guards pin: (a) the cheap
mtime pre-filter skips reading files clearly older than the window, and (b) the
``timestamp_end`` filter stays authoritative and correct.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from opentraces_schema import Agent, Step, TraceRecord

from opentraces.core.inbox import load_trace_records


def _staged(dir_: Path, trace_id: str, *, end_iso: str, mtime_epoch: float) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    rec = TraceRecord(
        trace_id=trace_id,
        session_id=f"s-{trace_id}",
        agent=Agent(name="claude-code", model="m"),
        task={"description": f"work {trace_id}"},
        steps=[Step(step_index=1, role="user", content="hi")],
        outcome={"success": True, "committed": False},
        timestamp_end=end_iso,
    )
    p = dir_ / f"{trace_id}.jsonl"
    p.write_text(rec.model_dump_json() + "\n")
    os.utime(p, (mtime_epoch, mtime_epoch))
    return p


def test_since_window_returns_only_recent_records(tmp_path):
    staging = tmp_path / "traces"
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=30)).isoformat()
    old = (now - timedelta(days=10)).isoformat()
    _staged(staging, "recent", end_iso=recent, mtime_epoch=time.time())
    _staged(staging, "old", end_iso=old, mtime_epoch=time.time() - 10 * 86400)

    since = (now - timedelta(hours=2)).isoformat()
    recs = load_trace_records(staging, since_iso=since)
    assert [r.trace_id for r in recs] == ["recent"]


def test_old_files_are_not_read_under_a_since_window(tmp_path, monkeypatch):
    """Files whose mtime is well before the window must be skipped via stat,
    never read — that is what bounds memory/time on the post-commit hot path."""
    staging = tmp_path / "traces"
    now = datetime.now(timezone.utc)
    _staged(staging, "recent", end_iso=(now - timedelta(minutes=5)).isoformat(),
            mtime_epoch=time.time())
    for i in range(20):
        _staged(staging, f"old{i}", end_iso=(now - timedelta(days=30)).isoformat(),
                mtime_epoch=time.time() - 30 * 86400)

    read_paths: list[str] = []
    real_read_text = Path.read_text

    def spy_read_text(self, *a, **k):
        read_paths.append(self.name)
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", spy_read_text)

    since = (now - timedelta(hours=2)).isoformat()
    recs = load_trace_records(staging, since_iso=since)

    assert [r.trace_id for r in recs] == ["recent"]
    # The 20 old files (mtime ~30 days ago) must NOT have been read.
    assert not any(name.startswith("old") for name in read_paths), (
        f"old files were read despite the mtime pre-filter: {read_paths}"
    )


def test_no_since_returns_all_records(tmp_path):
    """Without a window, behaviour is unchanged: every valid record is returned."""
    staging = tmp_path / "traces"
    now = datetime.now(timezone.utc)
    _staged(staging, "a", end_iso=now.isoformat(), mtime_epoch=time.time())
    _staged(staging, "b", end_iso=(now - timedelta(days=100)).isoformat(),
            mtime_epoch=time.time() - 100 * 86400)
    recs = load_trace_records(staging)
    assert {r.trace_id for r in recs} == {"a", "b"}


def test_malformed_line_is_skipped(tmp_path):
    staging = tmp_path / "traces"
    staging.mkdir(parents=True)
    (staging / "bad.jsonl").write_text("{ not json\n")
    now = datetime.now(timezone.utc)
    _staged(staging, "good", end_iso=now.isoformat(), mtime_epoch=time.time())
    recs = load_trace_records(staging)
    assert [r.trace_id for r in recs] == ["good"]

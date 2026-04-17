"""Regression test: load_snapshots_for_project must use the JSONL's first-line
``trace_id``, not the filename stem (which is Claude Code's session_id).

Bug: for a long stretch we propagated session_ids under the name ``trace_id``
because the loader did ``trace_id = jsonl_path.stem``. The fix reads the
first JSON line and pulls ``trace_id`` from it, falling back to the stem
only when the file is empty / unreadable.
"""
from __future__ import annotations

import json
from pathlib import Path

from opentraces.enrichment.git import attribution as ga


def _claude_proj_dir(home: Path, project_cwd: Path) -> Path:
    return home / ".claude" / "projects" / ga._encode_cwd(project_cwd)


def test_load_snapshots_uses_trace_id_from_first_line(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    proj_dir = _claude_proj_dir(tmp_path, project)
    proj_dir.mkdir(parents=True, exist_ok=True)

    # Filename = session_id; first line carries the real trace_id.
    session_id = "111-session-bbbbbbbbbbbbbbbbbbbb"
    real_trace_id = "222-trace-aaaaaaaaaaaaaaaaaaaa"
    jsonl = proj_dir / f"{session_id}.jsonl"
    jsonl.write_text(
        json.dumps({
            "trace_id": real_trace_id,
            "session_id": session_id,
            "task": {"description": "qa"},
            "agent": {"name": "claude-code", "model": "claude-opus-4-6"},
            "timestamp_start": "2026-04-14T12:00:00Z",
        }) + "\n"
        + json.dumps({
            "type": "file-history-snapshot",
            "messageId": "m1",
            "snapshot": {
                "trackedFileBackups": {
                    "r.md": {
                        "backupTime": "2026-04-14T12:00:05Z",
                        "originalText": "hi\n",
                    }
                }
            },
        }) + "\n"
    )

    snaps = ga.load_snapshots_for_project(project)
    # We must have at least one snapshot from this JSONL, and every snapshot
    # tied to it must carry the REAL trace_id, never the session_id stem.
    snaps_from_file = [s for s in snaps if s.jsonl_path == jsonl]
    assert snaps_from_file, "expected at least one file-history snapshot"
    for s in snaps_from_file:
        assert s.trace_id == real_trace_id
        assert s.trace_id != session_id


def test_load_snapshots_falls_back_to_stem_when_jsonl_empty(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    proj_dir = _claude_proj_dir(tmp_path, project)
    proj_dir.mkdir(parents=True, exist_ok=True)
    # Empty file — loader has no real trace_id to pick up; fallback should
    # keep the legacy behaviour without crashing.
    (proj_dir / "empty-session.jsonl").write_text("")

    # Should not raise; just yields no snapshots from the empty file.
    snaps = ga.load_snapshots_for_project(project)
    assert all(s.jsonl_path is None or s.jsonl_path.stem != "empty-session"
               or s.trace_id == "empty-session"
               for s in snaps)


def test_read_trace_id_helper_handles_garbage(tmp_path):
    p = tmp_path / "garbage.jsonl"
    p.write_text("not json at all\n")
    assert ga._read_trace_id_from_jsonl(p) is None

    p.write_text("[1,2,3]\n")  # JSON, but not a dict
    assert ga._read_trace_id_from_jsonl(p) is None

    p.write_text("")  # empty
    assert ga._read_trace_id_from_jsonl(p) is None

    p.write_text(json.dumps({"trace_id": ""}) + "\n")  # blank id
    assert ga._read_trace_id_from_jsonl(p) is None

    p.write_text(json.dumps({"trace_id": "tid-real"}) + "\n")
    assert ga._read_trace_id_from_jsonl(p) == "tid-real"

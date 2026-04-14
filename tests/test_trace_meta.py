"""Unit tests for the tolerant trace metadata resolver."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from opentraces.core import trace_meta as tm
from opentraces.core.config import _write_marker, get_project_dir


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("desc,expected", [
    ("now that we have built the teenage milestone, It is time to schedule...",
     "built-teenage-milestone"),
    ("ultrathink use a team of agents to review the instructions",
     "ultrathink-team-agents"),
    ("what remote are we connected to?", "remote-connected"),
    ("base directory for this skill", "base-directory-skill"),
    ("", None),
    (None, None),
    ("!!!", None),
    ("the the the a an of", None),
])
def test_slugify_task_cases(desc, expected):
    assert tm.slugify_task(desc) == expected


def test_slugify_truncates_to_max_len_by_dropping_tokens():
    out = tm.slugify_task("absolutely wonderful magnificent", max_len=15)
    # "absolutely-wonderful" = 20, too long; drop trailing token.
    assert out is not None
    assert len(out) <= 15
    assert not out.endswith("-")
    # Must not slice mid-token.
    for tok in out.split("-"):
        assert tok in {"absolutely", "wonderful", "magnificent"}


# ---------------------------------------------------------------------------
# resolver
# ---------------------------------------------------------------------------

def _mk_project(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_marker(proj, uuid.uuid4().hex, {})
    root = get_project_dir(proj)
    (root / "traces").mkdir(parents=True, exist_ok=True)
    tm.clear_cache()
    return proj, root


def _write_trace(root: Path, filename: str, record: dict) -> None:
    p = root / "traces" / filename
    p.write_text(json.dumps(record) + "\n")


def test_resolve_by_session_id_prefix(tmp_path, monkeypatch):
    proj, root = _mk_project(tmp_path, monkeypatch)
    _write_trace(root, "b73af9c8-6250-4a66-b9ca-8f74f372986e.jsonl", {
        "trace_id": "e670dddc-aaaa-bbbb-cccc-ddddddddeeee",
        "session_id": "b73af9c8-6250-4a66-b9ca-8f74f372986e",
        "task": {"description": "teenage milestone e2e test"},
        "agent": {"model": "anthropic/claude-opus-4-6"},
        "timestamp_start": "2026-04-09T10:00:00Z",
        "steps": [{}, {}, {}],
    })
    meta = tm.resolve_trace_meta(proj, "b73af9c8")
    assert meta is not None
    assert meta.session_id.startswith("b73af9c8")
    assert meta.trace_id.startswith("e670dddc")
    assert meta.short_name is not None
    assert "teenage" in meta.short_name
    assert meta.model == "claude-opus-4-6"
    assert meta.turn_count == 3


def test_resolve_by_trace_id_prefix(tmp_path, monkeypatch):
    proj, root = _mk_project(tmp_path, monkeypatch)
    _write_trace(root, "b73af9c8-6250-4a66-b9ca-8f74f372986e.jsonl", {
        "trace_id": "e670dddc-aaaa-bbbb-cccc-ddddddddeeee",
        "session_id": "b73af9c8-6250-4a66-b9ca-8f74f372986e",
        "task": {"description": "sample task"},
        "agent": {"model": "claude-opus-4-6"},
        "steps": [],
    })
    meta = tm.resolve_trace_meta(proj, "e670dddc")
    assert meta is not None
    assert meta.session_id.startswith("b73af9c8")
    assert meta.trace_id.startswith("e670dddc")


def test_resolve_accepts_t_prefix(tmp_path, monkeypatch):
    proj, root = _mk_project(tmp_path, monkeypatch)
    _write_trace(root, "abc12345-aaaa-bbbb-cccc-dddddddd1234.jsonl", {
        "trace_id": "abc12345-aaaa-bbbb-cccc-dddddddd1234",
        "session_id": "abc12345-aaaa-bbbb-cccc-dddddddd1234",
        "task": {"description": "x"},
        "steps": [],
    })
    meta = tm.resolve_trace_meta(proj, "t:abc12345")
    assert meta is not None


def test_resolve_missing_returns_none(tmp_path, monkeypatch):
    proj, _ = _mk_project(tmp_path, monkeypatch)
    assert tm.resolve_trace_meta(proj, "deadbeef") is None


def test_resolve_cached(tmp_path, monkeypatch):
    proj, root = _mk_project(tmp_path, monkeypatch)
    _write_trace(root, "feedface-aaaa-bbbb-cccc-dddddddd0001.jsonl", {
        "trace_id": "feedface-aaaa-bbbb-cccc-dddddddd0001",
        "session_id": "feedface-aaaa-bbbb-cccc-dddddddd0001",
        "task": {"description": "cached lookup"},
        "steps": [],
    })
    m1 = tm.resolve_trace_meta(proj, "feedface")
    # Delete the file and verify we still get the cached answer.
    (root / "traces" / "feedface-aaaa-bbbb-cccc-dddddddd0001.jsonl").unlink()
    m2 = tm.resolve_trace_meta(proj, "feedface")
    assert m1 == m2
    tm.clear_cache()
    assert tm.resolve_trace_meta(proj, "feedface") is None

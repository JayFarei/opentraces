"""Unit tests for rule-based trace-contribution summarisation."""
from __future__ import annotations

from opentraces.core.entity_join import EntityChange, TraceContribution
from opentraces.core.trace_summary import (
    summarize_contribution,
    summarize_contribution_verbose,
)


def _ent(ct: str, name: str, path: str, et: str = "function", old: str | None = None) -> EntityChange:
    return EntityChange(change_type=ct, entity_type=et, entity_name=name,
                        file_path=path, old_entity_name=old)


def _contrib(entities=None, chunks=None, lc: int = 100) -> TraceContribution:
    return TraceContribution(trace_id="t1", line_count=lc, line_ratio=0.5,
                             entities=entities or [], chunks=chunks or [])


def test_single_addition_no_dir_suffix():
    c = _contrib([_ent("added", "foo", "src/a.py")])
    b = summarize_contribution(c)
    assert b == ["+ Added foo"]


def test_two_additions_same_dir_no_suffix():
    c = _contrib([
        _ent("added", "foo", "src/a.py"),
        _ent("added", "bar", "src/a.py"),
    ])
    b = summarize_contribution(c)
    # 2 entities in one dir -> single bullet with shared dir info
    assert len(b) == 1
    assert "Added" in b[0]
    assert "foo" in b[0] and "bar" in b[0]


def test_three_additions_same_dir_rolls_up():
    c = _contrib([
        _ent("added", "foo", "apps/api/a.py"),
        _ent("added", "bar", "apps/api/a.py"),
        _ent("added", "baz", "apps/api/a.py"),
    ])
    b = summarize_contribution(c)
    assert len(b) == 1
    assert "in apps/api" in b[0]


def test_rename_bullet_shape():
    c = _contrib([_ent("renamed", "new_name", "x.py", old="old_name")])
    b = summarize_contribution(c)
    assert b == ["\u21B7 Renamed old_name \u2192 new_name"]


def test_deleted_verb_is_removed():
    c = _contrib([
        _ent("deleted", "a", "x.py"),
        _ent("deleted", "b", "x.py"),
    ])
    b = summarize_contribution(c)
    assert b[0].startswith("- Removed")


def test_chunk_only_bullet():
    c = _contrib(chunks=[(10, 20, "modified"), (30, 40, "modified")])
    b = summarize_contribution(c)
    assert len(b) == 1
    assert "chunks lines 10-20, 30-40" in b[0]


def test_max_bullets_caps_and_reports_remainder():
    ents = [
        _ent("added", "a", "src/a.py"),
        _ent("added", "b", "src/a.py"),
        _ent("modified", "c", "src/b.py"),
        _ent("modified", "d", "src/b.py"),
        _ent("deleted", "e", "src/c.py"),
        _ent("deleted", "f", "src/c.py"),
    ]
    c = _contrib(ents)
    b = summarize_contribution(c, max_bullets=2)
    assert len(b) == 3  # 2 bullets + remainder line
    assert b[-1].strip().startswith("(+")


def test_empty_with_lines_shows_no_overlap():
    c = _contrib(lc=14)
    b = summarize_contribution(c)
    assert b == ["(no entity overlap on this commit)"]


def test_empty_completely_returns_empty():
    c = _contrib(lc=0)
    b = summarize_contribution(c)
    assert b == []


def test_ascii_only_rename_glyph():
    c = _contrib([_ent("renamed", "new", "x.py", old="old")])
    b = summarize_contribution(c, ascii_only=True)
    assert b[0].startswith("rn ")


def test_verbose_one_line_per_entity():
    ents = [
        _ent("added", "foo", "src/a.py", et="function"),
        _ent("renamed", "new", "src/b.py", et="function", old="old"),
    ]
    out = summarize_contribution_verbose(_contrib(ents))
    assert len(out) == 2
    assert out[0].startswith("+ added function foo")
    assert "src/a.py" in out[0]
    assert "old" in out[1] and "new" in out[1]

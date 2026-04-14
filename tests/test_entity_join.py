"""Unit tests for shape-tolerant entity loader + trace join."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from opentraces.core.cache import AttributionCache
from opentraces.core.config import _write_marker
from opentraces.core.entity_join import (
    EntityChange,
    _normalize_record,
    join_entities_to_traces,
    load_entities,
)


def _mk_project(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_marker(proj, uuid.uuid4().hex, {})
    return proj


# ---------------------------------------------------------------------------
# _normalize_record
# ---------------------------------------------------------------------------

def test_normalize_snake_case():
    r = {
        "change_type": "added", "entity_kind": "function",
        "entity_name": "foo", "file": "x.py",
    }
    n = _normalize_record(r)
    assert isinstance(n, EntityChange)
    assert n.change_type == "added"
    assert n.entity_type == "function"
    assert n.entity_name == "foo"
    assert n.file_path == "x.py"


def test_normalize_camel_case():
    r = {
        "changeType": "modified", "entityType": "struct",
        "entityName": "Bar", "filePath": "src/lib.rs",
        "entityId": "src/lib.rs::struct::Bar",
    }
    n = _normalize_record(r)
    assert n is not None
    assert n.change_type == "modified"
    assert n.entity_type == "struct"
    assert n.entity_name == "Bar"
    assert n.file_path == "src/lib.rs"
    assert n.entity_id == "src/lib.rs::struct::Bar"


def test_normalize_junk_returns_none():
    assert _normalize_record("not a dict") is None


# ---------------------------------------------------------------------------
# load_entities — shape tolerance
# ---------------------------------------------------------------------------

def test_load_entities_snake_case(tmp_path):
    d = tmp_path / "entities"
    d.mkdir()
    sha = "a" * 40
    (d / f"{sha}.json").write_text(json.dumps({
        "entities": [
            {"change_type": "added", "entity_kind": "function",
             "entity_name": "foo", "file": "x.py"},
        ],
    }))
    out = load_entities(d, sha)
    assert len(out) == 1
    assert out[0].entity_name == "foo"


def test_load_entities_camel_case(tmp_path):
    """Real-upstream shape: {"changes": [...]} + camelCase keys."""
    d = tmp_path / "entities"
    d.mkdir()
    sha = "b" * 40
    (d / f"{sha}.json").write_text(json.dumps({
        "changes": [
            {"changeType": "added", "entityType": "struct",
             "entityName": "CatalogRow", "filePath": "apps/gateway/src/db.rs"},
            {"changeType": "modified", "entityType": "function",
             "entityName": "find_catalog", "filePath": "apps/gateway/src/db.rs"},
        ],
        "entities": [],
        "summary": {},
    }))
    out = load_entities(d, sha)
    assert len(out) == 2
    assert out[0].entity_name == "CatalogRow"
    assert out[0].entity_type == "struct"
    assert out[1].change_type == "modified"


def test_load_entities_missing_file(tmp_path):
    assert load_entities(tmp_path, "deadbeef") == []


def test_load_entities_real_upstream_fixture():
    """Trimmed copy of a real upstream cache file — camelCase + changes[]."""
    fixture = Path(__file__).resolve().parent / "fixtures" / "entity_join" / "cache_camelcase"
    out = load_entities(fixture / "entities", "2508ec10eb21361b0bea7f6ca6179958671929aa")
    assert len(out) == 5
    assert {e.change_type for e in out} == {"added", "modified", "renamed"}
    assert any(e.entity_type == "chunk" for e in out)
    # rename metadata preserved
    renamed = [e for e in out if e.change_type == "renamed"][0]
    assert renamed.old_entity_name == "regenerate_token"


# ---------------------------------------------------------------------------
# join — whole-file dominant
# ---------------------------------------------------------------------------

def _write_attribution(proj: Path, sha: str, data: dict) -> AttributionCache:
    cache = AttributionCache(proj)
    cache.write_attribution(sha, data)
    return cache


def _write_entity_cache(cache: AttributionCache, sha: str, obj: dict) -> None:
    p = cache.entity_path(sha)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))


def test_join_whole_file_dominant(tmp_path, monkeypatch):
    proj = _mk_project(tmp_path, monkeypatch)
    sha = "c" * 40
    cache = _write_attribution(proj, sha, {
        "coverage": {"attributed": 3, "total": 3, "ratio": 1.0},
        "traces": [
            {"trace_id": "aaa", "line_count": 2, "files": ["x.py"]},
            {"trace_id": "bbb", "line_count": 1, "files": ["x.py"]},
        ],
        "files": {
            "x.py": {"total": 3, "lines": [
                {"n": 1, "trace_id": "aaa", "consistency": "attributed"},
                {"n": 2, "trace_id": "aaa", "consistency": "attributed"},
                {"n": 3, "trace_id": "bbb", "consistency": "attributed"},
            ]},
        },
    })
    _write_entity_cache(cache, sha, {"changes": [
        {"changeType": "added", "entityType": "function",
         "entityName": "foo", "filePath": "x.py"},
    ]})
    contribs = join_entities_to_traces(proj, sha)
    assert [c.trace_id for c in contribs] == ["aaa", "bbb"]
    # aaa dominates x.py (2 lines vs 1), so foo credits to aaa only.
    aaa = next(c for c in contribs if c.trace_id == "aaa")
    bbb = next(c for c in contribs if c.trace_id == "bbb")
    assert [e.entity_name for e in aaa.entities] == ["foo"]
    assert bbb.entities == []


def test_join_chunk_line_range(tmp_path, monkeypatch):
    proj = _mk_project(tmp_path, monkeypatch)
    sha = "d" * 40
    cache = _write_attribution(proj, sha, {
        "coverage": {"attributed": 4, "total": 4, "ratio": 1.0},
        "traces": [
            {"trace_id": "aaa", "line_count": 2, "files": ["x.py"]},
            {"trace_id": "bbb", "line_count": 2, "files": ["x.py"]},
        ],
        "files": {
            "x.py": {"total": 4, "lines": [
                {"n": 1, "trace_id": "aaa", "consistency": "attributed"},
                {"n": 2, "trace_id": "aaa", "consistency": "attributed"},
                {"n": 10, "trace_id": "bbb", "consistency": "attributed"},
                {"n": 11, "trace_id": "bbb", "consistency": "attributed"},
            ]},
        },
    })
    _write_entity_cache(cache, sha, {"entities": [
        {"change_type": "modified", "entity_kind": "chunk",
         "entity_name": "lines 10-11", "file": "x.py"},
    ]})
    contribs = join_entities_to_traces(proj, sha)
    bbb = next(c for c in contribs if c.trace_id == "bbb")
    aaa = next(c for c in contribs if c.trace_id == "aaa")
    assert bbb.chunks == [(10, 11, "modified")]
    assert aaa.chunks == []


def test_join_tie_credits_both(tmp_path, monkeypatch):
    proj = _mk_project(tmp_path, monkeypatch)
    sha = "e" * 40
    cache = _write_attribution(proj, sha, {
        "coverage": {"attributed": 2, "total": 2, "ratio": 1.0},
        "traces": [
            {"trace_id": "aaa", "line_count": 1, "files": ["x.py"]},
            {"trace_id": "bbb", "line_count": 1, "files": ["x.py"]},
        ],
        "files": {
            "x.py": {"total": 2, "lines": [
                {"n": 1, "trace_id": "aaa", "consistency": "attributed"},
                {"n": 2, "trace_id": "bbb", "consistency": "attributed"},
            ]},
        },
    })
    _write_entity_cache(cache, sha, {"changes": [
        {"changeType": "added", "entityType": "function",
         "entityName": "tied", "filePath": "x.py"},
    ]})
    contribs = join_entities_to_traces(proj, sha)
    assert {c.trace_id for c in contribs} == {"aaa", "bbb"}
    for c in contribs:
        assert [e.entity_name for e in c.entities] == ["tied"]


def test_join_empty_attribution_returns_empty(tmp_path, monkeypatch):
    proj = _mk_project(tmp_path, monkeypatch)
    assert join_entities_to_traces(proj, "f" * 40) == []


# ---------------------------------------------------------------------------
# Overlap-case detection (plan-043 follow-up patch)
# ---------------------------------------------------------------------------

def test_overlap_case_has_entities(tmp_path, monkeypatch):
    """Trace owns >= 1 entity -> overlap_case = 'has_entities'."""
    proj = _mk_project(tmp_path, monkeypatch)
    sha = "1" * 40
    cache = _write_attribution(proj, sha, {
        "coverage": {"attributed": 2, "total": 2, "ratio": 1.0},
        "traces": [{"trace_id": "aaa", "line_count": 2, "files": ["x.py"]}],
        "files": {
            "x.py": {"total": 2, "lines": [
                {"n": 1, "trace_id": "aaa", "consistency": "attributed"},
                {"n": 2, "trace_id": "aaa", "consistency": "attributed"},
            ]},
        },
    })
    _write_entity_cache(cache, sha, {"changes": [
        {"changeType": "added", "entityType": "function",
         "entityName": "foo", "filePath": "x.py"},
    ]})
    contribs = join_entities_to_traces(proj, sha)
    assert contribs[0].overlap_case == "has_entities"


def test_overlap_case_phantom_is_filtered(tmp_path, monkeypatch):
    """trace with line_count == 0 is omitted entirely."""
    proj = _mk_project(tmp_path, monkeypatch)
    sha = "2" * 40
    _write_attribution(proj, sha, {
        "coverage": {"attributed": 1, "total": 1, "ratio": 1.0},
        "traces": [
            {"trace_id": "aaa", "line_count": 1, "files": ["x.py"]},
            {"trace_id": "ghost", "line_count": 0, "files": []},
        ],
        "files": {
            "x.py": {"total": 1, "lines": [
                {"n": 1, "trace_id": "aaa", "consistency": "attributed"},
            ]},
        },
    })
    contribs = join_entities_to_traces(proj, sha)
    assert [c.trace_id for c in contribs] == ["aaa"]


def test_overlap_case_scattered(tmp_path, monkeypatch):
    """Trace contributed lines outside any entity range."""
    proj = _mk_project(tmp_path, monkeypatch)
    sha = "3" * 40
    cache = _write_attribution(proj, sha, {
        "coverage": {"attributed": 3, "total": 3, "ratio": 1.0},
        "traces": [
            {"trace_id": "aaa", "line_count": 2, "files": ["x.py"]},
            {"trace_id": "bbb", "line_count": 1, "files": ["y.py"]},
        ],
        "files": {
            "x.py": {"total": 2, "lines": [
                {"n": 10, "trace_id": "aaa", "consistency": "attributed"},
                {"n": 11, "trace_id": "aaa", "consistency": "attributed"},
            ]},
            "y.py": {"total": 1, "lines": [
                {"n": 1, "trace_id": "bbb", "consistency": "attributed"},
            ]},
        },
    })
    # Entity only on x.py; bbb's contribution on y.py is scattered.
    _write_entity_cache(cache, sha, {"changes": [
        {"changeType": "modified", "entityType": "chunk",
         "entityName": "lines 10-11", "filePath": "x.py"},
    ]})
    contribs = join_entities_to_traces(proj, sha)
    bbb = next(c for c in contribs if c.trace_id == "bbb")
    assert bbb.overlap_case == "scattered"
    assert bbb.dominant_by is None


def test_overlap_case_diffuse(tmp_path, monkeypatch):
    """Trace's lines fall inside an entity but another trace dominates."""
    proj = _mk_project(tmp_path, monkeypatch)
    sha = "4" * 40
    cache = _write_attribution(proj, sha, {
        "coverage": {"attributed": 4, "total": 4, "ratio": 1.0},
        "traces": [
            {"trace_id": "aaa", "line_count": 3, "files": ["x.py"]},
            {"trace_id": "bbb", "line_count": 1, "files": ["x.py"]},
        ],
        "files": {
            "x.py": {"total": 4, "lines": [
                {"n": 1, "trace_id": "aaa", "consistency": "attributed"},
                {"n": 2, "trace_id": "aaa", "consistency": "attributed"},
                {"n": 3, "trace_id": "aaa", "consistency": "attributed"},
                {"n": 4, "trace_id": "bbb", "consistency": "attributed"},
            ]},
        },
    })
    # Chunk covers 1-4; aaa dominates; bbb's line-4 falls inside -> diffuse.
    _write_entity_cache(cache, sha, {"changes": [
        {"changeType": "modified", "entityType": "chunk",
         "entityName": "lines 1-4", "filePath": "x.py"},
    ]})
    contribs = join_entities_to_traces(proj, sha)
    bbb = next(c for c in contribs if c.trace_id == "bbb")
    assert bbb.overlap_case == "diffuse"
    assert bbb.dominant_by == "aaa"

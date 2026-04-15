"""Tests for opentraces.core.cache.AttributionCache (plan 043 phase 1).

Uses a tmp-home monkeypatch so `get_project_dir` lands under tmp_path
without touching the real ~/.opentraces/ tree.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from opentraces.core import cache as cache_mod
from opentraces.core.cache import (
    BLOB_SPILL_THRESHOLD,
    CACHE_VERSION,
    AttributionCache,
)


@pytest.fixture
def project_cwd(tmp_path, monkeypatch):
    """Fresh project dir with an .opentraces.json marker + redirected HOME."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # Re-import path constants pegged to HOME at import time.
    from importlib import reload

    from opentraces.core import paths as _paths
    reload(_paths)
    from opentraces.core import config as _config
    reload(_config)
    reload(cache_mod)

    proj = tmp_path / "proj"
    proj.mkdir()
    # Minimal marker so get_project_dir() is happy.
    (proj / ".opentraces.json").write_text(
        json.dumps({"project_id": "deadbeef" * 4, "policy": {}})
    )
    return proj


def test_paths_live_under_project_dir(project_cwd):
    c = AttributionCache(project_cwd)
    ap = c.attribution_path("abc123")
    assert ap.name == "abc123.json"
    assert ap.parent.name == "attributions"
    ep = c.entity_path("abc123")
    assert ep.parent.name == "entities"
    # Blobs fan out under ab/cd/<full>
    bp = c.blob_path("ab" + "cd" + "ef" * 30)
    assert bp.parent.name == "cd"
    assert bp.parent.parent.name == "ab"


def test_roundtrip_small_attribution(project_cwd):
    c = AttributionCache(project_cwd)
    data = {
        "commit_sha": "abc",
        "project_slug": "slug",
        "traces": [{"trace_id": "t1", "line_count": 2, "files": ["a.py"]}],
        "files": {
            "a.py": {
                "lines": [
                    {"n": 1, "trace_id": "t1", "consistency": "attributed"},
                    {"n": 2, "trace_id": "t1", "consistency": "attributed"},
                ],
                "total": 2,
            }
        },
        "coverage": {"attributed": 2, "total": 2, "ratio": 1.0},
    }
    c.write_attribution("abc", data)
    assert c.has_attribution("abc")
    got = c.read_attribution("abc")
    assert got["files"]["a.py"]["lines"][0]["trace_id"] == "t1"
    assert got["version"] == CACHE_VERSION


def test_read_missing_returns_none(project_cwd):
    c = AttributionCache(project_cwd)
    assert c.read_attribution("ghost") is None
    assert not c.has_attribution("ghost")


def test_list_attributed_shas(project_cwd):
    c = AttributionCache(project_cwd)
    c.write_attribution("aaa", {"files": {}})
    c.write_attribution("bbb", {"files": {}})
    assert c.list_attributed_shas() == ["aaa", "bbb"]


def test_blob_store_is_content_addressed(project_cwd):
    c = AttributionCache(project_cwd)
    content = b"hello world"
    sha = c.write_blob(content)
    assert sha == hashlib.sha256(content).hexdigest()
    # Second write: same digest, no error, blob path exists.
    again = c.write_blob(content)
    assert again == sha
    assert c.blob_path(sha).is_file()
    assert c.read_blob(sha) == content


def test_oversized_lines_spill_to_blob(project_cwd):
    c = AttributionCache(project_cwd)
    # Build lines large enough to exceed the spill threshold.
    big_lines = [
        {"n": i, "trace_id": "t1", "consistency": "attributed"}
        for i in range(1, BLOB_SPILL_THRESHOLD // 40 + 100)
    ]
    data = {
        "commit_sha": "xyz",
        "files": {"big.py": {"lines": big_lines, "total": len(big_lines)}},
    }
    c.write_attribution("xyz", data)
    got = c.read_attribution("xyz")
    info = got["files"]["big.py"]
    assert "lines" not in info
    assert "blob_sha256" in info
    assert info["total"] == len(big_lines)
    # Blob exists and parses back.
    blob = c.read_blob(info["blob_sha256"])
    assert blob is not None
    restored = json.loads(blob)
    assert len(restored) == len(big_lines)


def test_atomic_write_no_tmp_left_behind(project_cwd):
    c = AttributionCache(project_cwd)
    c.write_attribution("abc", {"files": {}})
    # No stray .tmp files.
    leftovers = list((c.root / "attributions").glob("*.tmp"))
    assert leftovers == []


def test_clear_removes_subdirs(project_cwd):
    c = AttributionCache(project_cwd)
    c.write_attribution("abc", {"files": {}})
    c.write_blob(b"payload")
    assert (c.root / "attributions").is_dir()
    assert (c.root / "blobs").is_dir()
    c.clear()
    assert not (c.root / "attributions").exists()
    assert not (c.root / "blobs").exists()


def test_blob_path_rejects_short_sha(project_cwd):
    c = AttributionCache(project_cwd)
    with pytest.raises(ValueError):
        c.blob_path("ab")

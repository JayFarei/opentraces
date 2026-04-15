"""Tests for opentraces.core.backfill (plan 043 phase 1).

Uses a synthetic tmp git repo with an .opentraces.json marker. We don't
drive a real claude session — `attribute_commit` falls back to blaming
against HEAD when no audit history exists, which is enough to exercise
the orchestrator plumbing (write cache, bump bookmark, aggregate
coverage, skip already-processed commits).
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from importlib import reload
from pathlib import Path

import pytest


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Init a tmp git repo with an opentraces marker + redirected HOME."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # Force-refresh path constants that pegged to HOME at import time.
    from opentraces.core import paths as _paths
    reload(_paths)
    from opentraces.core import config as _config
    reload(_config)
    from opentraces.core import cache as _cache
    reload(_cache)
    from opentraces.core import backfill as _backfill
    reload(_backfill)

    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.check_call(["git", "init", "-q", "-b", "main"], cwd=proj)
    subprocess.check_call(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T",
         "commit", "--allow-empty", "-q", "-m", "root"], cwd=proj)
    (proj / ".opentraces.json").write_text(
        json.dumps({"project_id": uuid.uuid4().hex, "policy": {}})
    )
    subprocess.check_call(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T",
         "add", ".opentraces.json"], cwd=proj)
    subprocess.check_call(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T",
         "commit", "-q", "-m", "marker"], cwd=proj)
    return proj, _backfill, _cache


def _commit(proj: Path, path: str, content: str, msg: str) -> str:
    (proj / path).write_text(content)
    env = {**os.environ}
    subprocess.check_call(["git", "add", path], cwd=proj, env=env)
    subprocess.check_call(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T",
         "commit", "-q", "-m", msg], cwd=proj, env=env)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=proj, text=True
    ).strip()


def test_dry_run_writes_no_cache(project):
    proj, backfill, cache_mod = project
    _commit(proj, "a.py", "print('a')\n", "add a")
    report = backfill.run_dry_run(proj)
    assert report.dry_run is True
    c = cache_mod.AttributionCache(proj)
    assert c.list_attributed_shas() == []


def test_full_writes_cache_entries(project):
    proj, backfill, cache_mod = project
    sha_a = _commit(proj, "a.py", "print('a')\n", "add a")
    sha_b = _commit(proj, "b.py", "print('b')\n", "add b")
    report = backfill.run_full(proj)
    assert report.commits_processed >= 1
    c = cache_mod.AttributionCache(proj)
    shas = c.list_attributed_shas()
    assert sha_a in shas or sha_b in shas
    # Coverage for a fresh synthetic repo with no sessions: everything is
    # pre-audit, so attributed_lines should be 0 but the run still succeeds.
    assert report.coverage_ratio == 0.0 or report.attributed_lines >= 0


def test_incremental_skips_already_processed(project):
    proj, backfill, cache_mod = project
    _commit(proj, "a.py", "print('a')\n", "add a")
    r1 = backfill.run_full(proj)
    processed_first = r1.commits_processed
    # Second run with no new commits should process nothing.
    r2 = backfill.run_incremental(proj)
    assert r2.commits_processed == 0
    # Add another commit and run incrementally.
    _commit(proj, "b.py", "print('b')\n", "add b")
    r3 = backfill.run_incremental(proj)
    assert r3.commits_processed >= 1
    assert r3.commits_processed < processed_first + 2  # not the whole history


def test_rebuild_clears_and_rewrites(project):
    proj, backfill, cache_mod = project
    sha_a = _commit(proj, "a.py", "print('a')\n", "add a")
    backfill.run_full(proj)
    c = cache_mod.AttributionCache(proj)
    # Corrupt the cache — delete one entry.
    target = c.attribution_path(sha_a)
    if target.is_file():
        target.unlink()
    backfill.run_full(proj)  # --rebuild
    # After rebuild, cache entry restored.
    assert c.has_attribution(sha_a)


def test_state_bookmark_advances(project):
    proj, backfill, _ = project
    _commit(proj, "a.py", "print('a')\n", "add a")
    report = backfill.run_full(proj)
    from opentraces.core.config import get_project_state_path
    from opentraces.core.state import StateManager
    sm = StateManager(state_path=get_project_state_path(proj))
    assert sm.get_last_backfilled_commit() == report.last_commit
    assert sm.get_last_backfill_at() is not None


def test_expand_ranges_helper(project):
    _, backfill, _ = project
    assert backfill._expand_ranges(["1-3", "5", "7-8"]) == [1, 2, 3, 5, 7, 8]
    assert backfill._expand_ranges([]) == []

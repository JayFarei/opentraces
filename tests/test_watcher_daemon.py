"""Unit tests for opentraces.watcher.daemon."""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest

from opentraces.watcher import daemon as _wd


def _git(*args, cwd):
    subprocess.check_call(["git", "-C", str(cwd), *args],
                          stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)


def _init_project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(["git", "init", "-q", "-b", "main", str(root)],
                          stdout=subprocess.DEVNULL)
    _git("config", "user.email", "t@t", cwd=root)
    _git("config", "user.name", "t", cwd=root)
    (root / ".opentraces.json").write_text(
        json.dumps({"project_id": uuid.uuid4().hex, "policy": {}})
    )
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "init", cwd=root)
    return root


def test_run_once_on_fresh_project_writes_cache(tmp_path):
    p = _init_project(tmp_path / "proj")
    report = _wd.run_once(p)
    assert report.error is None
    assert report.backfill_invoked is True
    # Cache was written for the init commit.
    from opentraces.core.cache import AttributionCache
    head = subprocess.check_output(
        ["git", "-C", str(p), "rev-parse", "HEAD"], text=True
    ).strip()
    assert AttributionCache(p).has_attribution(head)


def test_quiet_tick_is_fast(tmp_path):
    p = _init_project(tmp_path / "proj")
    # Warm up.
    _wd.run_once(p)
    # Second tick — no changes.
    r = _wd.run_once(p)
    assert r.backfill_invoked is False
    assert r.new_commits == 0
    assert r.duration_ms < 200  # generous; spec target is 50ms, CI jitters
    assert r.error is None


def test_non_enlisted_project_is_noop(tmp_path):
    root = tmp_path / "orphan"
    root.mkdir()
    subprocess.check_call(["git", "init", "-q", str(root)],
                          stdout=subprocess.DEVNULL)
    # No .opentraces.json marker.
    r = _wd.run_once(root)
    assert r.backfill_invoked is False
    assert r.error is None


def test_crash_after_probe_does_not_advance_state(tmp_path, monkeypatch):
    p = _init_project(tmp_path / "proj")
    # First tick — succeeds.
    _wd.run_once(p)
    # Add a new commit.
    (p / "new.txt").write_text("hi\n")
    _git("add", "-A", cwd=p)
    _git("commit", "-q", "-m", "add new", cwd=p)
    # Arm crash.
    monkeypatch.setattr(_wd, "_CRASH_AFTER_PROBE", "after_probe")
    r = _wd.run_once(p)
    assert r.error is not None
    # last_backfilled_commit should still point at the init commit, not HEAD.
    from opentraces.core.config import get_project_state_path
    from opentraces.core.state import StateManager
    sm = StateManager(state_path=get_project_state_path(p))
    last = sm.get_last_backfilled_commit()
    head = subprocess.check_output(
        ["git", "-C", str(p), "rev-parse", "HEAD"], text=True
    ).strip()
    assert last != head


def test_crash_recovery_second_tick_completes(tmp_path, monkeypatch):
    p = _init_project(tmp_path / "proj")
    _wd.run_once(p)  # warmup
    (p / "new.txt").write_text("hi\n")
    _git("add", "-A", cwd=p)
    _git("commit", "-q", "-m", "add new", cwd=p)
    monkeypatch.setattr(_wd, "_CRASH_AFTER_PROBE", "after_probe")
    _wd.run_once(p)  # crashes
    monkeypatch.setattr(_wd, "_CRASH_AFTER_PROBE", None)
    r = _wd.run_once(p)  # recovers
    assert r.error is None
    assert r.backfill_invoked is True
    from opentraces.core.cache import AttributionCache
    head = subprocess.check_output(
        ["git", "-C", str(p), "rev-parse", "HEAD"], text=True
    ).strip()
    assert AttributionCache(p).has_attribution(head)


def test_discover_enlisted_projects_reads_manifest(tmp_path, monkeypatch):
    # Write a project.json into the test PROJECTS_DIR.
    from opentraces.core import config as _cfg
    proj_src = tmp_path / "src"
    proj_src.mkdir()
    slug = _cfg.PROJECTS_DIR / "src-abc"
    slug.mkdir(parents=True)
    (slug / "project.json").write_text(json.dumps({"project_dir": str(proj_src)}))
    monkeypatch.setattr("opentraces.watcher.daemon.PROJECTS_DIR", _cfg.PROJECTS_DIR)
    found = _wd.discover_enlisted_projects()
    assert proj_src in found


def test_interval_config_override(tmp_path):
    p = _init_project(tmp_path / "proj")
    (p / ".opentraces.json").write_text(json.dumps({
        "project_id": uuid.uuid4().hex,
        "watcher": {"interval_seconds": 600},
    }))
    assert _wd._watcher_interval_for(p) == 600


def test_interval_config_default_when_missing(tmp_path):
    p = _init_project(tmp_path / "proj")
    assert _wd._watcher_interval_for(p) == _wd.DEFAULT_INTERVAL

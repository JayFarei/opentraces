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


def test_jsonl_activity_probe_recurses_into_nested_subagent_files(
    tmp_path, monkeypatch
):
    p = _init_project(tmp_path / "proj")
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))

    corpus_dir = _wd._claude_jsonl_dir(p)
    nested = corpus_dir / "main-session" / "subagents"
    nested.mkdir(parents=True)
    (nested / "sub-agent-1.jsonl").write_text("{}\n")

    assert _wd._jsonl_activity_since(p, None) is True


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


def test_discover_enlisted_projects_reads_manifest(tmp_path):
    # Write a project.json into the test PROJECTS_DIR. No explicit daemon
    # monkeypatch: discover_enlisted_projects reads ``_config.PROJECTS_DIR`` at
    # call time, so the conftest autouse isolation fixture already redirects it
    # (#23 step 6 — daemon no longer captures PROJECTS_DIR by value at import).
    from opentraces.core import config as _cfg
    proj_src = tmp_path / "src"
    proj_src.mkdir()
    slug = _cfg.PROJECTS_DIR / "src-abc"
    slug.mkdir(parents=True)
    (slug / "project.json").write_text(json.dumps({"project_dir": str(proj_src)}))
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


def test_gate_treats_anchored_pair_without_search_record_as_satisfied(tmp_path):
    """#23 step 4 (gate/worker asymmetry / livelock):

    The worker (reconcile_commit_anchors) skips a (patch, commit) pair when
    EITHER a search record OR an anchor already exists. If the gate
    (has_unsearched_recent_patches) only consulted search records, a pair with
    an anchor-but-no-search-record (e.g. a legacy log, or an anchor written
    under a different attribution version) would read as "unsearched" forever:
    the gate keeps firing maturation, the worker keeps skipping, no new search
    is ever recorded -> the daemon livelocks on that project every tick.

    With the fix the gate also consults git_anchor_created, so an anchored pair
    with no search record is treated as satisfied and the gate returns False.
    """
    from opentraces.core.trails import (
        TrailEventDraft,
        append_event_batch,
    )
    from opentraces.core.trails.ids import git_anchor_ref, trace_patch_ref
    from opentraces.core.trails.maturation import has_unsearched_recent_patches
    from opentraces.core.trails.models import sha256_text

    p = _init_project(tmp_path / "proj")
    head = subprocess.check_output(
        ["git", "-C", str(p), "rev-parse", "HEAD"], text=True
    ).strip()
    commit_id = {"algo": "sha1", "hex": head}

    authored = "livelock authored line\n"
    patch_id = "livelock-patch"
    anchor_id = "anchor-livelock"
    append_event_batch(
        p,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-livelock",
                step_index=1,
                capture_method=["watcher_backstop"],
                payload={
                    "trace_patch_id": patch_id,
                    "file_path": "ghost.py",
                    "affected_range": {"start_line": 1, "end_line": 1},
                    "authored_text": authored,
                    "raw_authored_hash": sha256_text(authored),
                    "git_clean_hash": sha256_text(" ".join(authored.split())),
                    "limitations": [],
                },
            ),
            # A git_anchor_created for (patch, HEAD) with NO accompanying
            # git_anchor_search_completed record.
            TrailEventDraft(
                event_type="git_anchor_created",
                trace_id="tr-livelock",
                step_index=1,
                capture_method=["post_commit_correlator"],
                payload={
                    "git_anchor_id": anchor_id,
                    "git_anchor_ref": git_anchor_ref(anchor_id),
                    "trace_patch_id": patch_id,
                    "trace_patch_ref": trace_patch_ref(patch_id),
                    "commit_id": commit_id,
                    "path": "ghost.py",
                    "range": {"start_line": 1, "end_line": 1},
                    "relation": "anchored_in_git",
                    "evidence_tier": "exact_range_hash",
                    "evidence_firmness": "firm",
                    "source": "test-fixture",
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )

    # The (patch, HEAD) pair is anchored, so the gate must NOT report it as
    # unsearched even though no search record exists.
    assert has_unsearched_recent_patches(p) is False

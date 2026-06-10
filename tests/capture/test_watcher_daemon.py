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


# --------------------------------------------------------------------------- #
# #45 — per-tick cache invalidation + RSS backstop.
# --------------------------------------------------------------------------- #


def test_run_once_drops_read_events_cache_for_repo(tmp_path):
    """After a tick the daemon must hold NO parsed-event memo for that repo, so
    the long-lived daemon's RSS floor is not the largest repo's full parsed log
    held alive across ticks (#45 part 2c)."""
    import opentraces.core.trails.event_log as event_log

    p = _init_project(tmp_path / "proj")
    # Warm-up tick populates and (per the finally) clears the cache.
    _wd.run_once(p)

    repo_key = str(Path(p).resolve())
    leftover = [k for k in event_log._READ_EVENTS_CACHE if k[0] == repo_key]
    assert leftover == [], (
        "run_once must invalidate the read_events cache for the ticked repo "
        f"(found {leftover})"
    )


def test_run_forever_rss_backstop_reexecs_after_sweep(tmp_path, monkeypatch):
    """When CURRENT RSS breaches OT_WATCHER_MAX_RSS_MB the service loop must
    re-exec ONCE, only AFTER the sweep (never mid-tick), preserving argv +
    interval (#45 part 3)."""
    p = _init_project(tmp_path / "proj")

    monkeypatch.setenv("OT_WATCHER_MAX_RSS_MB", "1")
    # Report a huge current RSS so the backstop trips on the first check.
    monkeypatch.setattr(_wd, "_current_rss_mb", lambda: 999_999.0)

    events: list[str] = []

    real_run_once = _wd.run_once

    def _tracking_run_once(project_cwd, **kwargs):
        events.append(f"tick:{Path(project_cwd).name}")
        return real_run_once(project_cwd, **kwargs)

    monkeypatch.setattr(_wd, "run_once", _tracking_run_once)

    class _ReExec(Exception):
        pass

    recorded = {}

    def _fake_execv(path, argv):
        recorded["path"] = path
        recorded["argv"] = list(argv)
        events.append("reexec")
        raise _ReExec()  # break the otherwise-infinite service loop

    monkeypatch.setattr(_wd.os, "execv", _fake_execv)
    # Make a stray sleep loud rather than silently hanging the test.
    monkeypatch.setattr(_wd.time, "sleep", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("re-exec must happen before sleep")))

    import pytest

    with pytest.raises(_ReExec):
        _wd.run_forever(interval=300, projects=[p])

    # The tick ran, THEN the re-exec — never the reverse, never mid-tick.
    assert events == [f"tick:{p.name}", "reexec"], events
    # argv preserves the run-forever entrypoint + the interval.
    assert recorded["path"] == _wd.sys.executable
    assert recorded["argv"][:4] == [
        _wd.sys.executable, "-m", "opentraces.watcher.daemon", "run-forever",
    ]
    assert "--interval" in recorded["argv"]
    i = recorded["argv"].index("--interval")
    assert recorded["argv"][i + 1] == "300"


def test_run_forever_no_reexec_below_ceiling(tmp_path, monkeypatch):
    """Below the ceiling the loop must NOT re-exec — it sleeps and continues."""
    p = _init_project(tmp_path / "proj")
    monkeypatch.setenv("OT_WATCHER_MAX_RSS_MB", "4096")
    monkeypatch.setattr(_wd, "_current_rss_mb", lambda: 10.0)
    monkeypatch.setattr(_wd, "run_once", lambda *a, **k: None)

    def _no_execv(*a, **k):
        raise AssertionError("must not re-exec below the RSS ceiling")

    monkeypatch.setattr(_wd.os, "execv", _no_execv)

    class _Stop(Exception):
        pass

    def _stop_sleep(*_a, **_k):
        raise _Stop()  # break the loop after one clean sweep

    monkeypatch.setattr(_wd.time, "sleep", _stop_sleep)

    import pytest

    with pytest.raises(_Stop):
        _wd.run_forever(interval=300, projects=[p])


# --------------------------------------------------------------------------- #
# #45 — memory-bound proof (env-gated, NOT default CI; like the perf suites).
#
# Run ``run_once`` against a /tmp copy of a large real event-log repo in a fresh
# subprocess and assert ru_maxrss stays bounded. Before #45 the daemon path
# materialised the full parsed log (two graphs on a cache-flag alternation) —
# ~318MB+ on the kb corpus, extrapolating to ~8.5GB on the main repo. With
# scoped reads + per-tick invalidation the floor is a fraction of that.
#
# Gated behind OT_WATCHER_MEM_PROOF=1 AND OT_WATCHER_MEM_PROOF_REPO=<path to a
# real git repo carrying refs/opentraces/local/events/v1>. Skips otherwise.
# --------------------------------------------------------------------------- #


def test_run_once_memory_bound_on_large_corpus(tmp_path):
    import os as _os

    if _os.environ.get("OT_WATCHER_MEM_PROOF") != "1":
        import pytest
        pytest.skip("set OT_WATCHER_MEM_PROOF=1 to run the memory-bound proof")

    src_repo = _os.environ.get("OT_WATCHER_MEM_PROOF_REPO")
    if not src_repo or not Path(src_repo).is_dir():
        import pytest
        pytest.skip("set OT_WATCHER_MEM_PROOF_REPO to a large event-log repo")

    # Default ceiling 1024MB: the verifier measured ~721MB peak on the kb
    # corpus (cold caches: first-tick scoped reads + git subprocess buffers),
    # so 500MB false-alarmed. The point of the proof is "a fraction of the
    # ~8.5GB full-graph floor", not an exact byte budget; override with
    # OT_WATCHER_MEM_PROOF_MAX_MB for tighter machines.
    max_mb = float(_os.environ.get("OT_WATCHER_MEM_PROOF_MAX_MB", "1024"))

    # Copy the source .git to /tmp so we never reconcile against a live repo.
    work = tmp_path / "corpus"
    work.mkdir()
    subprocess.check_call(
        ["git", "clone", "--mirror", str(src_repo), str(work / ".git")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # Convert the bare mirror into a normal worktree skeleton enough for a tick.
    subprocess.check_call(
        ["git", "-C", str(work), "config", "core.bare", "false"]
    )
    subprocess.check_call(
        ["git", "-C", str(work), "checkout", "-f"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    (work / ".opentraces.json").write_text(json.dumps(
        {"project_id": uuid.uuid4().hex, "policy": {}}
    ))

    # Run run_once in a fresh subprocess and read its peak RSS (ru_maxrss).
    driver = (
        "import resource, sys, json\n"
        "from pathlib import Path\n"
        "from opentraces.watcher import daemon as d\n"
        f"d.run_once(Path({str(work)!r}))\n"
        "peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
        "import platform\n"
        # macOS reports bytes, Linux KiB.
        "mb = peak / (1024*1024) if platform.system()=='Darwin' else peak/1024\n"
        "print(json.dumps({'peak_mb': mb}))\n"
    )
    env = dict(_os.environ)
    out = subprocess.check_output(
        [_wd.sys.executable, "-c", driver], env=env, text=True
    )
    peak_mb = json.loads(out.strip().splitlines()[-1])["peak_mb"]
    assert peak_mb < max_mb, (
        f"run_once peak RSS {peak_mb:.0f}MB exceeded the {max_mb:.0f}MB ceiling "
        "— the daemon path is materialising the full parsed log again (#45)"
    )

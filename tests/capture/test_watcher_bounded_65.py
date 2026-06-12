"""#65 — bounded-by-construction watcher sweep, pruning, provenance.

Pins the four structural bounds added for #65:

* budgeted child ticks: a real child process breaching its RSS or wall budget
  is killed (REAL processes, REAL `ps` readings — no monkeypatched meters)
  and the sweep continues;
* the run-forever backstop fires MID-SWEEP using the real RSS reader (the
  between-sweeps-only check was unreachable on real machines);
* enlistment liveness pruning (#23 finished): tmp-rooted/stale and
  missing-target/stale enlistments are quarantined, live ones kept;
* provenance: the daemon self-declares {version, executable, pid} and doctor
  flags shim/daemon drift (the pipx→brew failure mode).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

import opentraces.watcher.daemon as wd
from opentraces.watcher.prune import prune_enlistments


# --- budgeted child ticks ---------------------------------------------------


def test_child_killed_on_rss_budget(tmp_path):
    """A child that allocates past its RSS budget is killed; verdict says so."""
    allocator = (
        "import time\n"
        "buf = bytearray(300 * 1024 * 1024)\n"
        "time.sleep(30)\n"
    )
    t0 = time.monotonic()
    verdict = wd._tick_in_child(
        tmp_path, budget_mb=100, timeout_s=25,
        _argv=[sys.executable, "-c", allocator],
    )
    elapsed = time.monotonic() - t0
    assert verdict == "rss_killed"
    assert elapsed < 20, "kill must come from the RSS budget, not the timeout"


def test_child_killed_on_wall_budget(tmp_path):
    """A hanging child is killed at the wall budget."""
    verdict = wd._tick_in_child(
        tmp_path, budget_mb=10_000, timeout_s=1.5,
        _argv=[sys.executable, "-c", "import time; time.sleep(30)"],
    )
    assert verdict == "timeout_killed"


def test_child_ok_and_error_verdicts(tmp_path):
    ok = wd._tick_in_child(
        tmp_path, budget_mb=10_000, timeout_s=30,
        _argv=[sys.executable, "-c", "pass"],
    )
    err = wd._tick_in_child(
        tmp_path, budget_mb=10_000, timeout_s=30,
        _argv=[sys.executable, "-c", "import sys; sys.exit(3)"],
    )
    assert ok == "ok"
    assert err == "error:3"


def test_run_sweep_continues_past_killed_child(tmp_path, monkeypatch):
    """One pathological project must not stop the sweep: the summary records
    the kill and the remaining projects still tick."""
    projects = [tmp_path / "a", tmp_path / "b", tmp_path / "c"]
    for p in projects:
        p.mkdir()

    calls: list[Path] = []

    def _fake_child(project_cwd, *, budget_mb, timeout_s, _argv=None):
        calls.append(project_cwd)
        return "rss_killed" if project_cwd.name == "b" else "ok"

    monkeypatch.setattr(wd, "_tick_in_child", _fake_child)
    summary = wd.run_sweep(projects=projects)
    assert [p.name for p in calls] == ["a", "b", "c"]
    assert summary["ok"] == 2
    assert summary["rss_killed"] == 1


def test_run_sweep_in_process_uses_run_once(tmp_path, monkeypatch):
    ticked: list[Path] = []
    monkeypatch.setattr(wd, "run_once", lambda p: ticked.append(p))
    summary = wd.run_sweep(projects=[tmp_path], in_process=True)
    assert ticked == [tmp_path]
    assert summary["ok"] == 1


# --- mid-sweep backstop reachability (REAL RSS reader) -----------------------


def test_run_forever_backstop_fires_mid_sweep_with_real_rss(monkeypatch, tmp_path):
    """The #45 backstop must be reachable DURING a sweep: with the REAL
    ``_current_rss_mb`` reader and a ceiling set just above the current
    process RSS, a run_once that genuinely allocates must trip the re-exec
    before the sweep finishes. (The prior tests monkeypatched the RSS reader,
    which proved the trip wiring but not reachability — the #65 failure.)"""
    baseline = wd._current_rss_mb()
    assert baseline is not None, "real RSS reader must work on this platform"
    monkeypatch.setenv("OT_WATCHER_MAX_RSS_MB", str(int(baseline + 120)))

    hoard: list[bytearray] = []
    projects = [tmp_path / f"p{i}" for i in range(5)]
    for p in projects:
        p.mkdir()
    ticked: list[str] = []

    def _allocating_run_once(project):
        ticked.append(project.name)
        hoard.append(bytearray(200 * 1024 * 1024))  # genuinely grow RSS

    class _ReExec(Exception):
        pass

    def _fake_reexec(interval):
        raise _ReExec()

    monkeypatch.setattr(wd, "run_once", _allocating_run_once)
    monkeypatch.setattr(wd, "_reexec_self", _fake_reexec)
    with pytest.raises(_ReExec):
        wd.run_forever(interval=1, projects=projects)
    hoard.clear()
    # The trip happened mid-sweep: not all 5 projects were ticked first.
    assert 1 <= len(ticked) < 5, (
        f"backstop fired after {len(ticked)} projects; "
        "must be reachable before the sweep completes"
    )


# --- enlistment pruning (#23 finished) ---------------------------------------


def _make_enlistment(projects_dir: Path, slug: str, target: Path,
                     *, age_days: float) -> Path:
    d = projects_dir / slug
    d.mkdir(parents=True)
    (d / "project.json").write_text(json.dumps({"path": str(target)}))
    (d / "state.json").write_text("{}")
    old = time.time() - age_days * 86400
    for p in (d / "project.json", d / "state.json", d):
        os.utime(p, (old, old))
    return d


def test_prune_quarantines_leaks_keeps_live(tmp_path, monkeypatch, _isolate_opentraces_global_state):
    import opentraces.watcher.prune as prune_mod
    from opentraces.core import paths as _paths

    # pytest tmpdirs are themselves tmp-rooted on macOS (/private/var/folders),
    # which would make EVERY fixture target "leaked". Pin the tmp-root set to
    # a controlled directory so the policy itself is what's under test.
    tmp_root = tmp_path / "tmproot"
    tmp_root.mkdir()
    monkeypatch.setattr(prune_mod, "_tmp_roots", lambda: [tmp_root.resolve()])

    projects_dir = _paths.PROJECTS_DIR
    live_target = tmp_path / "live-project"
    live_target.mkdir()
    tmp_target = tmp_root / "ot-65-test-leak"
    tmp_target.mkdir()

    live = _make_enlistment(projects_dir, "live-aaaa", live_target, age_days=30)
    leak_tmp = _make_enlistment(projects_dir, "ot-h-leak-bbbb", tmp_target, age_days=30)
    leak_missing = _make_enlistment(
        projects_dir, "gone-cccc", tmp_path / "deleted-project", age_days=30
    )
    fresh_missing = _make_enlistment(
        projects_dir, "fresh-dddd", tmp_path / "also-deleted", age_days=1
    )

    summary = prune_enlistments()
    assert summary["pruned_tmp"] == 1
    assert summary["pruned_missing"] == 1
    assert summary["kept"] == 2  # live + fresh-missing
    assert live.is_dir()
    assert fresh_missing.is_dir()
    assert not (projects_dir / "ot-h-leak-bbbb").exists()
    assert not (projects_dir / "gone-cccc").exists()
    quarantine = _paths.OPENTRACES_DIR / "pruned_projects"
    assert (quarantine / "ot-h-leak-bbbb").is_dir()
    assert (quarantine / "gone-cccc").is_dir()


def test_prune_dry_run_and_disable(tmp_path, monkeypatch, _isolate_opentraces_global_state):
    import opentraces.watcher.prune as prune_mod
    from opentraces.core import paths as _paths

    # Pin tmp roots away from pytest's own tmpdir (see test above).
    monkeypatch.setattr(
        prune_mod, "_tmp_roots", lambda: [(tmp_path / "tmproot").resolve()]
    )
    projects_dir = _paths.PROJECTS_DIR
    leak = _make_enlistment(
        projects_dir, "gone-dry", tmp_path / "deleted", age_days=30
    )
    dry = prune_enlistments(dry_run=True)
    assert dry["pruned_missing"] == 1 and leak.is_dir()

    monkeypatch.setenv("OT_WATCHER_PRUNE", "0")
    disabled = prune_enlistments()
    assert disabled.get("disabled") is True and leak.is_dir()


# --- provenance: status file + doctor drift ----------------------------------


def test_run_sweep_writes_status_file(_isolate_opentraces_global_state):
    from opentraces.core import paths as _paths

    wd.run_sweep(projects=[])
    status_path = _paths.OPENTRACES_DIR / "watcher.status.json"
    assert status_path.is_file()
    declared = json.loads(status_path.read_text())
    assert declared["schema_version"] == "opentraces.watcher_status.v1"
    assert declared["executable"] == sys.executable
    assert declared["pid"] == os.getpid()
    assert declared["verb"] == "run-sweep"


def test_doctor_flags_dead_shim_interpreter_and_version_drift(
    _isolate_opentraces_global_state,
):
    from opentraces.core import paths as _paths
    from opentraces.core.doctor import _watcher_provenance

    bin_dir = _paths.OPENTRACES_DIR / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "ot-watcher").write_text(
        '#!/bin/sh\n'
        'exec "/nonexistent/pipx/venvs/opentraces/bin/python" '
        '-m opentraces.watcher.daemon run-forever "$@"\n'
    )
    (_paths.OPENTRACES_DIR / "watcher.status.json").write_text(json.dumps({
        "schema_version": "opentraces.watcher_status.v1",
        "version": "0.0.1-definitely-not-installed",
        "executable": "/also/nonexistent/python",
        "started_at": "2026-06-12T00:00:00Z",
    }))

    out = _watcher_provenance()
    assert "shim-interpreter-missing" in out["drift"]
    assert "shim-legacy-verb" in out["drift"]
    assert "daemon-executable-missing" in out["drift"]
    if out.get("cli_version"):
        assert "daemon-version-drift" in out["drift"]


def test_current_shim_renders_runtime_resolution():
    from opentraces.watcher.installer import _render_shim

    shim = _render_shim()
    assert "command -v opentraces" in shim
    assert "setup watcher sweep" in shim
    assert "/opt/homebrew/bin/opentraces" in shim
    assert "run-sweep" in shim  # fallback verb is one-shot too


# --- maturation per-tick budget ----------------------------------------------


def test_maturation_deadline_truncates_and_skips_watermark(tmp_path):
    import subprocess as sp

    from opentraces.core.trails.maturation import (
        _load_maturation_watermark,
        mature_trails,
    )

    from opentraces.core.trails import TrailEventDraft, append_event_batch

    repo = tmp_path / "repo"
    repo.mkdir()
    sp.run(["git", "init", "-q"], cwd=repo, check=True)
    sp.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    sp.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x\n")
    sp.run(["git", "add", "-A"], cwd=repo, check=True)
    sp.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
    # The watermark only stamps when an event log exists (state includes the
    # event-log head) — give the repo one event.
    append_event_batch(repo, [TrailEventDraft(
        event_type="trace_patch_created", trace_id="tr1", step_index=1,
        capture_method=["hook_posttooluse"],
        payload={"trace_patch_id": "tracepatch-sha256:wm", "file_path": "f.txt",
                 "authored_text": "x\n"},
    )], writer="test")

    expired = mature_trails(repo, deadline=time.monotonic() - 1)
    assert expired.truncated is True
    assert _load_maturation_watermark(repo) is None

    full = mature_trails(repo)
    assert full.truncated is False
    assert _load_maturation_watermark(repo) is not None


# --- #65 codex P1: the quiet-tick gate must stream, never materialise --------


def test_gate_streams_through_sink_never_materialises(tmp_path, monkeypatch):
    """#65 codex P1 sentinel: on a truncated-backlog repo the maturation
    watermark is deliberately unstamped, so EVERY quiet tick re-enters
    has_unsearched_recent_patches before the budgeted worker. The gate must
    route its scoped read through a streaming sink (key extraction only) —
    a materialised event list here re-opens the multi-GB path and gets the
    child RSS-killed before the budgeted mature_trails can make progress.
    """
    import subprocess as sp

    from opentraces.core.trails import TrailEventDraft, append_event_batch
    from opentraces.core.trails import maturation as mat_mod

    repo = tmp_path / "repo"
    repo.mkdir()
    sp.run(["git", "init", "-q"], cwd=repo, check=True)
    sp.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    sp.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x\n")
    sp.run(["git", "add", "-A"], cwd=repo, check=True)
    sp.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
    append_event_batch(repo, [TrailEventDraft(
        event_type="trace_patch_created", trace_id="tr1", step_index=1,
        capture_method=["hook_posttooluse"],
        payload={"trace_patch_id": "tracepatch-sha256:gate", "file_path": "f.txt",
                 "authored_text": "x\n"},
    )], writer="test")

    real_scoped = mat_mod.read_events_scoped
    calls: list[bool] = []

    def _spy(repo_arg, **kwargs):
        calls.append(kwargs.get("sink") is not None)
        assert kwargs.get("sink") is not None, (
            "gate called read_events_scoped WITHOUT a sink — "
            "materialised slice on the per-quiet-tick path"
        )
        return real_scoped(repo_arg, **kwargs)

    monkeypatch.setattr(mat_mod, "read_events_scoped", _spy)
    result = mat_mod.has_unsearched_recent_patches(repo)
    assert calls, "gate must reach the scoped read (no watermark yet)"
    assert result is True  # one patch, never searched


# --- #65 soak run 1: sweep wall budget + fair rotation ------------------------


def test_sweep_budget_defers_tail_lru_order(tmp_path, monkeypatch):
    """Soak run 1 sentinel: a sweep over more projects than its wall budget
    allows must (a) stop at the budget, (b) report the deferred count, and
    (c) have visited the LEAST recently ticked projects first so deferred
    tails lead the next sweep."""
    projects = []
    for i in range(4):
        p = tmp_path / f"p{i}"
        p.mkdir()
        projects.append(p)

    # LRU signal: p2 never ticked (0), p0 old, p3 recent, p1 newest.
    mtimes = {"p2": 0.0, "p0": 100.0, "p3": 200.0, "p1": 300.0}
    monkeypatch.setattr(wd, "_last_tick_mtime",
                        lambda p: mtimes.get(p.name, 0.0))
    monkeypatch.setenv("OT_WATCHER_SWEEP_BUDGET_S", "0.45")

    ticked: list[str] = []

    def _slow_child(project_cwd, *, budget_mb, timeout_s, _argv=None):
        ticked.append(project_cwd.name)
        time.sleep(0.25)
        return "ok"

    monkeypatch.setattr(wd, "_tick_in_child", _slow_child)
    summary = wd.run_sweep(projects=projects)
    # 0.45s budget / 0.25s per tick → exactly 2 ticks fit.
    assert ticked == ["p2", "p0"], "must visit least-recently-ticked first"
    assert summary["ok"] == 2
    assert summary["deferred"] == 2


def test_maturation_deadline_pre_scan_bail(tmp_path):
    """Codex P2 sentinel: an already-expired budget returns truncated WITHOUT
    paying the shared scan (no git work at all on a spent budget)."""
    from opentraces.core.trails.maturation import mature_trails

    # Not even a git repo — if the pre-scan bail works, nothing touches it.
    summary = mature_trails(tmp_path, deadline=time.monotonic() - 1)
    assert summary.truncated is True
    assert summary.commits_considered == 0


def test_shim_verifies_candidate_supports_sweep_verb():
    """Codex P2 sentinel: the shim must probe each CLI candidate for the
    sweep verb before exec'ing it — an older CLI would otherwise be exec'd,
    fail every interval, and silently stop the watcher post-migration."""
    from opentraces.watcher.installer import _render_shim

    shim = _render_shim()
    assert 'setup watcher sweep --help >/dev/null 2>&1' in shim
    # The verify must gate the SAME candidate that gets exec'd.
    probe_idx = shim.index("sweep --help")
    exec_idx = shim.index('exec "$c" setup watcher sweep')
    assert probe_idx < exec_idx

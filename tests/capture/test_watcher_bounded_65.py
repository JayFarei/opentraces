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
import subprocess
import sys
import time
import uuid
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
    _make_enlistment(projects_dir, "ot-h-leak-bbbb", tmp_target, age_days=30)
    _make_enlistment(
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
    # Shrink the real-world defer floor so the sub-second test budget drives
    # the rotation (codex round 3 added MIN_CHILD_BUDGET_S=30s).
    monkeypatch.setattr(wd, "MIN_CHILD_BUDGET_S", 0.05)

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


def test_cold_backlog_drains_across_truncated_ticks(tmp_path, monkeypatch):
    """#65 honest cure sentinel — the LIVELOCK discriminator.

    A maturation backlog that cannot finish inside a single tick's deadline must
    make FORWARD PROGRESS across ticks (drain), not discard every commit it
    computed on truncation and re-do the same work forever. That all-or-nothing
    discard (``_flush_maturation_scratch`` gated on ``not truncated`` + a per-tick
    re-walk of already-searched commits) is the exact >16GB / pegged-core #65
    recurrence shape.

    Deterministic by construction: ``time.monotonic`` is replaced in both the
    maturation and anchors modules with a fake clock that advances a fixed step
    per call, so truncation fires at the same point on every machine regardless
    of real speed. The bounded per-tick window only ever covers a few commits, so
    the 8-commit backlog MUST span several ticks.

    FAILS on the pre-fix code (the backlog never drains — every tick recomputes
    and throws away the same prefix). PASSES once truncated ticks flush their
    completed work AND already-covered commits are skipped before the loop.
    """
    import subprocess as sp

    from opentraces.core.trails import TrailEventDraft, append_event_batch
    from opentraces.core.trails import anchors as anchors_mod
    from opentraces.core.trails import maturation as mat

    repo = tmp_path / "repo"
    repo.mkdir()
    sp.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    sp.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    sp.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    n_commits = 8
    for i in range(n_commits):
        (repo / "f.txt").write_text(f"line {i}\nbody {i}\n")
        sp.run(["git", "add", "-A"], cwd=repo, check=True)
        sp.run(["git", "commit", "-q", "-m", f"c{i}"], cwd=repo, check=True)

    # A small, constant backlog of unsearched patches — tick-WORK is held fixed;
    # the only thing that forces multiple ticks is the bounded deadline window.
    drafts = [
        TrailEventDraft(
            event_type="trace_patch_created",
            trace_id=f"tr-{j}",
            step_index=1,
            capture_method=["hook_posttooluse"],
            payload={
                "trace_patch_id": f"tracepatch-sha256:p{j}",
                "file_path": "f.txt",
                "authored_text": f"line {j}\n",
            },
        )
        for j in range(2)
    ]
    append_event_batch(repo, drafts, writer="test-fixture")

    # Fake monotonic clock: +1.0 per call, delegating every other time attr to the
    # real module. Deadline is recomputed per tick as (now + WINDOW); WINDOW is
    # wide enough to search >=1 commit but far too narrow to drain all 8, so every
    # early tick truncates.
    import time as real_time

    class _FakeClock:
        def __init__(self) -> None:
            self.t = 0.0

        def monotonic(self) -> float:
            self.t += 1.0
            return self.t

        def __getattr__(self, name):
            return getattr(real_time, name)

    clock = _FakeClock()
    monkeypatch.setattr(mat, "time", clock)
    monkeypatch.setattr(anchors_mod, "time", clock)

    # Deadline-aware Git listing/blob/append operations now consume the same
    # injected clock as the Python reconciliation loop. Keep the window large
    # enough to complete at least one commit's full evidence transaction while
    # remaining far below the eight-commit backlog.
    window = 60.0
    max_ticks = 40
    ticks = 0
    first_truncated = None
    while mat.has_unsearched_recent_patches(repo) and ticks < max_ticks:
        summary = mat.mature_trails(repo, deadline=clock.t + window)
        if first_truncated is None:
            first_truncated = summary.truncated
        ticks += 1

    assert first_truncated is True, (
        "test misconfigured: the first tick completed the whole backlog, so it "
        "never exercised the truncation/flush path"
    )
    assert not mat.has_unsearched_recent_patches(repo), (
        f"backlog never drained in {max_ticks} truncated ticks — completed work is "
        "discarded on truncation and recomputed forever (#65 livelock)"
    )


def test_covered_prefix_does_not_consume_budget(tmp_path, monkeypatch):
    """#65 codex follow-up — the PARTIAL-COMMIT livelock.

    A commit with a large already-searched prefix must not let that prefix
    consume the wall-clock budget: reconcile checks the deadline only for patches
    that still need real work, so the unsearched tail is reached even under a
    tight budget. Pre-fix (deadline checked BEFORE the dedup-skips) the covered
    prefix burns the budget and the tail is never searched — a single commit could
    never finish across ticks even though commit-level skipping wouldn't fire.
    """
    import subprocess as sp

    from opentraces.core.trails import TrailEventDraft, append_event_batch
    from opentraces.core.trails import anchors as anchors_mod
    from opentraces.core.trails.anchors import reconcile_commit_anchors
    from opentraces.core.trails.event_log import read_events_scoped
    from opentraces.core.trails.ids import id_from_payload
    from opentraces.core.trails.models import ATTRIBUTION_VERSION

    repo = tmp_path / "repo"
    repo.mkdir()
    sp.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    sp.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    sp.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    (repo / "f.txt").write_text("line\nbody\n")
    sp.run(["git", "add", "-A"], cwd=repo, check=True)
    sp.run(["git", "commit", "-q", "-m", "c"], cwd=repo, check=True)
    head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()

    n_total, n_covered = 12, 10
    drafts = [
        TrailEventDraft(
            event_type="trace_patch_created",
            trace_id=f"tr{j}",
            step_index=1,
            capture_method=["hook_posttooluse"],
            payload={
                "trace_patch_id": f"tracepatch-sha256:p{j}",
                "file_path": "f.txt",
                "authored_text": f"line {j}\n",
            },
        )
        for j in range(n_total)
    ]
    append_event_batch(repo, drafts, writer="test")

    patch_events = list(
        read_events_scoped(repo, event_types={"trace_patch_created"})
    )
    assert len(patch_events) == n_total
    # Force the covered patches to be a contiguous PREFIX regardless of read
    # order, so the budget would be spent on them first under the old ordering.
    # Keys are the NORMALISED ids reconcile computes (id_from_payload strips the
    # "tracepatch-sha256:" prefix), not the raw payload strings.
    covered_raw = {f"tracepatch-sha256:p{j}" for j in range(n_covered)}
    covered_events = [e for e in patch_events if e.payload["trace_patch_id"] in covered_raw]
    uncovered_events = [e for e in patch_events if e.payload["trace_patch_id"] not in covered_raw]
    assert len(covered_events) == n_covered
    assert len(uncovered_events) == n_total - n_covered
    patch_events = covered_events + uncovered_events  # covered prefix first
    version = ATTRIBUTION_VERSION
    search_keys = {(id_from_payload(e.payload, "trace_patch"), head, version) for e in covered_events}
    expected_tail = {id_from_payload(e.payload, "trace_patch") for e in uncovered_events}
    anchor_keys: set = set()

    import time as real_time

    class _FakeClock:
        def __init__(self) -> None:
            self.t = 0.0

        def monotonic(self) -> float:
            self.t += 1.0
            return self.t

        def __getattr__(self, name):
            return getattr(real_time, name)

    clock = _FakeClock()
    monkeypatch.setattr(anchors_mod, "time", clock)

    # Budget far smaller than the covered prefix (10) but enough for the 2-patch
    # tail. Pre-fix this expires inside the prefix; post-fix the prefix is free.
    summary: dict = {}
    reconcile_commit_anchors(
        repo,
        head,
        patch_events=patch_events,
        anchor_keys=anchor_keys,
        search_keys=search_keys,
        attribution_version=version,
        deadline=clock.t + 4.0,
        append_events=False,
        summary_out=summary,
    )
    searched = {r["trace_patch_id"] for r in summary.get("search_results", [])}
    assert searched == expected_tail, (
        "the unsearched tail must be reached despite the long covered prefix; "
        f"got {sorted(searched)}, expected {sorted(expected_tail)}"
    )


def test_run_once_large_patch_payloads_stays_under_rss_ceiling(
    tmp_path, monkeypatch, _isolate_opentraces_global_state
):
    """Ungated #65 memory proof: a full watcher tick over large patch
    payloads must stay bounded by chunking maturation's patch stream."""
    from opentraces.core.config import get_project_state_path
    from opentraces.core.state import StateManager
    from opentraces.core.trails import TrailEventDraft, append_event_batch
    from opentraces.core.trails.models import sha256_text

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    (repo / ".opentraces.json").write_text(json.dumps({
        "project_id": uuid.uuid4().hex,
        "policy": {},
    }))
    (repo / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
    head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()

    patch_count = 80
    payload = "x" * (1024 * 1024)
    drafts = []
    for index in range(patch_count):
        authored = f"missing-payload-{index}\n{payload}"
        drafts.append(
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id=f"tr-rss-{index}",
                step_index=1,
                capture_method=["watcher_backstop"],
                payload={
                    "trace_patch_id": f"rss-patch-{index}",
                    "file_path": f"missing-{index}.py",
                    "affected_range": {"start_line": 1, "end_line": 1},
                    "authored_text": authored,
                    "raw_authored_hash": sha256_text(authored),
                    "git_clean_hash": sha256_text(" ".join(authored.split())),
                    "limitations": [],
                },
            )
        )
    append_event_batch(repo, drafts, writer="test-fixture")
    legacy_search_count = 120
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="git_anchor_search_completed",
                trace_id=f"old-tr-{index}",
                step_index=1,
                capture_method=["legacy_fixture"],
                payload={
                    "trace_patch_id": f"old-rss-patch-{index}",
                    "search_head": {"algo": "sha1", "hex": head},
                    "algorithms_attempted": ["exact_range_hash"],
                    "result": "unknown",
                    "created_anchor_ids": [],
                },
            )
            for index in range(legacy_search_count)
        ],
        writer="legacy-fixture",
    )

    state = StateManager(state_path=get_project_state_path(repo))
    state.set_last_backfilled_commit(head)
    state.set_last_watcher_run_at()

    result_path = tmp_path / "run_once_result.json"
    driver = (
        "import json, platform, resource, sys\n"
        "from pathlib import Path\n"
        "from opentraces.watcher import daemon as wd\n"
        f"report = wd.run_once(Path({str(repo)!r}))\n"
        "peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
        "mb = peak / (1024*1024) if platform.system() == 'Darwin' else peak / 1024\n"
        f"Path({str(result_path)!r}).write_text(json.dumps({{\n"
        "    'peak_mb': mb,\n"
        "    'error': report.error,\n"
        "    'searches': report.trail_maturation_searches,\n"
        "    'anchors': report.trail_maturation_anchors,\n"
        "}))\n"
        "sys.exit(1 if report.error else 0)\n"
    )
    monkeypatch.setenv("OT_MATURATION_PATCH_CHUNK_MAX_BYTES", str(4 * 1024 * 1024))
    monkeypatch.setenv("OT_MATURATION_PATCH_CHUNK_MAX_EVENTS", "4")
    monkeypatch.setenv("OT_MATURATION_TICK_BUDGET_S", "600")
    verdict = wd._tick_in_child(
        repo,
        budget_mb=800,
        timeout_s=120,
        _argv=[sys.executable, "-c", driver],
    )
    assert verdict == "ok", "healthy #65 tick must not need the RSS child kill"
    result = json.loads(result_path.read_text())

    assert result["searches"] == patch_count
    assert result["anchors"] == 0
    assert result["peak_mb"] < 800, (
        f"run_once peak RSS {result['peak_mb']:.0f}MB exceeded the 800MB "
        "synthetic large-payload ceiling"
    )


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


# --- #65 codex round 3: child timeout capped + attempt-aware rotation ---------


def test_child_timeout_capped_to_remaining_sweep_budget(tmp_path, monkeypatch):
    """A child started near the sweep deadline must get the REMAINING budget
    as its timeout, not the full per-child default — and a tail with less
    than MIN_CHILD_BUDGET_S left is deferred, never started."""
    projects = [tmp_path / "a", tmp_path / "b", tmp_path / "c"]
    for p in projects:
        p.mkdir()
    monkeypatch.setenv("OT_WATCHER_SWEEP_BUDGET_S", "40")
    monkeypatch.setenv("OT_WATCHER_TICK_TIMEOUT_S", "900")
    monkeypatch.setattr(wd, "_last_tick_mtime", lambda p: 0.0)

    seen_timeouts: list[float] = []
    clock = {"now": 1000.0}

    def _fake_monotonic():
        return clock["now"]

    def _fake_child(project_cwd, *, budget_mb, timeout_s, _argv=None):
        seen_timeouts.append(timeout_s)
        clock["now"] += 35.0  # most of the budget consumed by the first tick
        return "ok"

    monkeypatch.setattr(wd.time, "monotonic", _fake_monotonic)
    monkeypatch.setattr(wd, "_tick_in_child", _fake_child)
    summary = wd.run_sweep(projects=projects)
    # First child: 40s remaining → capped at 40 (not 900). After it, 5s
    # remain (< MIN_CHILD_BUDGET_S=30) → b and c deferred, never started.
    assert len(seen_timeouts) == 1
    assert seen_timeouts[0] <= 40.0, "child timeout must be capped to remaining budget"
    assert summary["ok"] == 1
    assert summary["deferred"] == 2


def test_killed_projects_rotate_to_back_not_front(tmp_path, monkeypatch, _isolate_opentraces_global_state):
    """Codex round 3 sentinel: a project whose tick is rss-killed never
    stamps state.json — the attempt marker must still advance its rotation
    key so it goes to the BACK of the next sweep instead of starving the
    healthy projects behind it."""
    from opentraces.core import paths as _paths

    bad = tmp_path / "bad"
    bad.mkdir()
    good = tmp_path / "good"
    good.mkdir()

    # Enlist both so get_project_state_path resolves inside the isolated home.
    slug_dirs = {}
    for name, p in (("bad", bad), ("good", good)):
        d = _paths.PROJECTS_DIR / f"{name}-0000"
        d.mkdir(parents=True)
        (d / "project.json").write_text(json.dumps({"path": str(p)}))
        slug_dirs[name] = d
    monkeypatch.setattr(
        wd, "get_project_state_path",
        lambda p: slug_dirs[p.name] / "state.json",
    )

    def _killing_child(project_cwd, *, budget_mb, timeout_s, _argv=None):
        return "rss_killed" if project_cwd.name == "bad" else "ok"

    monkeypatch.setattr(wd, "_tick_in_child", _killing_child)
    # Sweep 1: both attempted; bad gets killed, only the marker advances.
    wd.run_sweep(projects=[bad, good])
    assert (slug_dirs["bad"] / "last_sweep_attempt").is_file()

    # bad's rotation key must now be RECENT (marker), not 0/never —
    # so a fresh never-ticked project would sort ahead of it.
    bad_key = wd._last_tick_mtime(bad)
    assert bad_key > 0.0, "killed project must carry an attempt timestamp"
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    d = _paths.PROJECTS_DIR / "fresh-0000"
    d.mkdir()
    (d / "project.json").write_text(json.dumps({"path": str(fresh)}))
    slug_dirs["fresh"] = d
    assert wd._last_tick_mtime(fresh) < bad_key, (
        "never-attempted project must sort ahead of a recently-killed one"
    )


# --- #65 soak findings round 2: marker/prune interaction + tmp deprioritised --


def test_attempt_marker_does_not_reset_prune_staleness(tmp_path, monkeypatch, _isolate_opentraces_global_state):
    """Soak sentinel: every sweep stamps last_sweep_attempt — if prune counted
    it as activity, a leaked tmp enlistment's staleness clock would reset each
    sweep and it would NEVER age out. A 30-day-stale tmp leak with a
    seconds-old attempt marker must still be pruned."""
    import opentraces.watcher.prune as prune_mod
    from opentraces.core import paths as _paths

    tmp_root = tmp_path / "tmproot"
    tmp_root.mkdir()
    monkeypatch.setattr(prune_mod, "_tmp_roots", lambda: [tmp_root.resolve()])
    leak_target = tmp_root / "prerel-99"
    leak_target.mkdir()

    d = _make_enlistment(_paths.PROJECTS_DIR, "prerel-99-0000", leak_target,
                         age_days=30)
    (d / "last_sweep_attempt").touch()  # fresh marker, as every sweep leaves

    summary = prune_enlistments()
    assert summary["pruned_tmp"] == 1, (
        "a fresh sweep-attempt marker must not shield a stale tmp leak"
    )
    assert not d.exists()


def test_sweep_orders_real_projects_before_tmp_worlds(tmp_path, monkeypatch):
    """Soak sentinel: leaked /tmp worlds each burned a full child timeout and
    monopolised every bounded sweep on the live machine. Real projects must
    sweep FIRST regardless of how stale the tmp worlds' rotation keys are."""
    import opentraces.watcher.prune as prune_mod

    tmp_root = tmp_path / "tmproot"
    tmp_root.mkdir()
    monkeypatch.setattr(prune_mod, "_tmp_roots", lambda: [tmp_root.resolve()])

    tmp_world = tmp_root / "prerel-1"
    tmp_world.mkdir()
    real_a = tmp_path / "real-a"
    real_a.mkdir()
    real_b = tmp_path / "real-b"
    real_b.mkdir()

    # tmp world has the OLDEST rotation key (would win pure LRU).
    mtimes = {"prerel-1": 0.0, "real-a": 200.0, "real-b": 100.0}
    monkeypatch.setattr(wd, "_last_tick_mtime",
                        lambda p: mtimes.get(p.name, 0.0))

    ticked: list[str] = []

    def _fake_child(project_cwd, *, budget_mb, timeout_s, _argv=None):
        ticked.append(project_cwd.name)
        return "ok"

    monkeypatch.setattr(wd, "_tick_in_child", _fake_child)
    wd.run_sweep(projects=[tmp_world, real_a, real_b])
    assert ticked == ["real-b", "real-a", "prerel-1"], (
        "real projects (LRU within group) must precede tmp-rooted worlds"
    )


def test_ensure_git_hook_auto_installs_for_opted_in_repo(tmp_path):
    """0.4.5: the watcher tick auto-attaches the per-repo correlator to an
    opted-in repo, so a global opt-in installs it everywhere (new or existing)
    without a manual `setup git`. Idempotent + best-effort."""
    import subprocess as sp

    from opentraces.capture.git import install as gi

    repo = tmp_path / "r"
    repo.mkdir()
    sp.run(["git", "init", "-q"], cwd=repo, check=True)
    hook = repo / ".git" / "hooks" / gi.HOOK_FILENAME
    assert not hook.exists()

    wd._ensure_git_hook(repo)
    assert hook.exists(), "watcher tick must auto-install the post-commit hook"

    # Idempotent: a second call (stat fast-path) doesn't error or duplicate.
    before = hook.read_text()
    wd._ensure_git_hook(repo)
    assert hook.read_text() == before


def test_ensure_git_hook_is_noop_on_non_git_dir(tmp_path):
    """A non-git enlisted dir must not raise (best-effort)."""
    wd._ensure_git_hook(tmp_path)  # no .git → install returns False, no raise
    assert not (tmp_path / ".git").exists()

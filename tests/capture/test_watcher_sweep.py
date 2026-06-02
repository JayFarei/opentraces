"""Watcher sweep integration (Phase 1 of live-session ingestion).

After ``run_incremental`` finishes on an active tick, the watcher invokes
``scan_project`` so any new session turns get staged into the inbox.
Quiet ticks (no JSONL activity, no new commits) skip the sweep — the
watcher's "quiet" optimisation is preserved.

A failure inside ``scan_project`` must never break the commit-attribution
backfill path — the sweep is best-effort.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

from opentraces.core.trails import TrailEventDraft, append_event_batch, read_events
from opentraces.core.trails.models import sha256_text
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
    (root / ".opentraces.json").write_text(json.dumps({
        "marker_version": "2",
        "project_id": uuid.uuid4().hex,
        "review_policy": "review",
        "push_policy": "manual",
        "remotes": {"origin": {"url": "t/t", "visibility": "private"}},
        "active_remote": "origin",
        "agents": ["claude-code"],
    }))
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "init", cwd=root)
    return root


class _SpyReport:
    """Minimal stand-in for ``ScanReport`` — what the daemon inspects."""
    def __init__(self):
        self.results = []

    created = 0
    refreshed = 0
    new_generations = 0
    noops = 0
    errored = 0


class TestWatcherSweep:
    def test_active_tick_invokes_scan_project(
        self, tmp_path, monkeypatch
    ) -> None:
        p = _init_project(tmp_path / "proj")

        calls: list[Path] = []

        def _spy(project_dir, **kwargs):
            calls.append(Path(project_dir))
            return _SpyReport()

        monkeypatch.setattr(_wd, "scan_project", _spy, raising=False)

        report = _wd.run_once(p)
        assert report.error is None
        # Active tick (new commits from init commit).
        assert report.backfill_invoked is True
        assert len(calls) == 1, \
            "scan_project must be called exactly once on an active tick"
        assert calls[0].resolve() == p.resolve()

    def test_quiet_tick_does_not_invoke_scan_project(
        self, tmp_path, monkeypatch
    ) -> None:
        p = _init_project(tmp_path / "proj")
        # Warm up so the bookmark is current.
        _wd.run_once(p)

        calls: list[Path] = []

        def _spy(project_dir, **kwargs):
            calls.append(Path(project_dir))
            return _SpyReport()

        monkeypatch.setattr(_wd, "scan_project", _spy, raising=False)

        # Second tick — nothing changed.
        report = _wd.run_once(p)
        assert report.error is None
        assert report.backfill_invoked is False, "should be quiet"
        assert calls == [], "quiet ticks must not sweep sessions"

    def test_quiet_tick_runs_trace_trails_runtime(
        self, tmp_path, monkeypatch
    ) -> None:
        p = _init_project(tmp_path / "proj")
        _wd.run_once(p)

        poll_calls: list[Path] = []

        def _poll(project_dir):
            poll_calls.append(Path(project_dir))
            return SimpleNamespace(observations=[])

        monkeypatch.setattr(
            "opentraces.capture.fs_watcher.runtime.poll_project_once",
            _poll,
        )
        monkeypatch.setattr(
            "opentraces.core.trails.reconcile_watcher_observations",
            lambda project_dir: {
                "observations_processed": 0,
                "patches_created": 0,
                "patches_upgraded": 0,
            },
        )

        report = _wd.run_once(p)
        assert report.error is None
        assert report.backfill_invoked is False
        assert poll_calls == [p.resolve()]

    def test_quiet_tick_matures_existing_unsearched_patch(
        self, tmp_path
    ) -> None:
        p = _init_project(tmp_path / "proj")
        _wd.run_once(p)

        authored = "not in current history\n"
        append_event_batch(
            p,
            [
                TrailEventDraft(
                    event_type="trace_patch_created",
                    trace_id="tr-quiet",
                    step_index=1,
                    capture_method=["watcher_backstop"],
                    payload={
                        "trace_patch_id": "quiet-patch",
                        "file_path": "ghost.py",
                        "affected_range": {"start_line": 1, "end_line": 1},
                        "authored_text": authored,
                        "raw_authored_hash": sha256_text(authored),
                        "git_clean_hash": sha256_text(" ".join(authored.split())),
                        "limitations": [],
                    },
                )
            ],
            writer="test-fixture",
        )

        report = _wd.run_once(p)
        assert report.error is None
        assert report.backfill_invoked is False
        assert report.trail_maturation_searches == 1
        assert report.trail_maturation_anchors == 0

        searches = [
            event for event in read_events(p)
            if event.event_type == "git_anchor_search_completed"
        ]
        assert len(searches) == 1
        assert searches[0].payload["results"][0]["result"] == "unknown"

    def test_sweep_failure_does_not_break_the_tick(
        self, tmp_path, monkeypatch
    ) -> None:
        p = _init_project(tmp_path / "proj")

        def _boom(project_dir, **kwargs):
            raise RuntimeError("simulated sweep failure")

        monkeypatch.setattr(_wd, "scan_project", _boom, raising=False)

        report = _wd.run_once(p)
        # The backfill side still succeeded; the sweep failure is
        # swallowed with a log line, not surfaced as a tick error.
        assert report.backfill_invoked is True
        assert report.error is None, \
            "sweep failure must not set the tick-level error"

    def test_tick_report_exposes_session_counts(
        self, tmp_path, monkeypatch
    ) -> None:
        p = _init_project(tmp_path / "proj")

        class _Report:
            created = 2
            refreshed = 1
            new_generations = 0
            noops = 3
            errored = 0
            results = [object()] * 6

        monkeypatch.setattr(
            _wd, "scan_project", lambda *a, **k: _Report(), raising=False
        )

        report = _wd.run_once(p)
        assert report.error is None
        assert report.sessions_created == 2
        assert report.sessions_refreshed == 1
        assert report.sessions_new_generations == 0
        assert report.sessions_noops == 3
        assert report.sessions_errored == 0

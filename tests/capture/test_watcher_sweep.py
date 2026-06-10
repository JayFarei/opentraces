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

    def test_steady_state_quiet_tick_skips_maturation_scan(
        self, tmp_path, monkeypatch
    ) -> None:
        """#23 step 3 (watermark): once a tick has matured, a subsequent quiet
        tick at the same head must short-circuit ``has_unsearched_recent_patches``
        WITHOUT enumerating git objects.

        The runaway-CPU symptom in #23 is the gate reading the whole event log
        every quiet tick. With the watermark in place, the steady-state quiet
        tick issues ZERO ``rev-list --objects`` calls and reports
        ``trail_maturation_searches == 0`` (maturation is never invoked).
        """
        p = _init_project(tmp_path / "proj")
        # Seed an unsearched patch, then run a tick that matures it. This stamps
        # the maturation watermark.
        authored = "watermark steady-state authored line\n"
        append_event_batch(
            p,
            [
                TrailEventDraft(
                    event_type="trace_patch_created",
                    trace_id="tr-wm",
                    step_index=1,
                    capture_method=["watcher_backstop"],
                    payload={
                        "trace_patch_id": "wm-patch",
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
        first = _wd.run_once(p)
        assert first.error is None
        assert first.trail_maturation_searches == 1

        # Count rev-list --objects calls on the NEXT quiet tick.
        import subprocess as _sp

        rev_list_objects = {"count": 0}
        real_run = _sp.run

        def _counting_run(args, *a, **k):
            try:
                if (
                    isinstance(args, (list, tuple))
                    and "rev-list" in args
                    and "--objects" in args
                ):
                    rev_list_objects["count"] += 1
            except Exception:  # noqa: BLE001
                pass
            return real_run(args, *a, **k)

        # Patch subprocess.run in both modules that issue rev-list --objects.
        import subprocess as _subprocess_mod

        monkeypatch.setattr(_subprocess_mod, "run", _counting_run)

        second = _wd.run_once(p)
        assert second.error is None
        assert second.backfill_invoked is False, "second tick must be quiet"
        # Watermark hit: maturation is not invoked, so no searches and no
        # whole-log object enumeration from the maturation gate.
        assert second.trail_maturation_searches == 0
        assert rev_list_objects["count"] == 0, (
            "steady-state quiet tick must not enumerate git objects "
            f"(saw {rev_list_objects['count']} rev-list --objects calls)"
        )

    def test_batched_maturation_one_rev_list_and_identical_dedup_keys(
        self, tmp_path, monkeypatch
    ) -> None:
        """#23 step 1: ``mature_trails`` over N commits issues exactly ONE
        ``rev-list --objects`` (the single batched anchor/search slice read) and
        produces search summaries whose dedup keys are identical to the
        per-commit path.
        """
        from opentraces.core.trails.maturation import mature_trails

        p = _init_project(tmp_path / "proj")

        # Build 3 commits, each touching a distinct file with a distinct line.
        commit_shas: list[str] = []
        for i in range(3):
            (p / f"f{i}.py").write_text(f"VALUE_{i} = 'line {i}'\n")
            _git("add", "-A", cwd=p)
            _git("commit", "-q", "-m", f"commit {i}", cwd=p)
            commit_shas.append(
                subprocess.check_output(
                    ["git", "-C", str(p), "rev-parse", "HEAD"], text=True
                ).strip()
            )

        # Seed 2 trace patches whose authored text matches two of the commits.
        for i in (0, 2):
            authored = f"VALUE_{i} = 'line {i}'\n"
            append_event_batch(
                p,
                [
                    TrailEventDraft(
                        event_type="trace_patch_created",
                        trace_id=f"tr-{i}",
                        step_index=1,
                        capture_method=["watcher_backstop"],
                        payload={
                            "trace_patch_id": f"batch-patch-{i}",
                            "file_path": f"f{i}.py",
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

        # Count rev-list --objects during the batched mature_trails.
        import subprocess as _sp

        rev_list_objects = {"count": 0}
        real_run = _sp.run

        def _counting_run(args, *a, **k):
            try:
                if (
                    isinstance(args, (list, tuple))
                    and "rev-list" in args
                    and "--objects" in args
                ):
                    rev_list_objects["count"] += 1
            except Exception:  # noqa: BLE001
                pass
            return real_run(args, *a, **k)

        import subprocess as _subprocess_mod

        monkeypatch.setattr(_subprocess_mod, "run", _counting_run)

        summary = mature_trails(p, commit_refs=commit_shas)
        assert summary.errors == []
        # Exactly ONE batched anchor/search slice read for the whole run.
        assert rev_list_objects["count"] == 1, (
            "batched maturation must issue exactly one rev-list --objects "
            f"(saw {rev_list_objects['count']})"
        )

        # Collect the dedup keys (patch, commit-head, attribution_version)
        # recorded by the batched path.
        from opentraces.core.trails.search_records import iter_search_records

        batched_records = [
            record
            for event in read_events(p)
            if event.event_type == "git_anchor_search_completed"
            for record in iter_search_records(event)
        ]
        batched_keys = {
            (
                record["trace_patch_id"],
                record["search_head_sha"],
                record["attribution_version"],
            )
            for record in batched_records
        }
        # Each of the 2 seeded patches is searched against all 3 commits
        # (recording 'unknown' on the non-matching ones): 2 x 3 = 6 dedup keys.
        assert len(batched_keys) == 6
        searched_patches = {k[0] for k in batched_keys}
        assert searched_patches == {"batch-patch-0", "batch-patch-2"}
        # Exactly two records anchored (each patch's matching commit).
        anchored = [r for r in batched_records if r["result"] == "anchored"]
        assert len(anchored) == 2
        assert {r["trace_patch_id"] for r in anchored} == {
            "batch-patch-0",
            "batch-patch-2",
        }

        # Idempotent: a second mature_trails over the same commits records no
        # new search keys (dedup holds across the batched path).
        mature_trails(p, commit_refs=commit_shas)
        again_keys = {
            (
                record["trace_patch_id"],
                record["search_head_sha"],
                record["attribution_version"],
            )
            for event in read_events(p)
            if event.event_type == "git_anchor_search_completed"
            for record in iter_search_records(event)
        }
        assert again_keys == batched_keys, "batched dedup must be stable"

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

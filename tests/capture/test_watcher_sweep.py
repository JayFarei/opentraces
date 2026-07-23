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
import shutil
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
        # #363: the watcher's full-window (commit_refs=None) untruncated sweep is
        # a provably-complete view, so its flush is the v3-COVERAGE shape --
        # anchored-only results[] (empty here, the one patch is unknown) plus an
        # O(1) coverage through-pointer claim, never the O(backlog) exact-id
        # list. (A commit-SUBSET or deadline-truncated sweep stays on the exact-
        # ids compact shape -- pinned in tests/core/test_anchor_search_v3.py.)
        assert searches[0].payload["results"] == []
        assert "unanchored_trace_patch_ids" not in searches[0].payload
        assert searches[0].payload["coverage"] == {
            "through_trace_patch_id": "quiet-patch", "scope_trace_id": None,
        }

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

    def test_batched_maturation_two_streaming_passes_and_identical_dedup_keys(
        self, tmp_path, monkeypatch
    ) -> None:
        """``mature_trails`` over N commits uses bounded whole-log walks, not
        per-commit or per-chunk object enumeration. Search summaries keep the
        same dedup keys as the per-commit path.
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
        # The implementation may collapse these further, but it must not regress
        # to one object walk per commit or per patch chunk.
        assert rev_list_objects["count"] <= 2, (
            "batched maturation must issue bounded rev-list --objects walks "
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

    def test_maturation_dedup_keys_are_chunk_scoped(
        self, tmp_path, monkeypatch
    ) -> None:
        """Historical search records for unrelated patches must not be retained
        and passed into every chunk reconcile. This fails on #80's residual
        whole-tick ``search_keys`` set, where the first reconcile sees all
        historical keys for the candidate commits.
        """
        from opentraces.core.trails import anchors as anchors_mod
        from opentraces.core.trails import maturation as mat_mod

        p = _init_project(tmp_path / "proj")
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

        historical_patch_count = 80
        for commit_sha in commit_shas:
            append_event_batch(
                p,
                [
                    TrailEventDraft(
                        event_type="git_anchor_search_completed",
                        trace_id=f"old-tr-{i}",
                        step_index=1,
                        capture_method=["legacy_fixture"],
                        payload={
                            "trace_patch_id": f"old-patch-{i}",
                            "search_head": {"algo": "sha1", "hex": commit_sha},
                            "algorithms_attempted": ["exact_range_hash"],
                            "result": "unknown",
                            "created_anchor_ids": [],
                        },
                    )
                    for i in range(historical_patch_count)
                ],
                writer="legacy-fixture",
            )

        for i in range(4):
            authored = f"new missing {i}\n"
            append_event_batch(
                p,
                [
                    TrailEventDraft(
                        event_type="trace_patch_created",
                        trace_id=f"new-tr-{i}",
                        step_index=1,
                        capture_method=["watcher_backstop"],
                        payload={
                            "trace_patch_id": f"new-patch-{i}",
                            "file_path": f"missing-{i}.py",
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

        monkeypatch.setenv("OT_MATURATION_PATCH_CHUNK_MAX_EVENTS", "2")
        monkeypatch.setenv("OT_MATURATION_PATCH_CHUNK_MAX_BYTES", "1")
        real_reconcile = anchors_mod.reconcile_commit_anchors
        seen_search_key_counts: list[int] = []

        def _spy_reconcile(*args, **kwargs):
            patch_events = list(kwargs.get("patch_events") or [])
            search_keys = set(kwargs.get("search_keys") or set())
            seen_search_key_counts.append(len(search_keys))
            assert len(search_keys) <= len(patch_events) * len(commit_shas)
            return real_reconcile(*args, **kwargs)

        monkeypatch.setattr(mat_mod, "reconcile_commit_anchors", _spy_reconcile)
        summary = mat_mod.mature_trails(p, commit_refs=commit_shas)
        assert summary.errors == []
        assert summary.searches_completed == 4 * len(commit_shas)
        assert seen_search_key_counts
        assert max(seen_search_key_counts) < historical_patch_count, (
            "chunk reconcile saw unrelated historical search keys; dedup is not "
            "bounded by the current patch chunk"
        )

    def test_chunked_maturation_dedups_duplicate_patch_across_chunks(
        self, tmp_path, monkeypatch
    ) -> None:
        """A duplicate patch id split across chunks is searched once per commit,
        and a patch whose authored text appears in two commits anchors to both
        commits without reintroducing C x chunk search-summary fan-out.
        """
        from opentraces.core.trails.maturation import mature_trails
        from opentraces.core.trails.search_records import iter_search_records

        p = _init_project(tmp_path / "proj")
        commit_shas: list[str] = []
        (p / "shared.py").write_text("DUP = 'x'\n")
        _git("add", "-A", cwd=p)
        _git("commit", "-q", "-m", "add shared", cwd=p)
        commit_shas.append(
            subprocess.check_output(
                ["git", "-C", str(p), "rev-parse", "HEAD"], text=True
            ).strip()
        )
        (p / "shared.py").write_text("")
        _git("add", "-A", cwd=p)
        _git("commit", "-q", "-m", "remove shared", cwd=p)
        commit_shas.append(
            subprocess.check_output(
                ["git", "-C", str(p), "rev-parse", "HEAD"], text=True
            ).strip()
        )
        (p / "shared.py").write_text("DUP = 'x'\n")
        _git("add", "-A", cwd=p)
        _git("commit", "-q", "-m", "readd shared", cwd=p)
        commit_shas.append(
            subprocess.check_output(
                ["git", "-C", str(p), "rev-parse", "HEAD"], text=True
            ).strip()
        )

        collision = "DUP = 'x'\n"
        missing = "not in any commit\n"
        append_event_batch(
            p,
            [
                TrailEventDraft(
                    event_type="trace_patch_created",
                    trace_id="tr-collision",
                    step_index=1,
                    capture_method=["watcher_backstop"],
                    payload={
                        "trace_patch_id": "collision-patch",
                        "file_path": "shared.py",
                        "affected_range": {"start_line": 1, "end_line": 1},
                        "authored_text": collision,
                        "raw_authored_hash": sha256_text(collision),
                        "git_clean_hash": sha256_text(" ".join(collision.split())),
                        "limitations": [],
                    },
                ),
                TrailEventDraft(
                    event_type="trace_patch_created",
                    trace_id="tr-collision-dup",
                    step_index=2,
                    capture_method=["watcher_backstop"],
                    payload={
                        "trace_patch_id": "collision-patch",
                        "file_path": "shared.py",
                        "affected_range": {"start_line": 1, "end_line": 1},
                        "authored_text": collision,
                        "raw_authored_hash": sha256_text(collision),
                        "git_clean_hash": sha256_text(" ".join(collision.split())),
                        "limitations": [],
                    },
                ),
                TrailEventDraft(
                    event_type="trace_patch_created",
                    trace_id="tr-missing",
                    step_index=3,
                    capture_method=["watcher_backstop"],
                    payload={
                        "trace_patch_id": "missing-patch",
                        "file_path": "missing.py",
                        "affected_range": {"start_line": 1, "end_line": 1},
                        "authored_text": missing,
                        "raw_authored_hash": sha256_text(missing),
                        "git_clean_hash": sha256_text(" ".join(missing.split())),
                        "limitations": [],
                    },
                ),
            ],
            writer="test-fixture",
        )

        monkeypatch.setenv("OT_MATURATION_PATCH_CHUNK_MAX_EVENTS", "1")
        monkeypatch.setenv("OT_MATURATION_PATCH_CHUNK_MAX_BYTES", "1")
        summary = mature_trails(p, commit_refs=commit_shas)
        assert summary.errors == []
        assert summary.truncated is False
        assert summary.searches_completed == 2 * len(commit_shas)

        events = read_events(p)
        search_events = [
            event for event in events
            if event.event_type == "git_anchor_search_completed"
        ]
        assert len(search_events) == len(commit_shas)
        assert {event.payload["searched"] for event in search_events} == {2}
        records = [
            record
            for event in search_events
            for record in iter_search_records(event)
        ]
        keys = {
            (record["trace_patch_id"], record["search_head_sha"])
            for record in records
        }
        assert len(records) == len(keys) == 2 * len(commit_shas)
        anchored_commits = {
            record["search_head_sha"]
            for record in records
            if record["trace_patch_id"] == "collision-patch"
            and record["result"] == "anchored"
        }
        assert anchored_commits == {commit_shas[0], commit_shas[2]}

    def test_chunked_maturation_matches_whole_pass_oracle(
        self, tmp_path, monkeypatch
    ) -> None:
        """Forced one-patch chunks must emit the same anchors and per-patch
        search records as a real ``mature_trails`` run with one whole chunk."""
        from opentraces.core.trails.maturation import mature_trails
        from opentraces.core.trails.search_records import iter_search_records

        seed = _init_project(tmp_path / "seed")
        commit_shas: list[str] = []
        for i in range(3):
            (seed / f"f{i}.py").write_text(f"VALUE_{i} = 'line {i}'\n")
            _git("add", "-A", cwd=seed)
            _git("commit", "-q", "-m", f"commit {i}", cwd=seed)
            commit_shas.append(
                subprocess.check_output(
                    ["git", "-C", str(seed), "rev-parse", "HEAD"], text=True
                ).strip()
            )
        for i in range(3):
            authored = f"VALUE_{i} = 'line {i}'\n"
            append_event_batch(
                seed,
                [
                    TrailEventDraft(
                        event_type="trace_patch_created",
                        trace_id=f"tr-{i}",
                        step_index=1,
                        capture_method=["watcher_backstop"],
                        payload={
                            "trace_patch_id": f"equiv-patch-{i}",
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

        whole = tmp_path / "whole"
        chunked = tmp_path / "chunked"
        shutil.copytree(seed, whole)
        shutil.copytree(seed, chunked)

        monkeypatch.setenv("OT_MATURATION_PATCH_CHUNK_MAX_EVENTS", "999")
        monkeypatch.setenv("OT_MATURATION_PATCH_CHUNK_MAX_BYTES", str(1024 * 1024))
        whole_summary = mature_trails(whole, commit_refs=commit_shas)
        assert whole_summary.errors == []
        assert whole_summary.truncated is False

        monkeypatch.setenv("OT_MATURATION_PATCH_CHUNK_MAX_EVENTS", "1")
        monkeypatch.setenv("OT_MATURATION_PATCH_CHUNK_MAX_BYTES", "1")
        summary = mature_trails(chunked, commit_refs=commit_shas)
        assert summary.errors == []
        assert summary.truncated is False

        def _search_records(repo: Path) -> list[tuple]:
            records = [
                record
                for event in read_events(repo)
                if event.event_type == "git_anchor_search_completed"
                for record in iter_search_records(event)
            ]
            return sorted(
                (
                    record["trace_patch_id"],
                    record["search_head_sha"],
                    record["result"],
                    tuple(record["created_anchor_ids"]),
                )
                for record in records
            )

        def _anchors(repo: Path) -> list[tuple]:
            return sorted(
                (
                    event.payload.get("trace_patch_id"),
                    (event.payload.get("commit_id") or {}).get("hex"),
                    event.payload.get("path"),
                    (event.payload.get("range") or {}).get("start_line"),
                    (event.payload.get("range") or {}).get("end_line"),
                    event.payload.get("evidence_tier"),
                )
                for event in read_events(repo)
                if event.event_type == "git_anchor_created"
            )

        assert _search_records(chunked) == _search_records(whole)
        assert _anchors(chunked) == _anchors(whole)

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


class TestGateScopedRead:
    """#45: ``has_unsearched_recent_patches`` reads the scoped anchor/search/patch
    slice (mature_trails' slice), never the whole event log. The gate runs on
    every quiet tick that misses the maturation watermark, so a full-log read
    here is the same unbounded per-tick RSS cost Bug B closed on the hook path.
    """

    def _commit(self, repo: Path, name: str, body: str) -> str:
        (repo / name).write_text(body)
        _git("add", "-A", cwd=repo)
        _git("commit", "-q", "-m", f"c-{name}", cwd=repo)
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()

    def _emit_patch(self, repo: Path, patch_id: str) -> None:
        append_event_batch(
            repo,
            [TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="tr-gate", step_index=1,
                capture_method=["watcher_backstop"],
                payload={
                    "trace_patch_id": patch_id,
                    "file_path": "g.py",
                    "affected_range": {"start_line": 1, "end_line": 1},
                    "authored_text": "x\n",
                    "raw_authored_hash": sha256_text("x\n"),
                    "git_clean_hash": sha256_text("x"),
                    "limitations": [],
                },
            )],
            writer="test-fixture",
        )

    def _emit_search_summary(
        self, repo: Path, commit_sha: str, results: list[dict]
    ) -> None:
        from opentraces.core.trails.search_records import (
            build_anchor_search_summary_payload,
        )
        payload = build_anchor_search_summary_payload(
            schema_version="opentraces.trail.anchor_search.v2",
            search_head={"hex": commit_sha},
            algorithms_attempted=["exact"],
            results=results,
        )
        append_event_batch(
            repo,
            [TrailEventDraft(
                event_type="git_anchor_search_completed",
                trace_id=None, step_index=None,
                capture_method=["trail_maturation"],
                payload=payload,
            )],
            writer="trail-maturation",
        )

    @staticmethod
    def _previous_gate_impl(repo: Path) -> bool:
        """Faithful copy of the pre-#45 gate body (full ``read_events`` +
        identical patch_ids/searched/anchored set logic) as the parity oracle."""
        from opentraces.core.trails.event_log import read_events as _full_read
        from opentraces.core.trails.ids import id_from_payload
        from opentraces.core.trails.maturation import _candidate_commits
        from opentraces.core.trails.models import ATTRIBUTION_VERSION
        from opentraces.core.trails.search_records import iter_search_records

        commits = _candidate_commits(repo, commit_refs=None, max_commits=50)
        if not commits:
            return False
        events = _full_read(repo, verify=False)
        patch_ids = {
            tp for event in events
            if event.event_type == "trace_patch_created"
            for tp in [id_from_payload(event.payload, "trace_patch")] if tp
        }
        if not patch_ids:
            return False
        searched = {
            (r["trace_patch_id"], r["search_head_sha"], r["attribution_version"])
            for event in events
            if event.event_type == "git_anchor_search_completed"
            for r in iter_search_records(event)
        }
        anchored = {
            (id_from_payload(event.payload, "trace_patch"),
             (event.payload.get("commit_id") or {}).get("hex"))
            for event in events
            if event.event_type == "git_anchor_created"
        }
        return any(
            (tp, c, ATTRIBUTION_VERSION) not in searched
            and (tp, c) not in anchored
            for tp in patch_ids for c in commits
        )

    def test_gate_uses_scoped_read_not_full_read(self, tmp_path, monkeypatch):
        """Sentinel: with the full ``read_events`` made to explode, the gate must
        still compute the right answer off the scoped slice."""
        from opentraces.core.trails import maturation as _mat
        from opentraces.core.trails import event_log as _event_log

        p = _init_project(tmp_path / "proj")
        self._commit(p, "a.py", "a\n")
        self._emit_patch(p, "gate-patch-unsearched")

        def _boom(*a, **k):
            raise AssertionError("gate must not call full read_events (#45)")

        monkeypatch.setattr(_event_log, "read_events", _boom)
        if hasattr(_mat, "read_events"):
            monkeypatch.setattr(_mat, "read_events", _boom)
        # Patch exists, no search recorded -> unsearched -> True.
        assert _mat.has_unsearched_recent_patches(p) is True

    def test_gate_matches_previous_impl_across_fixtures(self, tmp_path):
        """Parity across searched / anchored / unsearched combinations, INCLUDING
        a commit carrying TWO search summaries (pkg-44 partial-summary handoff)."""
        from opentraces.core.trails import maturation as _mat
        from opentraces.core.trails.event_log import invalidate_read_events_cache

        # Fixture 1: a patch with no search -> unsearched.
        p1 = _init_project(tmp_path / "p1")
        self._commit(p1, "a.py", "a\n")
        self._emit_patch(p1, "p1-unsearched")
        invalidate_read_events_cache(p1)
        assert self._previous_gate_impl(p1) is True
        invalidate_read_events_cache(p1)
        assert _mat.has_unsearched_recent_patches(p1) is True

        # Fixture 2: EVERY (patch, candidate-commit) searched -> satisfied
        # (False). One commit carries TWO search summaries (pkg-44's partial-
        # summary handoff shape): the set-union over both must still cover its
        # (patch, commit) pairs. We search the patch against every candidate
        # commit returned by the maturation candidate walk (including the init
        # commit) so the gate has nothing left unsearched.
        from opentraces.core.trails.maturation import _candidate_commits

        p2 = _init_project(tmp_path / "p2")
        self._commit(p2, "a.py", "a\n")
        self._commit(p2, "b.py", "b\n")
        self._emit_patch(p2, "p2-patch")
        all_commits = _candidate_commits(p2, commit_refs=None, max_commits=50)
        assert len(all_commits) >= 3, "init + a.py + b.py are all candidates"
        for c in all_commits:
            self._emit_search_summary(
                p2, c,
                [{"trace_patch_id": "p2-patch", "trace_id": "tr-gate",
                  "step_index": 1, "generation_index": 0, "result": "unknown"}],
            )
        # SECOND summary for the FIRST candidate commit (a re-run): two
        # git_anchor_search_completed events for one commit is a normal state.
        self._emit_search_summary(
            p2, all_commits[0],
            [{"trace_patch_id": "p2-patch", "trace_id": "tr-gate",
              "step_index": 1, "generation_index": 0, "result": "unknown"}],
        )
        invalidate_read_events_cache(p2)
        # All candidate commits searched for the one patch -> nothing unsearched.
        assert self._previous_gate_impl(p2) is False
        invalidate_read_events_cache(p2)
        assert _mat.has_unsearched_recent_patches(p2) is False

        # Fixture 3: searched for one commit but a NEWER commit is unsearched.
        p3 = _init_project(tmp_path / "p3")
        c_old = self._commit(p3, "a.py", "a\n")
        self._emit_patch(p3, "p3-patch")
        self._emit_search_summary(
            p3, c_old,
            [{"trace_patch_id": "p3-patch", "trace_id": "tr-gate",
              "step_index": 1, "generation_index": 0, "result": "unknown"}],
        )
        # New commit lands AFTER the search -> (patch, new_commit) is unsearched.
        self._commit(p3, "c.py", "c\n")
        invalidate_read_events_cache(p3)
        assert self._previous_gate_impl(p3) is True
        invalidate_read_events_cache(p3)
        assert _mat.has_unsearched_recent_patches(p3) is True

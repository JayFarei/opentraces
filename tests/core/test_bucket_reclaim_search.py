"""Bucket reclaim wiring for the anchor-search compaction pass (issue #358).

Builds a real mini bucket: a project git repo carrying legacy per-patch AND
v2-fat ``git_anchor_search_completed`` events (the raw-dict fixture shapes
from ``test_search_compaction.py``), an opted-in project registration, a
synced events mirror, and projected trail companions -- then exercises the
dry-run/apply/idempotent/interrupt-resume contract of
``opentraces.core.bucket_reclaim_search.reclaim_anchor_search``.
"""
from __future__ import annotations

import gzip
import json
import subprocess
from pathlib import Path

import pytest

from opentraces.core.trails import (
    TrailEventDraft,
    append_event_batch,
    read_events,
    verify_event_log,
)
from opentraces.core.trails.anchors import ANCHOR_ALGORITHMS_PHASE5
from opentraces.core.trails.ids import trace_patch_ref


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _legacy_search(*, trace_id, step_index, patch_id, commit_hex, result, anchor_ids):
    return TrailEventDraft(
        event_type="git_anchor_search_completed",
        trace_id=trace_id,
        generation_index=0,
        step_index=step_index,
        capture_method=["watcher_reconcile"],
        payload={
            "trace_patch_id": f"tracepatch-sha256:{patch_id}",
            "trace_patch_ref": trace_patch_ref(f"tracepatch-sha256:{patch_id}"),
            "search_head": {"algo": "sha1", "hex": commit_hex},
            "algorithms_attempted": ANCHOR_ALGORITHMS_PHASE5,
            "result": result,
            "created_anchor_ids": anchor_ids,
        },
    )


def _v2_fat_search_event(*, commit_hex: str, entries: list[dict]) -> TrailEventDraft:
    anchored = sum(1 for e in entries if e["result"] == "anchored")
    return TrailEventDraft(
        event_type="git_anchor_search_completed",
        trace_id=None,
        step_index=None,
        capture_method=["watcher_reconcile"],
        payload={
            "schema_version": "opentraces.trail.anchor_search.v2",
            "summary": True,
            "search_head": {"algo": "sha1", "hex": commit_hex},
            "algorithms_attempted": ANCHOR_ALGORITHMS_PHASE5,
            "searched": len(entries),
            "anchored": anchored,
            "unknown": len(entries) - anchored,
            "results": entries,
        },
    )


def _patch_created(*, trace_id: str, trace_patch_id: str, file_path: str, step_index: int) -> TrailEventDraft:
    return TrailEventDraft(
        event_type="trace_patch_created",
        trace_id=trace_id,
        generation_index=0,
        step_index=step_index,
        capture_method=["hook_posttooluse"],
        payload={
            "trace_patch_id": trace_patch_id,
            "file_path": file_path,
            "affected_range": {"start_line": 1, "end_line": 1},
            "authored_text": "fixture\n",
            "raw_authored_hash": "sha256:fixture",
            "git_clean_hash": "sha256:fixture",
            "limitations": [],
        },
    )


def _record(trace_id: str):
    from opentraces_schema import Agent, Step, TraceRecord

    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="codex-cli", version="0.31.0"),
        steps=[Step(step_index=1, role="user", content="task")],
    )


def _build_world(tmp_path: Path) -> dict:
    """One project's repo carrying a legacy group (trace ``t-anchored``, one
    anchored + one unknown patch, same reconcile-run batch -- collapses to
    ONE v3-compact summary) and a v2-fat summary (trace ``t-fat-unanchored``,
    six patches, ALL unknown -- loses search-event touch entirely once
    compacted). Registers the project, syncs the mirror, and projects both
    traces' companions via ``bucket_repair`` so the "before" state is exactly
    what a real affected machine would have on disk."""

    from opentraces.core.bucket_store import bucket_repair, write_trace_record
    from opentraces.core.config import get_project_dir, load_config, register_project, save_config

    repo = tmp_path / "proj"
    _init_repo(repo)

    commit_a = "a" * 40
    anchored_patch_id = "tracepatch-sha256:" + ("0" + "a" * 63)
    unknown_patch_id = "tracepatch-sha256:" + ("1" + "a" * 63)
    append_event_batch(
        repo,
        [
            _patch_created(trace_id="t-anchored", trace_patch_id=anchored_patch_id, file_path="a0.py", step_index=0),
            _patch_created(trace_id="t-anchored", trace_patch_id=unknown_patch_id, file_path="a1.py", step_index=1),
        ],
        writer="capture-claude-code",
    )
    append_event_batch(
        repo,
        [
            _legacy_search(
                trace_id="t-anchored", step_index=0, patch_id="0" + "a" * 63,
                commit_hex=commit_a, result="anchored", anchor_ids=["gitanchor-sha256:" + "a" * 64],
            ),
            _legacy_search(
                trace_id="t-anchored", step_index=1, patch_id="1" + "a" * 63,
                commit_hex=commit_a, result="unknown", anchor_ids=[],
            ),
        ],
        writer="watcher",
    )

    commit_b = "b" * 40
    fat_patch_ids = ["tracepatch-sha256:" + f"{i:02d}{'b' * 62}" for i in range(6)]
    append_event_batch(
        repo,
        [
            _patch_created(trace_id="t-fat-unanchored", trace_patch_id=pid, file_path=f"b{i}.py", step_index=i)
            for i, pid in enumerate(fat_patch_ids)
        ],
        writer="capture-claude-code",
    )
    fat_entries = [
        {
            "trace_patch_id": pid, "trace_id": "t-fat-unanchored", "step_index": i, "generation_index": 0,
            "result": "unknown", "created_anchor_ids": [],
        }
        for i, pid in enumerate(fat_patch_ids)
    ]
    append_event_batch(
        repo, [_v2_fat_search_event(commit_hex=commit_b, entries=fat_entries)], writer="watcher",
    )

    cfg = load_config()
    register_project(cfg, repo)
    save_config(cfg)
    slug = get_project_dir(repo).name

    write_trace_record(_record("t-anchored"), project_slug=slug, source_layer="canonical")
    write_trace_record(_record("t-fat-unanchored"), project_slug=slug, source_layer="canonical")

    bucket_repair(dry_run=False)  # mirror sync + companion projection from the fat log

    return {
        "repo": repo,
        "slug": slug,
        "anchored_patch_id": anchored_patch_id,
        "unknown_patch_id": unknown_patch_id,
        "fat_patch_ids": fat_patch_ids,
    }


def _bucket_snapshot(bucket_root: Path) -> dict[str, bytes]:
    """Every file under the bucket root, by relative path -> bytes."""
    return {
        str(p.relative_to(bucket_root)): p.read_bytes()
        for p in sorted(bucket_root.rglob("*"))
        if p.is_file()
    }


def _read_trail_events(slug: str, trace_id: str) -> list[dict]:
    from opentraces.core.bucket_layout import trace_v1_trail_path

    path = trace_v1_trail_path(slug, trace_id)
    raw = gzip.decompress(path.read_bytes()).decode("utf-8")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def _search_events(events: list[dict]) -> list[dict]:
    return [e for e in events if e["event_type"] == "git_anchor_search_completed"]


def test_dry_run_reports_and_mutates_nothing(tmp_path: Path) -> None:
    from opentraces.core import paths
    from opentraces.core.bucket_reclaim_search import reclaim_anchor_search

    world = _build_world(tmp_path)
    before_head = subprocess.run(
        ["git", "rev-parse", "refs/opentraces/local/events/v1"], cwd=world["repo"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    before_bucket = _bucket_snapshot(paths.bucket_dir())

    result = reclaim_anchor_search(apply=False)

    proj = next(p for p in result.projects if p.project_slug == world["slug"])
    assert proj.action == "compacted"
    assert proj.legacy_events_collapsed == 2
    assert proj.fat_summaries_rewritten == 1
    assert proj.companions_regenerated  # the plan, not yet executed
    assert proj.bytes_before > proj.bytes_after  # a real, honest preview

    after_head = subprocess.run(
        ["git", "rev-parse", "refs/opentraces/local/events/v1"], cwd=world["repo"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert after_head == before_head
    assert _bucket_snapshot(paths.bucket_dir()) == before_bucket


def test_apply_rewrites_ref_mirror_and_companions(tmp_path: Path) -> None:
    from opentraces.core.bucket_events import read_events_mirror_batches
    from opentraces.core.bucket_reclaim_search import reclaim_anchor_search
    from opentraces.core.bucket_store import restore_trail_events_to_repo

    world = _build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]
    before_head = subprocess.run(
        ["git", "rev-parse", "refs/opentraces/local/events/v1"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    result = reclaim_anchor_search(apply=True)
    proj = next(p for p in result.projects if p.project_slug == slug)

    assert proj.action == "compacted"
    assert proj.ref_rewritten is True
    assert proj.mirror_rewritten is True
    assert proj.bytes_before > proj.bytes_after

    after_head = subprocess.run(
        ["git", "rev-parse", "refs/opentraces/local/events/v1"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert after_head != before_head

    # No legacy or v2-fat shape survives on the live ref.
    live_events = read_events(repo, verify=False)
    for event in live_events:
        if event.event_type != "git_anchor_search_completed":
            continue
        payload = event.payload or {}
        assert "coverage" in payload or "unanchored_trace_patch_ids" in payload

    verdict = verify_event_log(repo)
    assert verdict["event_chain_valid"] is True
    assert verdict["content_hashes_valid"] is True

    # Replay property: a fresh clone reconstructs the SAME chain from the
    # rewritten mirror byte-identically at the event level.
    fresh = tmp_path / "replayed"
    _init_repo(fresh)
    restore_trail_events_to_repo(fresh, repo_id=slug, force=True)
    replayed_events = read_events(fresh, verify=False)
    assert [e.event_id for e in replayed_events] == [e.event_id for e in live_events]
    replay_verdict = verify_event_log(fresh)
    assert replay_verdict["event_chain_valid"] is True
    assert replay_verdict["content_hashes_valid"] is True

    mirror_events = list(read_events_mirror_batches())
    assert {e.event_id for e in mirror_events} == {e.event_id for e in live_events}

    # Per-trace reads still work: the anchored trace keeps ONE (now small)
    # search event; the previously-fat unanchored trace loses search-event
    # touch entirely but its companion is still readable.
    anchored_trail = _read_trail_events(slug, "t-anchored")
    anchored_search = _search_events(anchored_trail)
    assert len(anchored_search) == 1
    assert anchored_search[0]["payload"]["results"][0]["result"] == "anchored"
    assert all(r["result"] == "anchored" for r in anchored_search[0]["payload"]["results"])

    fat_trail = _read_trail_events(slug, "t-fat-unanchored")
    assert _search_events(fat_trail) == []
    assert any(e["event_type"] == "trace_patch_created" for e in fat_trail)


def test_second_apply_is_idempotent(tmp_path: Path) -> None:
    from opentraces.core import paths
    from opentraces.core.bucket_reclaim_search import reclaim_anchor_search

    world = _build_world(tmp_path)
    reclaim_anchor_search(apply=True)

    after_first = _bucket_snapshot(paths.bucket_dir())
    result2 = reclaim_anchor_search(apply=True)
    proj2 = next(p for p in result2.projects if p.project_slug == world["slug"])

    assert proj2.bytes_reclaimed == 0
    assert proj2.ref_rewritten is False
    assert proj2.mirror_rewritten is False
    assert proj2.companions_regenerated == []
    assert _bucket_snapshot(paths.bucket_dir()) == after_first


def test_kill_mid_run_leaves_readable_bucket_and_resume_completes(tmp_path: Path) -> None:
    from opentraces.core import bucket_reclaim_search as reclaim_mod

    world = _build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]
    before_head = subprocess.run(
        ["git", "rev-parse", "refs/opentraces/local/events/v1"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated kill before companion regen")

    # A LOCAL MonkeyPatch instance, undone explicitly right after the
    # simulated kill -- the fixture-provided ``monkeypatch`` is shared with
    # the autouse bucket/HOME isolation fixture (same function-scoped
    # instance), so calling ``.undo()`` on IT would also roll back that
    # isolation and start reading/writing the real ``~/.opentraces``.
    kill_patch = pytest.MonkeyPatch()
    kill_patch.setattr(reclaim_mod, "project_per_trace_exports", _boom)
    with pytest.raises(RuntimeError, match="simulated kill"):
        reclaim_mod.reclaim_anchor_search(apply=True)
    kill_patch.undo()

    # Ref + mirror already advanced past the interrupted point; bucket is
    # readable (companions are stale/fat, not corrupt).
    mid_head = subprocess.run(
        ["git", "rev-parse", "refs/opentraces/local/events/v1"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert mid_head != before_head
    verdict = verify_event_log(repo)
    assert verdict["event_chain_valid"] is True

    stale_anchored = _read_trail_events(slug, "t-anchored")
    assert len(_search_events(stale_anchored)) == 2  # still the pre-compaction pair
    stale_fat = _read_trail_events(slug, "t-fat-unanchored")
    assert len(_search_events(stale_fat)) == 1  # still the fat v2 summary

    # Re-run (no monkeypatch): finishes the interrupted companion step.
    result = reclaim_mod.reclaim_anchor_search(apply=True)
    proj = next(p for p in result.projects if p.project_slug == slug)
    assert proj.action == "compacted"

    resumed_head = subprocess.run(
        ["git", "rev-parse", "refs/opentraces/local/events/v1"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert resumed_head == mid_head  # ref/mirror already correct -- untouched again

    anchored_trail = _read_trail_events(slug, "t-anchored")
    assert len(_search_events(anchored_trail)) == 1
    fat_trail = _read_trail_events(slug, "t-fat-unanchored")
    assert _search_events(fat_trail) == []

    # A THIRD run is a true no-op.
    result3 = reclaim_mod.reclaim_anchor_search(apply=True)
    proj3 = next(p for p in result3.projects if p.project_slug == slug)
    assert proj3.bytes_reclaimed == 0

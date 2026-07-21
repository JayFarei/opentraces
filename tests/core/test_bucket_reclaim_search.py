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
import shutil
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


def _build_unreachable_world(tmp_path: Path) -> dict:
    """Like ``_build_world``, but the project's repo directory is moved
    ASIDE afterward (not deleted) -- ``_iter_opted_in_projects`` skips a
    registered project whose on-disk path is missing, so this reproduces
    "repo unreachable" exactly (a missing/unmounted path, or an un-enrolled
    project), while ``world["moved_repo"]`` lets a test move it BACK to
    prove the "project becomes reachable again" scenario without changing
    its Git history at all (same head, byte-identical repo)."""

    world = _build_world(tmp_path)
    moved = tmp_path / "proj-moved-aside"
    shutil.move(str(world["repo"]), str(moved))
    world["moved_repo"] = moved
    return world


def _build_second_clean_project(tmp_path: Path, *, trace_id: str) -> dict:
    """A second, ordinary project with NO anchor-search history at all --
    used to prove a per-project failure elsewhere doesn't stop this one
    from being processed normally."""
    from opentraces.core.bucket_store import bucket_repair, write_trace_record
    from opentraces.core.config import get_project_dir, load_config, register_project, save_config

    repo = tmp_path / "proj-clean"
    _init_repo(repo)
    append_event_batch(
        repo,
        [_patch_created(trace_id=trace_id, trace_patch_id=f"tracepatch-sha256:{'c' * 64}", file_path="c.py", step_index=0)],
        writer="capture-claude-code",
    )
    cfg = load_config()
    register_project(cfg, repo)
    save_config(cfg)
    slug = get_project_dir(repo).name
    write_trace_record(_record(trace_id), project_slug=slug, source_layer="canonical")
    bucket_repair(dry_run=False)
    return {"repo": repo, "slug": slug}


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


def _raw_mirror_event_ids() -> list[str]:
    """Every ``event_id`` across ALL mirror batch files, WITHOUT the
    duplicate-collapsing ``read_events_mirror_batches`` now applies -- used
    to prove a crash-window superset is physically on disk, not just that
    the deduping reader tolerates it."""
    from opentraces.core.bucket_layout import events_v1_batches_dir

    batches_dir = events_v1_batches_dir()
    if not batches_dir.exists():
        return []
    ids: list[str] = []
    for path in sorted(batches_dir.glob("*.jsonl.gz")):
        raw = gzip.decompress(path.read_bytes()).decode("utf-8")
        ids.extend(json.loads(line)["event_id"] for line in raw.splitlines() if line.strip())
    return ids


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
    # Call the per-project worker directly, not ``reclaim_anchor_search`` --
    # the top-level entry point now CATCHES a per-project exception (issue
    # #358 repair: one bad project must never sink the whole reclaim pass,
    # see test_one_project_error_does_not_sink_the_others), so it would no
    # longer raise here. Driving the worker directly is what actually
    # simulates "the process died mid-run" for this test's purpose.
    with pytest.raises(RuntimeError, match="simulated kill"):
        reclaim_mod._process_reachable_project(slug, repo, apply=True)
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


def test_resume_with_journal_but_missing_ref_errors_without_wiping_mirror(tmp_path: Path) -> None:
    """Issue #358 repair (blocker): a journal survives a kill (written
    durably BEFORE the ref swap), but the project's canonical ref then goes
    missing before resume -- e.g. a re-clone during crash recovery, since
    ``refs/opentraces/local/events/v1`` is local-only and never pushed. The
    old code treated ``head is None`` on resume as "compacted to an empty
    chain" and, via the journal's own removal scope, deleted every mirror
    batch file for this project (its sole surviving copy at that point) and
    regenerated every affected companion to empty. Resume must refuse
    instead, leaving the mirror/companions untouched and the journal in
    place for a future recovery."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod
    from opentraces.core.bucket_events import read_events_mirror_batches

    world = _build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]

    mirror_before = list(read_events_mirror_batches())
    assert mirror_before
    anchored_before = _read_trail_events(slug, "t-anchored")
    assert anchored_before

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated kill before ref swap")

    patch = pytest.MonkeyPatch()
    patch.setattr(reclaim_mod, "import_event_log", _boom)
    with pytest.raises(RuntimeError, match="simulated kill before ref swap"):
        reclaim_mod._process_reachable_project(slug, repo, apply=True)
    patch.undo()
    assert reclaim_mod._journal_path(slug).exists(), "journal must persist after the kill"

    # Crash-recovery re-clone: refs/opentraces/* are local-only, never
    # pushed, so a fresh clone genuinely lacks this ref.
    subprocess.run(
        ["git", "update-ref", "-d", "refs/opentraces/local/events/v1"],
        cwd=repo, check=True,
    )

    result = reclaim_mod.reclaim_anchor_search(apply=True)
    proj = next(p for p in result.projects if p.project_slug == slug)

    assert proj.action == "error"
    assert "missing" in (proj.reason or "")
    assert any(slug in err for err in result.errors)

    # The journal is NOT cleared on this failure path -- a future run, once
    # the ref is restored, can still recover from it.
    assert reclaim_mod._journal_path(slug).exists()

    mirror_after = list(read_events_mirror_batches())
    assert {e.event_id for e in mirror_after} == {e.event_id for e in mirror_before}
    assert _read_trail_events(slug, "t-anchored") == anchored_before


def _assert_mirror_matches_live_and_replays(tmp_path: Path, repo: Path, slug: str, label: str) -> None:
    """Shared post-resume assertion for the kill-injection tests below: the
    mirror's event_id set is EXACTLY the live canonical chain's (no stale
    leftovers, no duplicates), the chain itself verifies, a fresh clone
    replays it byte-identically at the event level, AND — the concrete
    consequence a wrong ordinal/filename scheme produces (issue #358 repair
    finding) — a FOLLOW-UP, independent ``sync_events_mirror`` call stays a
    clean no-op rather than duplicating everything under a second set of
    filenames."""
    from opentraces.core.bucket_events import read_events_mirror_batches, sync_events_mirror
    from opentraces.core.bucket_store import restore_trail_events_to_repo

    live_events = read_events(repo, verify=False)
    live_ids = [e.event_id for e in live_events]
    mirror_ids = [e.event_id for e in read_events_mirror_batches()]
    assert sorted(mirror_ids) == sorted(live_ids)
    assert len(mirror_ids) == len(set(mirror_ids)), "mirror carries duplicate event_ids"

    verdict = verify_event_log(repo)
    assert verdict["event_chain_valid"] is True
    assert verdict["content_hashes_valid"] is True

    fresh = tmp_path / f"replayed-{label}"
    _init_repo(fresh)
    restore_trail_events_to_repo(fresh, repo_id=slug, force=True)
    replayed_ids = [e.event_id for e in read_events(fresh, verify=False)]
    assert replayed_ids == live_ids
    assert verify_event_log(fresh)["event_chain_valid"] is True

    sync_events_mirror(repo, repo_id=slug)
    after_sync_ids = [e.event_id for e in read_events_mirror_batches()]
    assert len(after_sync_ids) == len(set(after_sync_ids)), (
        "a routine sync after resume duplicated the mirror"
    )
    assert sorted(after_sync_ids) == sorted(live_ids)


def test_kill_between_ref_swap_and_mirror_write_resume_removes_stale(tmp_path: Path) -> None:
    """Issue #358 repair (blocker): a kill AFTER ``import_event_log`` swaps
    the ref but BEFORE the mirror is touched at all used to leave the mirror
    permanently fat -- resume recomputed ``old_ids`` from the ALREADY-
    compacted ref, saw ``old_ids == target_ids`` (compaction is idempotent on
    compact input), and concluded there was nothing stale to remove. The
    per-project journal (written durably before the ref swap) is what makes
    resume see the ORIGINAL removal target instead."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod
    from opentraces.core.bucket_events import read_events_mirror_batches

    world = _build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated kill between ref swap and mirror write")

    patch = pytest.MonkeyPatch()
    patch.setattr(reclaim_mod, "_reconcile_mirror_for_project", _boom)
    with pytest.raises(RuntimeError, match="simulated kill between ref swap"):
        reclaim_mod._process_reachable_project(slug, repo, apply=True)
    patch.undo()

    # Prove the crash window is real: ref already moved, mirror untouched.
    live_ids_mid = {e.event_id for e in read_events(repo, verify=False)}
    mirror_ids_mid = {e.event_id for e in read_events_mirror_batches()}
    assert mirror_ids_mid != live_ids_mid

    result = reclaim_mod.reclaim_anchor_search(apply=True)
    proj = next(p for p in result.projects if p.project_slug == slug)
    assert proj.action == "compacted"

    _assert_mirror_matches_live_and_replays(tmp_path, repo, slug, "a1")

    result2 = reclaim_mod.reclaim_anchor_search(apply=True)
    proj2 = next(p for p in result2.projects if p.project_slug == slug)
    assert proj2.bytes_reclaimed == 0


def test_kill_inside_mirror_write_resume_completes(tmp_path: Path) -> None:
    """Issue #358 repair (blocker): a kill INSIDE the mirror reconcile call --
    new batch file(s) already written, stale file(s) already removed, but the
    index (and therefore the journal clear) never committed -- must also
    resume cleanly to a mirror that matches the live chain exactly, not a
    mix of two differently-numbered layouts (the concrete duplication repro
    from the #358 repair finding)."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod
    from opentraces.core.bucket_events import read_events_mirror_batches

    world = _build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]

    real_write_json = reclaim_mod._atomic_write_json

    def _boom_on_index_write(path, payload):
        if path.name == "index.json":
            raise RuntimeError("simulated kill mid mirror write")
        return real_write_json(path, payload)

    patch = pytest.MonkeyPatch()
    patch.setattr(reclaim_mod, "_atomic_write_json", _boom_on_index_write)
    with pytest.raises(RuntimeError, match="simulated kill mid mirror write"):
        reclaim_mod._process_reachable_project(slug, repo, apply=True)
    patch.undo()

    # The batch files themselves already mutated (write-new-then-remove-stale
    # inside _reconcile_mirror_for_project ran to completion); only the index
    # commit (and the journal clear that follows it) was interrupted.
    mirror_ids_mid = {e.event_id for e in read_events_mirror_batches()}
    live_ids_mid = {e.event_id for e in read_events(repo, verify=False)}
    assert mirror_ids_mid == live_ids_mid  # the file mutations themselves were already correct

    result = reclaim_mod.reclaim_anchor_search(apply=True)
    proj = next(p for p in result.projects if p.project_slug == slug)
    assert proj.action == "compacted"

    _assert_mirror_matches_live_and_replays(tmp_path, repo, slug, "a2")

    result2 = reclaim_mod.reclaim_anchor_search(apply=True)
    proj2 = next(p for p in result2.projects if p.project_slug == slug)
    assert proj2.bytes_reclaimed == 0


def test_kill_before_stale_removal_leaves_broken_reads_until_repair_heals_it(tmp_path: Path) -> None:
    """Issue #358 repair (major): a kill INSIDE ``_reconcile_mirror_for_
    project`` AFTER the new consolidated batch file is written but BEFORE
    the stale-removal loop completes even one iteration leaves a strict
    superset on disk -- the old AND new files together. ``compact_search_
    events`` re-chains the WHOLE stream from the first touched slot onward
    (``search_compaction._refinalize``), so only the untouched prefix keeps
    its original ``event_id``; everything superseded gets a genuinely
    DIFFERENT one. ``read_events_mirror_batches`` collapses the true (same-
    id) duplicates -- real, but partial: the mismatched-id leftovers are not
    something a generic reader can safely arbitrate on its own, so
    ``restore_trail_events_to_repo`` still raises mid-window. What actually
    closes the window is this project's OWN reconcile finishing --
    ``resume_pending_anchor_search_journals`` (which ``bucket_repair`` now
    runs before its own per-project mirror sync) -- not read-side
    tolerance."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod
    from opentraces.core.bucket_events import read_events_mirror_batches
    from opentraces.core.bucket_store import restore_trail_events_to_repo

    world = _build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]

    def _boom_on_first_read(path):
        raise RuntimeError("simulated kill before stale removal")

    patch = pytest.MonkeyPatch()
    patch.setattr(reclaim_mod, "_read_gzip_bytes", _boom_on_first_read)
    with pytest.raises(RuntimeError, match="simulated kill before stale removal"):
        reclaim_mod._process_reachable_project(slug, repo, apply=True)
    patch.undo()

    live_ids = [e.event_id for e in read_events(repo, verify=False)]
    raw_ids_mid = _raw_mirror_event_ids()
    assert len(raw_ids_mid) > len(live_ids), "crash window not reached -- repro invalid"
    assert len(raw_ids_mid) != len(set(raw_ids_mid)), "no physical duplicate on disk -- repro invalid"

    # The dedup fix (bucket_events.py) collapses the TRUE (same event_id)
    # duplicates -- no raise, no duplicate ids -- but that alone does not
    # reconstruct the live chain: genuinely superseded (different-id) stale
    # content is still mixed in.
    deduped_ids = [e.event_id for e in read_events_mirror_batches()]
    assert len(deduped_ids) == len(set(deduped_ids))
    assert sorted(deduped_ids) != sorted(live_ids)

    # The concrete consumer the finding named: replay mid-window DOES still
    # raise -- read-side tolerance alone is not the fix.
    fresh = tmp_path / "replayed-mid-window"
    _init_repo(fresh)
    with pytest.raises(ValueError, match="contiguous event_sequence"):
        restore_trail_events_to_repo(fresh, repo_id=slug, force=True)

    # What actually closes the window: the project's OWN reconcile finishing.
    # ``resume_pending_anchor_search_journals`` scopes to ONLY the slug with a
    # pending journal (never opens an unrelated project's log looking for new
    # fat content), matching what ``bucket_repair`` now runs automatically.
    healed = reclaim_mod.resume_pending_anchor_search_journals()
    assert [r.project_slug for r in healed] == [slug]
    assert healed[0].action == "compacted"
    assert not reclaim_mod._journal_path(slug).exists()

    raw_ids_after = _raw_mirror_event_ids()
    assert sorted(raw_ids_after) == sorted(live_ids)
    assert len(raw_ids_after) == len(set(raw_ids_after))

    fresh2 = tmp_path / "replayed-after-heal"
    _init_repo(fresh2)
    restore_trail_events_to_repo(fresh2, repo_id=slug, force=True)
    assert [e.event_id for e in read_events(fresh2, verify=False)] == live_ids

    # A subsequent full reclaim run stays a true no-op -- healing via the
    # scoped resume already finished everything a full pass would have done.
    result = reclaim_mod.reclaim_anchor_search(apply=True)
    proj = next(p for p in result.projects if p.project_slug == slug)
    assert proj.bytes_reclaimed == 0


def test_bucket_repair_heals_pending_anchor_search_journal_before_its_own_sync(tmp_path: Path) -> None:
    """Issue #358 repair (major): the same crash window, but proving the
    ACTUAL routine path the finding named -- a plain ``bucket_repair()``
    call (no direct reference to the reclaim module) must heal a pending
    journal before its own per-project ``sync_events_mirror`` runs, so nobody
    has to remember to re-run ``bucket reclaim --apply``. Dry-run must not
    mutate anything (same read-only contract as the rest of this pass)."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod
    from opentraces.core.bucket_events import read_events_mirror_batches
    from opentraces.core.bucket_store import bucket_repair

    world = _build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]

    def _boom_on_first_read(path):
        raise RuntimeError("simulated kill before stale removal")

    patch = pytest.MonkeyPatch()
    patch.setattr(reclaim_mod, "_read_gzip_bytes", _boom_on_first_read)
    with pytest.raises(RuntimeError, match="simulated kill before stale removal"):
        reclaim_mod._process_reachable_project(slug, repo, apply=True)
    patch.undo()

    raw_ids_mid = _raw_mirror_event_ids()
    assert len(raw_ids_mid) != len(set(raw_ids_mid))

    # dry_run must not touch the pending journal or the mirror.
    bucket_repair(dry_run=True)
    assert reclaim_mod._journal_path(slug).exists()
    assert sorted(_raw_mirror_event_ids()) == sorted(raw_ids_mid)

    bucket_repair(dry_run=False)

    assert not reclaim_mod._journal_path(slug).exists()
    live_ids = [e.event_id for e in read_events(repo, verify=False)]
    mirror_ids = [e.event_id for e in read_events_mirror_batches()]
    assert sorted(mirror_ids) == sorted(live_ids)
    raw_ids_after = _raw_mirror_event_ids()
    assert len(raw_ids_after) == len(set(raw_ids_after))


def test_reclaim_then_normal_sync_no_duplicates_single_project(tmp_path: Path) -> None:
    """Issue #358 repair (blocker, repro-confirmed): a routine
    ``sync_events_mirror`` call AFTER ``bucket reclaim --apply`` used to
    duplicate every event in the mirror, because reclaim stamped
    ``event_log_head: None`` (forcing the next sync into a full,
    never-cleans-up-stale-files rebuild) and renumbered batch files with an
    ordinal scheme a standalone rebuild would never reproduce."""
    from opentraces.core.bucket_events import read_events_mirror_batches, sync_events_mirror
    from opentraces.core.bucket_reclaim_search import reclaim_anchor_search

    world = _build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]

    reclaim_anchor_search(apply=True)
    mid_ids = [e.event_id for e in read_events_mirror_batches()]
    assert len(mid_ids) == len(set(mid_ids))

    sync_events_mirror(repo, repo_id=slug)  # a routine watcher tick / bucket repair

    after_ids = [e.event_id for e in read_events_mirror_batches()]
    assert len(after_ids) == len(set(after_ids)), "mirror duplicated after reclaim + a normal sync"
    assert sorted(after_ids) == sorted({e.event_id for e in read_events(repo, verify=False)})


def test_reclaim_then_normal_sync_no_duplicates_multi_project(tmp_path: Path) -> None:
    """Same as the single-project case, but with a SECOND, untouched project
    sharing the mirror -- reclaim must not disturb project B's own batch
    files, and B's own later sync must not collide with what reclaim wrote
    for A."""
    from opentraces.core.bucket_events import read_events_mirror_batches, sync_events_mirror
    from opentraces.core.bucket_reclaim_search import reclaim_anchor_search

    world = _build_world(tmp_path / "a")
    other = _build_second_clean_project(tmp_path / "b", trace_id="tB")

    reclaim_anchor_search(apply=True)

    sync_events_mirror(world["repo"], repo_id=world["slug"])
    sync_events_mirror(other["repo"], repo_id=other["slug"])

    after_ids = [e.event_id for e in read_events_mirror_batches()]
    assert len(after_ids) == len(set(after_ids)), "mirror duplicated after reclaim + a normal sync"

    expected = {e.event_id for e in read_events(world["repo"], verify=False)} | {
        e.event_id for e in read_events(other["repo"], verify=False)
    }
    assert set(after_ids) == expected


def test_ref_bytes_are_not_counted_as_reclaimed(tmp_path: Path) -> None:
    """Issue #358 repair (major): ``import_event_log`` writes a whole NEW
    chain and moves the ref, but the OLD chain's objects stay in the Git
    object database (unreachable, retained -- this module never runs
    ``git gc``). The headline ``bytes_reclaimed`` must therefore count only
    the companion shrink that is REALLY on disk, with the ref-side delta
    reported separately and honestly."""
    from opentraces.core.bucket_reclaim_search import reclaim_anchor_search

    world = _build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]
    before_head = subprocess.run(
        ["git", "rev-parse", "refs/opentraces/local/events/v1"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    result = reclaim_anchor_search(apply=True)
    proj = next(p for p in result.projects if p.project_slug == slug)

    assert proj.ref_bytes_reclaimable_after_gc > 0
    assert proj.bytes_before == proj.companion_bytes_before
    assert proj.bytes_after == proj.companion_bytes_after
    assert proj.bytes_reclaimed == proj.companion_bytes_before - proj.companion_bytes_after
    assert result.bytes_reclaimed == sum(
        p.companion_bytes_before - p.companion_bytes_after for p in result.projects
    )

    # The old head's objects are still retained -- exactly what makes
    # counting the ref delta as "reclaimed" dishonest without a gc.
    still_present = subprocess.run(
        ["git", "cat-file", "-e", before_head], cwd=repo, capture_output=True,
    )
    assert still_present.returncode == 0


def test_unreachable_dry_run_reports_and_mutates_nothing(tmp_path: Path) -> None:
    from opentraces.core import paths
    from opentraces.core.bucket_reclaim_search import reclaim_anchor_search

    world = _build_unreachable_world(tmp_path)
    before_bucket = _bucket_snapshot(paths.bucket_dir())

    result = reclaim_anchor_search(apply=False)
    proj = next(p for p in result.projects if p.project_slug == world["slug"])

    assert proj.repo_reachable is False
    assert proj.action == "mirror_only_compacted"
    assert proj.mirror_rewritten is True
    assert _bucket_snapshot(paths.bucket_dir()) == before_bucket


def test_unreachable_apply_compacts_mirror_and_companions(tmp_path: Path) -> None:
    from opentraces.core.bucket_events import read_events_mirror_batches
    from opentraces.core.bucket_reclaim_search import reclaim_anchor_search

    world = _build_unreachable_world(tmp_path)
    slug = world["slug"]

    result = reclaim_anchor_search(apply=True)
    proj = next(p for p in result.projects if p.project_slug == slug)

    assert proj.action == "mirror_only_compacted"
    assert proj.mirror_rewritten is True

    mirror_events = list(read_events_mirror_batches())
    mirror_ids = [e.event_id for e in mirror_events]
    assert len(mirror_ids) == len(set(mirror_ids))
    for event in mirror_events:
        if event.event_type != "git_anchor_search_completed":
            continue
        payload = event.payload or {}
        assert "coverage" in payload or "unanchored_trace_patch_ids" in payload

    anchored_trail = _read_trail_events(slug, "t-anchored")
    assert len(_search_events(anchored_trail)) == 1
    fat_trail = _read_trail_events(slug, "t-fat-unanchored")
    assert _search_events(fat_trail) == []


def test_unreachable_second_apply_is_idempotent(tmp_path: Path) -> None:
    from opentraces.core import paths
    from opentraces.core.bucket_reclaim_search import reclaim_anchor_search

    world = _build_unreachable_world(tmp_path)
    reclaim_anchor_search(apply=True)

    after_first = _bucket_snapshot(paths.bucket_dir())
    result2 = reclaim_anchor_search(apply=True)
    proj2 = next(p for p in result2.projects if p.project_slug == world["slug"])

    assert proj2.bytes_reclaimed == 0
    assert proj2.mirror_rewritten is False
    assert _bucket_snapshot(paths.bucket_dir()) == after_first


def test_unreachable_project_reappearing_does_not_duplicate_on_sync(tmp_path: Path) -> None:
    """Issue #358 repair (major): mirror-only compaction used to stamp
    ``event_log_head: None``, so once the project's repo path comes back
    (a remount, or the project being re-enrolled) the NEXT routine sync
    took the full-rebuild-without-cleanup path and duplicated every event.
    Preserving the pre-existing (still-accurate, since this path never
    rewrites the repo's own ref) ``event_log_head`` closes that."""
    from opentraces.core.bucket_events import read_events_mirror_batches, sync_events_mirror
    from opentraces.core.bucket_reclaim_search import reclaim_anchor_search

    world = _build_unreachable_world(tmp_path)
    slug = world["slug"]

    reclaim_anchor_search(apply=True)

    # The repo "comes back" byte-identically (same Git history / head).
    shutil.move(str(world["moved_repo"]), str(world["repo"]))

    sync_events_mirror(world["repo"], repo_id=slug)

    after_ids = [e.event_id for e in read_events_mirror_batches()]
    assert len(after_ids) == len(set(after_ids)), "mirror duplicated once the project became reachable again"


def test_one_project_error_does_not_sink_the_others(tmp_path: Path) -> None:
    """Issue #358 repair (major): one project's processing failure (a git
    error mid-swap, a corrupt event) must not take down the whole ``bucket
    reclaim`` verb -- every OTHER project still gets processed and reported,
    and the failure is surfaced in ``errors`` / that project's own
    ``action="error"`` row instead of propagating."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod

    world = _build_world(tmp_path / "a")
    other = _build_second_clean_project(tmp_path / "b", trace_id="tB")

    real_process = reclaim_mod._process_reachable_project

    def _boom(slug, repo, *, apply):
        if slug == world["slug"]:
            raise RuntimeError("simulated per-project failure")
        return real_process(slug, repo, apply=apply)

    patch = pytest.MonkeyPatch()
    patch.setattr(reclaim_mod, "_process_reachable_project", _boom)
    try:
        result = reclaim_mod.reclaim_anchor_search(apply=True)  # must NOT raise
    finally:
        patch.undo()

    assert any("simulated per-project failure" in err for err in result.errors)

    proj_a = next(p for p in result.projects if p.project_slug == world["slug"])
    assert proj_a.action == "error"
    assert proj_a.reason == "simulated per-project failure"

    proj_b = next(p for p in result.projects if p.project_slug == other["slug"])
    assert proj_b.action == "clean"  # unaffected by A's failure


# ---------------------------------------------------------------------------
# Issue #358 repair v3: CAS-on-concurrent-append (blocker) + dry-run/apply
# mirror event-count parity (major) -- both verifier-confirmed against a real
# end-to-end repro.
# ---------------------------------------------------------------------------


def test_concurrent_append_during_compaction_survives_via_cas_retry(tmp_path: Path) -> None:
    """Issue #358 repair (blocker, verifier-confirmed): ``_process_reachable_
    project`` snapshots the chain (``read_events``), spends minutes of
    O(corpus) work compacting it, then used to swap against a FRESH self-read
    -- which, if some OTHER writer (a hot post-commit hook reconcile, a
    watcher maturation tick, a trail attach) appended an event during that
    window, already reflected the append. The CAS trivially "succeeded"
    against data computed from the STALE pre-append snapshot, so the append
    was force-overwritten out of the ref -- absent from ``read_events``
    afterwards even though the report said ``action=compacted`` with zero
    errors. The append must survive in the canonical chain, the mirror, AND
    its trace's companion, with the report honest that a retry happened."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod
    from opentraces.core.bucket_events import read_events_mirror_batches

    world = _build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]

    real_compact = reclaim_mod.compact_search_events
    concurrent_patch_id = "9" + "c" * 63
    commit_c = "c" * 40

    def _compact_then_concurrent_append(events):
        result = real_compact(events)
        # The exact race window the finding names: a writer lands new
        # content WHILE this (real, minutes-long on the motivating 27GB
        # bucket) compaction call is still running -- a real post-commit
        # hook reconcile creates the patch AND immediately searches it, so
        # the delta carries both events, matching the module docstring's
        # own list of concurrent writers (hot hook / watcher maturation /
        # trail attach).
        append_event_batch(
            repo,
            [
                _patch_created(
                    trace_id="t-concurrent", trace_patch_id=f"tracepatch-sha256:{concurrent_patch_id}",
                    file_path="concurrent.py", step_index=0,
                ),
                _legacy_search(
                    trace_id="t-concurrent", step_index=0, patch_id=concurrent_patch_id,
                    commit_hex=commit_c, result="anchored", anchor_ids=["gitanchor-sha256:" + "c" * 64],
                ),
            ],
            writer="watcher",
        )
        return result

    patch = pytest.MonkeyPatch()
    patch.setattr(reclaim_mod, "compact_search_events", _compact_then_concurrent_append)
    try:
        proj = reclaim_mod._process_reachable_project(slug, repo, apply=True)
    finally:
        patch.undo()

    assert proj.action == "compacted"
    assert proj.swap_retries == 1

    live_events = read_events(repo, verify=False)
    assert proj.events_after == len(live_events)
    verdict = verify_event_log(repo)
    assert verdict["event_chain_valid"] is True
    assert verdict["content_hashes_valid"] is True

    def _touches_concurrent_patch(event) -> bool:
        if event.event_type != "git_anchor_search_completed":
            return False
        return any(
            r.get("trace_patch_id") == f"tracepatch-sha256:{concurrent_patch_id}"
            for r in (event.payload or {}).get("results") or []
        )

    concurrent_live = [e for e in live_events if _touches_concurrent_patch(e)]
    assert len(concurrent_live) == 1, "the concurrently-appended search event is missing from the final chain"

    mirror_ids = {e.event_id for e in read_events_mirror_batches()}
    assert concurrent_live[0].event_id in mirror_ids, "the concurrently-appended event is missing from the mirror"

    assert "t-concurrent" in proj.companions_regenerated
    concurrent_trail = _read_trail_events(slug, "t-concurrent")
    assert any(
        e["event_type"] == "trace_patch_created"
        and e["payload"].get("trace_patch_id") == f"tracepatch-sha256:{concurrent_patch_id}"
        for e in concurrent_trail
    ), "the concurrently-appended patch is missing from its trace's companion"
    assert any(
        e["event_type"] == "git_anchor_search_completed"
        and any(
            r.get("trace_patch_id") == f"tracepatch-sha256:{concurrent_patch_id}"
            for r in (e["payload"].get("results") or [])
        )
        for e in concurrent_trail
    ), "the concurrently-appended search event is missing from its trace's companion"


def test_concurrent_appends_every_retry_exhausts_to_error_without_partial_swap(tmp_path: Path) -> None:
    """Issue #358 repair (blocker): a writer hot enough to land a NEW append
    before EVERY retry attempt must not livelock the swap forever. On
    exhausting the bound: nothing is lost (every hot event a real writer
    landed is still on the ref -- this pass's swap never actually landed),
    nothing is half-swapped (mirror/companions stay byte-identical to before
    this run), and the journal survives for a future attempt."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod
    from opentraces.core import paths
    from opentraces.core.trails.event_log import EventLogHeadMovedError

    world = _build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]
    before_bucket = _bucket_snapshot(paths.bucket_dir())

    calls = {"n": 0}

    def _always_races(cwd, events, *, writer, force, expected_head=None):
        calls["n"] += 1
        # A genuinely new, real writer lands another event before every
        # single retry attempt -- the worst case the bound exists for.
        append_event_batch(
            repo,
            [
                _patch_created(
                    trace_id=f"t-hot-{calls['n']}",
                    trace_patch_id=f"tracepatch-sha256:{calls['n']}{'e' * 63}",
                    file_path=f"hot{calls['n']}.py", step_index=0,
                )
            ],
            writer="watcher",
        )
        raise EventLogHeadMovedError("simulated: ref moved (test)")

    patch = pytest.MonkeyPatch()
    patch.setattr(reclaim_mod, "import_event_log", _always_races)
    try:
        result = reclaim_mod.reclaim_anchor_search(apply=True)
    finally:
        patch.undo()

    assert calls["n"] == reclaim_mod._SWAP_MAX_RETRIES

    proj = next(p for p in result.projects if p.project_slug == slug)
    assert proj.action == "error"
    assert any(slug in err for err in result.errors)
    assert reclaim_mod._journal_path(slug).exists(), "journal must survive for a future attempt"

    # Nothing lost: every hot writer's event actually landed on the ref --
    # our failed reclaim swap never touched it.
    live_events = read_events(repo, verify=False)
    for n in range(1, calls["n"] + 1):
        assert any(
            e.event_type == "trace_patch_created" and e.trace_id == f"t-hot-{n}"
            for e in live_events
        ), f"hot event {n} is missing from the canonical chain"

    # Nothing half-swapped: the swap never landed, so mirror/companions are
    # byte-identical to before this run touched anything -- excluding the
    # journal itself (under ``reclaim/``), which IS expected to exist now
    # (written durably before the first swap attempt; it is what makes the
    # next attempt recoverable).
    def _without_journal(snapshot: dict[str, bytes]) -> dict[str, bytes]:
        return {k: v for k, v in snapshot.items() if not k.startswith("reclaim" + "/")}

    after_bucket = _bucket_snapshot(paths.bucket_dir())
    assert _without_journal(after_bucket) == _without_journal(before_bucket)


def test_quiescent_apply_reports_zero_swap_retries(tmp_path: Path) -> None:
    """Issue #358 repair: the original quiescent-world behavior (no
    concurrent writer) is unchanged by the CAS-retry machinery -- zero
    retries, same as before this repair existed."""
    from opentraces.core.bucket_reclaim_search import reclaim_anchor_search

    world = _build_world(tmp_path)
    result = reclaim_anchor_search(apply=True)
    proj = next(p for p in result.projects if p.project_slug == world["slug"])
    assert proj.swap_retries == 0


def test_dry_run_and_apply_report_identical_mirror_event_counts(tmp_path: Path) -> None:
    """Issue #358 repair (major, verifier-confirmed): dry-run counted mirror
    deltas in EVENT units (``len(to_add)``, ``len(stale_ids & existing_
    ids)``) but apply overwrote them with ``_reconcile_mirror_for_
    project``'s return, which counts batch FILES written and every event id
    in a removed file (including untouched-but-relocated ones) -- a
    different unit that silently disagreed on the SAME quiescent world."""
    from opentraces.core.bucket_reclaim_search import reclaim_anchor_search

    world = _build_world(tmp_path)
    slug = world["slug"]

    dry = reclaim_anchor_search(apply=False)
    proj_dry = next(p for p in dry.projects if p.project_slug == slug)

    applied = reclaim_anchor_search(apply=True)
    proj_apply = next(p for p in applied.projects if p.project_slug == slug)

    assert proj_dry.mirror_events_added > 0
    assert proj_dry.mirror_events_removed > 0
    assert proj_apply.mirror_events_added == proj_dry.mirror_events_added
    assert proj_apply.mirror_events_removed == proj_dry.mirror_events_removed

    # The file-level numbers are a DIFFERENT unit, honestly reported under
    # their own field names -- never conflated with the event counts above.
    assert proj_apply.mirror_batch_files_written >= 1


def test_unreachable_dry_run_and_apply_report_identical_mirror_event_counts(tmp_path: Path) -> None:
    """Same fix, mirror-only path (issue #358 repair, finding 2)."""
    from opentraces.core.bucket_reclaim_search import reclaim_anchor_search

    world = _build_unreachable_world(tmp_path)
    slug = world["slug"]

    dry = reclaim_anchor_search(apply=False)
    proj_dry = next(p for p in dry.projects if p.project_slug == slug)

    applied = reclaim_anchor_search(apply=True)
    proj_apply = next(p for p in applied.projects if p.project_slug == slug)

    assert proj_dry.mirror_events_added > 0
    assert proj_apply.mirror_events_added == proj_dry.mirror_events_added
    assert proj_apply.mirror_events_removed == proj_dry.mirror_events_removed


# ---------------------------------------------------------------------------
# Issue #358 repair v3 adversarial re-review: two further blockers and one
# major found IN the finding-1/finding-2 repairs above -- all verifier-
# confirmed against real repros.
# ---------------------------------------------------------------------------


def test_append_during_prefilter_window_is_not_duplicated(tmp_path: Path) -> None:
    """Issue #358 repair v3 (blocker, verifier-confirmed): the CAS base and
    the events snapshot were not taken atomically. ``head = _head_sha(repo)``
    was captured BEFORE the O(corpus) ``_events_tree_bytes`` prefilter walk
    and before ``read_events`` -- and ``read_events`` re-reads the ref head
    itself at call time. A writer that appends DURING that window (a hot
    post-commit hook reconcile, a watcher maturation tick, a trail attach --
    the module's own named concurrent writers) is therefore already inside
    ``old_events``/``compacted`` by the time ``read_events`` returns, while
    the CAS base variable still names the OLDER, pre-append commit. The swap
    then fails against that stale base, ``read_events_since(stale_head)``
    returns the SAME append as a 'delta' the caller believes is new, and
    ``compact_and_append`` folds it onto the compacted tail a SECOND time --
    silent duplication, not loss (the mirror image of finding 1, which this
    stage already fixed for the window BELOW ``read_events``). The
    concurrently-appended patch and its search result must each survive
    EXACTLY once in the final canonical chain."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod

    world = _build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]

    real_tree_bytes = reclaim_mod._events_tree_bytes
    concurrent_patch_id = "9" + "d" * 63
    commit_c = "d" * 40
    fired = {"done": False}

    def _tree_bytes_then_concurrent_append(r, head):
        result = real_tree_bytes(r, head)
        if not fired["done"]:
            fired["done"] = True
            append_event_batch(
                repo,
                [
                    _patch_created(
                        trace_id="t-window", trace_patch_id=f"tracepatch-sha256:{concurrent_patch_id}",
                        file_path="window.py", step_index=0,
                    ),
                    _legacy_search(
                        trace_id="t-window", step_index=0, patch_id=concurrent_patch_id,
                        commit_hex=commit_c, result="anchored",
                        anchor_ids=["gitanchor-sha256:" + "d" * 64],
                    ),
                ],
                writer="watcher",
            )
        return result

    patch = pytest.MonkeyPatch()
    patch.setattr(reclaim_mod, "_events_tree_bytes", _tree_bytes_then_concurrent_append)
    try:
        proj = reclaim_mod._process_reachable_project(slug, repo, apply=True)
    finally:
        patch.undo()

    assert proj.action == "compacted"

    live_events = read_events(repo, verify=False)
    verdict = verify_event_log(repo)
    assert verdict["event_chain_valid"] is True

    patch_copies = [
        e for e in live_events
        if e.event_type == "trace_patch_created"
        and (e.payload or {}).get("trace_patch_id") == f"tracepatch-sha256:{concurrent_patch_id}"
    ]
    assert len(patch_copies) == 1, (
        f"concurrently-appended patch appears {len(patch_copies)} times in the "
        f"final canonical chain (swap_retries={proj.swap_retries})"
    )

    search_copies = [
        e for e in live_events
        if e.event_type == "git_anchor_search_completed"
        and any(
            r.get("trace_patch_id") == f"tracepatch-sha256:{concurrent_patch_id}"
            for r in (e.payload or {}).get("results") or []
        )
    ]
    assert len(search_copies) == 1, (
        f"concurrently-appended search result appears in {len(search_copies)} "
        f"summaries in the final canonical chain (swap_retries={proj.swap_retries})"
    )


def test_swap_reports_import_events_own_head_not_a_stale_fresh_read(tmp_path: Path) -> None:
    """Issue #358 repair v3 (major, verifier-confirmed): ``_swap_compacted_
    chain`` returned a FRESH ``_head_sha(repo)`` read taken AFTER
    ``import_event_log`` had already landed the swap, instead of using
    ``import_event_log``'s OWN reported ``head`` -- the exact fresh-self-read
    anti-pattern finding 1 eliminated at the CAS boundary, reintroduced one
    line below it. A writer that appends between the ref update inside
    ``import_event_log`` and this module's own subsequent ``_head_sha`` call
    stamps the mirror index with a head NEWER than what the mirror actually
    reflects; ``sync_events_mirror``'s incremental fast path then sees
    ``prior_head == current_head`` and takes the no-op route forever,
    permanently hiding the raced event from the mirror."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod
    from opentraces.core.trails import import_event_log as real_import_event_log

    world = _build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]

    captured: dict[str, str] = {}
    raced = {"done": False}

    def _import_then_race(*args, **kwargs):
        result = real_import_event_log(*args, **kwargs)
        captured["swap_head"] = result["head"]
        if not raced["done"]:
            raced["done"] = True
            # A genuinely new writer lands right after the ref moves --
            # between `import_event_log`'s own `update-ref` and this
            # module's next `_head_sha` call.
            append_event_batch(
                repo,
                [
                    _patch_created(
                        trace_id="t-race2", trace_patch_id=f"tracepatch-sha256:{'8' * 64}",
                        file_path="race2.py", step_index=0,
                    )
                ],
                writer="watcher",
            )
        return result

    patch = pytest.MonkeyPatch()
    patch.setattr(reclaim_mod, "import_event_log", _import_then_race)
    try:
        proj = reclaim_mod._process_reachable_project(slug, repo, apply=True)
    finally:
        patch.undo()

    assert proj.action == "compacted"
    assert "swap_head" in captured

    from opentraces.core.bucket_layout import events_v1_index_path

    index = json.loads(events_v1_index_path().read_text(encoding="utf-8"))
    assert index["event_log_head"] == captured["swap_head"], (
        "the mirror index was stamped with a head OTHER than the one "
        "import_event_log actually swapped in -- a later sync will compare "
        "against a commit that does not match what the mirror holds"
    )


def test_resume_after_crash_unions_journaled_and_fresh_old_ids(tmp_path: Path) -> None:
    """Issue #358 repair v3 (major, verifier-confirmed): on resume,
    ``journaled_old_ids`` REPLACED the journal's set instead of UNIONING it
    with the fresh pre-compaction read (``affected`` unions correctly one
    line below -- the asymmetry is the bug). A writer that appends a NEW
    event during the crash window (after the journal is durably written,
    before the swap lands) is invisible to the journal by construction; a
    ROUTINE, reclaim-unrelated ``sync_events_mirror`` tick mirrors it into
    its own batch file before resume runs. On resume, ``_refinalize``
    re-chains the WHOLE stream from the first touched slot onward, so the
    new event gets a REWRITTEN id/sequence too -- but its pre-compaction
    batch file is not a subset of ``journaled_old_ids`` alone, so
    ``_reconcile_mirror_for_project`` never removes it: the mirror ends up
    carrying BOTH the superseded (original-id) and the rewritten (new-id)
    copy of the same logical event forever."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod
    from opentraces.core.bucket_events import read_events_mirror_batches, sync_events_mirror

    world = _build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated kill after journal write, before swap")

    kill_patch = pytest.MonkeyPatch()
    kill_patch.setattr(reclaim_mod, "import_event_log", _boom)
    with pytest.raises(RuntimeError, match="simulated kill after journal write, before swap"):
        reclaim_mod._process_reachable_project(slug, repo, apply=True)
    kill_patch.undo()
    assert reclaim_mod._journal_path(slug).exists(), "journal must persist after the kill"

    # A real, reclaim-unrelated writer lands a NEW event and a routine
    # mirror sync ticks BEFORE resume runs -- matching the module's own
    # named concurrent writers, not a reclaim internal.
    race_patch_id = "9" * 64
    append_event_batch(
        repo,
        [
            _patch_created(
                trace_id="t-race3", trace_patch_id=f"tracepatch-sha256:{race_patch_id}",
                file_path="race3.py", step_index=0,
            )
        ],
        writer="watcher",
    )
    sync_events_mirror(repo, repo_id=slug)

    proj = reclaim_mod._process_reachable_project(slug, repo, apply=True)
    assert proj.action == "compacted"
    assert not reclaim_mod._journal_path(slug).exists()

    live_events = read_events(repo, verify=False)
    canonical_race = [
        e for e in live_events if e.trace_id == "t-race3" and e.event_type == "trace_patch_created"
    ]
    assert len(canonical_race) == 1

    mirror_events = list(read_events_mirror_batches())
    mirror_race = [
        e for e in mirror_events if e.trace_id == "t-race3" and e.event_type == "trace_patch_created"
    ]
    assert len(mirror_race) == 1, (
        f"the concurrently-appended event survives as {len(mirror_race)} distinct "
        "copies in the mirror -- its superseded pre-compaction batch file was "
        "never removed because journaled_old_ids replaced, rather than unioned "
        "with, the fresh pre-compaction read"
    )
    assert mirror_race[0].event_id == canonical_race[0].event_id


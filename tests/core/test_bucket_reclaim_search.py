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
from opentraces.core.trails.ids import git_anchor_ref, trace_patch_ref

# How many padding-only "unknown" results `_build_world`'s v2-fat summary
# carries on top of its six real patches -- see the docstring on
# `_build_world` for why this exists (issue #358 repair v3 round 2
# follow-up). Sized so the fixture's total canonical-events tree clears
# `_FAT_PREFILTER_MIN_BYTES` (65536) with comfortable margin.
_FAT_PADDING_ENTRY_COUNT = 450


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
    six real patches plus ``_FAT_PADDING_ENTRY_COUNT`` padding-only unknown
    results, ALL unknown -- loses search-event touch entirely once
    compacted). Registers the project, syncs the mirror, and projects both
    traces' companions via ``bucket_repair`` so the "before" state is exactly
    what a real affected machine would have on disk.

    The padding entries exist so this fixture clears ``_FAT_PREFILTER_MIN_
    BYTES`` (issue #358 repair v3 round 2 follow-up): a real v2-fat summary
    is large because of accumulated RESULT COUNT (the module docstring's
    26 GB driver), not large individual fields, so padding the same way here
    is what the prefilter is actually supposed to see as "fat" -- six real
    patches alone (~14.5 KB total tree) cleared the OLD, useless 64-byte
    threshold by accident but would silently look "clean" under any
    defensible one. Padding entries carry no backing ``trace_patch_created``
    event -- unnecessary, since ``_search_touch_ids``/``summary_search_
    touches_trace`` both resolve a v2-fat entry's trace via its own inline
    ``trace_id`` field, never a patch lookup (that lookup only exists for
    the already-compacted v3 ``unanchored_trace_patch_ids`` shape)."""

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
    # Padding-only entries (no backing `trace_patch_created` event -- see
    # the docstring above) purely to push this summary's own byte size
    # comfortably past `_FAT_PREFILTER_MIN_BYTES`, the way a real fat v2
    # summary's size actually accumulates.
    fat_entries += [
        {
            "trace_patch_id": f"tracepatch-sha256:pad{i:04d}{'b' * 57}", "trace_id": "t-fat-unanchored",
            "step_index": i, "generation_index": 0, "result": "unknown", "created_anchor_ids": [],
        }
        for i in range(_FAT_PADDING_ENTRY_COUNT)
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


def _git_anchor_created(*, trace_id, step_index, patch_id, commit_hex):
    """A ``git_anchor_created`` draft shaped like ``anchors.py``'s own
    (``git show 8b71cc7f1ea:src/opentraces/core/trails/anchors.py``) --
    used only by ``_build_world_with_interleaved_legacy_batch`` to
    reproduce the real pre-plan-090 writer's batch order."""
    trace_patch_id = f"tracepatch-sha256:{patch_id}"
    return TrailEventDraft(
        event_type="git_anchor_created",
        trace_id=trace_id,
        generation_index=0,
        step_index=step_index,
        capture_method=["watcher_reconcile"],
        payload={
            "git_anchor_id": f"gitanchor-sha256:{patch_id}",
            "git_anchor_ref": git_anchor_ref(f"gitanchor-sha256:{patch_id}"),
            "trace_patch_id": trace_patch_id,
            "trace_patch_ref": trace_patch_ref(trace_patch_id),
            "commit_id": {"algo": "sha1", "hex": commit_hex},
            "path": "a.py",
            "range": {"start_line": 1, "end_line": 1},
            "patch_id": patch_id,
            "observed_ref": commit_hex,
            "relation": "anchored_in_git",
            "evidence_tier": "exact_range_hash",
            "evidence_firmness": "firm",
            "source": "watcher",
            "limitations": [],
        },
    )


def _build_world_with_interleaved_legacy_batch(tmp_path: Path) -> dict:
    """Like ``_build_world``, but ``t-anchored``'s legacy group is built in
    the REAL pre-plan-090 writer's order (issue #358 repair round 3,
    blocker): a ``git_anchor_search_completed`` draft for a patch, then --
    only when it anchored -- a ``git_anchor_created`` draft for that SAME
    patch right after it, all in ONE reconcile-run batch (mirrors
    ``_seed_legacy_interleaved`` in ``test_search_compaction.py`` at the
    reclaim end-to-end level instead of the compaction-unit level).
    ``_build_world`` itself never interleaves -- its legacy batch is a pure
    run of search events -- and every other test in this module reads
    exact byte/count assertions against that shared fixture, so this is a
    separate, minimal world rather than a change to it.

    24 patches (not 3) -- purely to clear ``_FAT_PREFILTER_MIN_BYTES``
    (65536): a legacy per-patch batch accumulates size one small EVENT at a
    time (unlike ``_build_world``'s v2-fat summary, which pads one big
    ``results[]``), and 24 interleaved patches' worth of canonical history
    measures ~90KB, comfortably above the threshold -- 3 patches alone
    (~12KB) would hit the SAME "below fat-detection size prefilter" early
    return every other prefilter-conscious fixture in this module already
    has to work around."""

    from opentraces.core.bucket_store import bucket_repair, write_trace_record
    from opentraces.core.config import get_project_dir, load_config, register_project, save_config

    repo = tmp_path / "proj"
    _init_repo(repo)

    commit_a = "a" * 40
    patch_ids = [f"{i:02d}{'a' * 62}" for i in range(24)]
    trace_patch_ids = [f"tracepatch-sha256:{pid}" for pid in patch_ids]
    append_event_batch(
        repo,
        [
            _patch_created(trace_id="t-anchored", trace_patch_id=tpid, file_path=f"a{i}.py", step_index=i)
            for i, tpid in enumerate(trace_patch_ids)
        ],
        writer="capture-claude-code",
    )
    drafts: list[TrailEventDraft] = []
    for i, patch_id in enumerate(patch_ids):
        anchored = i % 2 == 0
        drafts.append(
            _legacy_search(
                trace_id="t-anchored", step_index=i, patch_id=patch_id, commit_hex=commit_a,
                result="anchored" if anchored else "unknown",
                anchor_ids=[f"gitanchor-sha256:{patch_id}"] if anchored else [],
            )
        )
        if anchored:
            drafts.append(
                _git_anchor_created(trace_id="t-anchored", step_index=i, patch_id=patch_id, commit_hex=commit_a)
            )
    append_event_batch(repo, drafts, writer="watcher")

    cfg = load_config()
    register_project(cfg, repo)
    save_config(cfg)
    slug = get_project_dir(repo).name
    write_trace_record(_record("t-anchored"), project_slug=slug, source_layer="canonical")
    bucket_repair(dry_run=False)

    return {"repo": repo, "slug": slug, "trace_patch_ids": trace_patch_ids}


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

    # Issue #358 streaming rewrite: the CAS swap moved from `import_event_
    # log` (list-based, materialized `compacted`) to `_swap_candidate_ref`
    # (streams the ALREADY-built candidate commit's own tree/commit
    # objects onto the ref) -- same seam ROLE (the one call that lands the
    # ref update), same generic boom-on-any-args wrapper, same "kill right
    # before the swap lands" trigger point.
    patch = pytest.MonkeyPatch()
    patch.setattr(reclaim_mod, "_swap_candidate_ref", _boom)
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

    real_stream_compact_chain = reclaim_mod._stream_compact_chain
    concurrent_patch_id = "9" + "c" * 63
    commit_c = "c" * 40

    def _compact_then_concurrent_append(repo_arg, head_arg, patch_to_trace_arg):
        # Issue #358 streaming rewrite: the O(corpus) compaction work moved
        # from `compact_search_events(old_events)` to `_stream_compact_
        # chain(repo, head, patch_to_trace)` -- the ONE place a fresh (non-
        # retry) compaction pass runs, called exactly once per swap attempt
        # (the CAS-retry fold uses a DIFFERENT function, `_stream_compact_
        # delta`, deliberately not wrapped here -- see that function's own
        # docstring), so this is the same seam ROLE: run the real,
        # (real, minutes-long on the motivating 27GB bucket) compaction to
        # completion, THEN let a concurrent writer land, THEN return.
        result = real_stream_compact_chain(repo_arg, head_arg, patch_to_trace_arg)
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
    patch.setattr(reclaim_mod, "_stream_compact_chain", _compact_then_concurrent_append)
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

    # Issue #358 streaming rewrite: the CAS swap moved from `import_event_
    # log(cwd, events, *, writer, force, expected_head=None)` to `_swap_
    # candidate_ref(repo, *, commit_sha, expected_head)` -- same seam ROLE
    # (the one call that lands the ref update, raising on a lost race),
    # signature updated to match the new function it replaces.
    def _always_races(repo_arg, *, commit_sha, expected_head=None):
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
    patch.setattr(reclaim_mod, "_swap_candidate_ref", _always_races)
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

    world = _build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]

    real_swap_candidate_ref = reclaim_mod._swap_candidate_ref
    captured: dict[str, str] = {}
    raced = {"done": False}

    # Issue #358 streaming rewrite: the CAS swap moved from `import_event_
    # log` to `_swap_candidate_ref` -- same seam ROLE (the one call that
    # lands the ref update and returns the head it actually landed), same
    # "run the real swap, capture its OWN reported head, THEN let a
    # concurrent writer land" trigger point. The property under test (the
    # mirror index gets stamped with the swap's OWN returned head, never a
    # fresh re-read taken after) is now true by construction in the new
    # implementation -- `_swap_candidate_ref` returns the commit_sha it was
    # GIVEN, never re-derives via `_head_sha` after landing the update --
    # this still exercises it end to end rather than asserting it in the
    # abstract.
    def _swap_then_race(*args, **kwargs):
        result = real_swap_candidate_ref(*args, **kwargs)
        captured["swap_head"] = result["head"]
        if not raced["done"]:
            raced["done"] = True
            # A genuinely new writer lands right after the ref moves.
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
    patch.setattr(reclaim_mod, "_swap_candidate_ref", _swap_then_race)
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

    # Issue #358 streaming rewrite: `import_event_log` -> `_swap_candidate_
    # ref` (see the module docstring's crash-safety section); same seam
    # role, same generic boom.
    kill_patch = pytest.MonkeyPatch()
    kill_patch.setattr(reclaim_mod, "_swap_candidate_ref", _boom)
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


# ---------------------------------------------------------------------------
# Issue #358 repair v3 round 2 -- adversarial re-review of the round-1 v3
# repair above found two more blockers and one more major, all IN that
# repair's own fixes -- verifier-confirmed against real repros.
# ---------------------------------------------------------------------------


def test_ref_lost_mid_snapshot_bracket_raises_without_wiping_mirror(tmp_path: Path) -> None:
    """Issue #358 repair v3 round 2 (blocker, verifier-confirmed):
    ``_read_events_snapshot`` brackets ``read_events`` with a ``_head_sha``
    check taken right after it, but used to treat a confirmed head of
    ``None`` (the ref vanishing DURING that bracket -- a re-clone or damaged
    repo mid-run, the exact hazard ``_process_reachable_project``'s own
    entry guard exists for one call stack frame higher) the same as any
    other stale-head mismatch: loop with ``head = None`` and, on the next
    iteration, silently return ``(None, [])``. The caller's ``if head is not
    None`` swap gate then skips the CAS entirely, derives an EMPTY
    compaction target, and -- with a pending journal, exactly the scenario
    that entry guard already refuses -- hands ``_reconcile_mirror_for_
    project`` a removal scope covering the WHOLE mirror (its sole surviving
    copy once the ref is gone), reporting ``action=compacted`` with zero
    errors. This must refuse instead, leaving the mirror and companions
    untouched and the journal in place for a future recovery -- the same
    outcome the entry guard produces for the same underlying loss, just
    noticed one read later."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod
    from opentraces.core.bucket_events import read_events_mirror_batches

    world = _build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated kill before ref swap")

    # Issue #358 streaming rewrite: `import_event_log` -> `_swap_candidate_
    # ref` (same seam role, same generic boom).
    kill_patch = pytest.MonkeyPatch()
    kill_patch.setattr(reclaim_mod, "_swap_candidate_ref", _boom)
    with pytest.raises(RuntimeError, match="simulated kill before ref swap"):
        reclaim_mod._process_reachable_project(slug, repo, apply=True)
    kill_patch.undo()
    assert reclaim_mod._journal_path(slug).exists(), "journal must persist after the kill"

    mirror_before = list(read_events_mirror_batches())
    assert mirror_before
    anchored_before = _read_trail_events(slug, "t-anchored")
    assert anchored_before

    # Resume: the ref is still present when this run's OWN entry guard reads
    # it (`head = _head_sha(repo)`), so that guard does not fire -- the loss
    # instead happens DURING the streaming compaction read, right after it
    # finishes, matching the finding's own repro (re-clone/damage racing the
    # O(corpus) read window). Issue #358 streaming rewrite: the read moved
    # from `read_events` (a materialized list) to `event_log.iter_events`
    # (a generator, imported into this module's namespace as `iter_events`)
    # -- the wrapper eagerly drains it into a list FIRST (so the ref
    # deletion below still lands strictly after the read completes, same
    # trigger point as before) and returns an iterator over that list, since
    # merely calling a generator function does nothing until consumed.
    real_iter_events = reclaim_mod.iter_events
    lost = {"done": False}

    def _read_then_lose_ref(r, *args, **kwargs):
        result = list(real_iter_events(r, *args, **kwargs))
        if not lost["done"]:
            lost["done"] = True
            subprocess.run(
                ["git", "update-ref", "-d", "refs/opentraces/local/events/v1"],
                cwd=repo, check=True,
            )
        return iter(result)

    ref_patch = pytest.MonkeyPatch()
    ref_patch.setattr(reclaim_mod, "iter_events", _read_then_lose_ref)
    try:
        with pytest.raises(RuntimeError, match="disappeared"):
            reclaim_mod._process_reachable_project(slug, repo, apply=True)
    finally:
        ref_patch.undo()

    assert reclaim_mod._journal_path(slug).exists(), "journal must survive the refusal"

    mirror_after = list(read_events_mirror_batches())
    assert {e.event_id for e in mirror_after} == {e.event_id for e in mirror_before}
    assert _read_trail_events(slug, "t-anchored") == anchored_before


def test_swap_retry_delta_original_id_is_removed_from_mirror(tmp_path: Path) -> None:
    """Issue #358 repair v3 round 2 (major, verifier-confirmed):
    ``_swap_compacted_chain``'s CAS-retry path folds a concurrent append via
    ``compact_and_append``, which re-chains it (a fresh ``event_id`` for
    every slot, from the new ``previous_event_id`` link) -- but the delta
    event's ORIGINAL id was discarded, never entering ``journaled_old_ids``
    or the mirror removal scope (both are pre-append snapshots). A routine,
    reclaim-unrelated ``sync_events_mirror`` tick that mirrors the append
    under its original id BEFORE this swap lands leaves that batch file a
    subset of neither the pre-compaction nor the post-compaction id set, so
    ``_reconcile_mirror_for_project`` never removes it: the mirror ends up
    carrying BOTH the superseded (original-id) and the rewritten (new-id)
    copy of the same logical event forever -- broken replay contiguity, the
    exact 'no reader can collapse this' leftover the module docstring warns
    about."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod
    from opentraces.core.bucket_events import read_events_mirror_batches, sync_events_mirror

    world = _build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]

    real_stream_compact_chain = reclaim_mod._stream_compact_chain
    concurrent_patch_id = "9" + "f" * 63
    commit_f = "f" * 40
    fired = {"done": False}
    captured: dict[str, set[str]] = {}

    # Issue #358 streaming rewrite: `compact_search_events` -> `_stream_
    # compact_chain` -- the ONE place a fresh (non-retry) compaction pass
    # runs (see `test_concurrent_append_during_compaction_survives_via_cas_
    # retry`'s retarget comment for why this, not `stream_compact_events`
    # itself, is the right seam).
    def _compact_then_mirrored_concurrent_append(repo_arg, head_arg, patch_to_trace_arg):
        result = real_stream_compact_chain(repo_arg, head_arg, patch_to_trace_arg)
        if not fired["done"]:
            fired["done"] = True
            # A hot post-commit hook reconcile creates the patch AND
            # immediately searches it -- the module docstring's own named
            # concurrent writer -- WHILE this (real, minutes-long on the
            # motivating 27GB bucket) compaction call is still running.
            append_event_batch(
                repo,
                [
                    _patch_created(
                        trace_id="t-swap-race", trace_patch_id=f"tracepatch-sha256:{concurrent_patch_id}",
                        file_path="swaprace.py", step_index=0,
                    ),
                    _legacy_search(
                        trace_id="t-swap-race", step_index=0, patch_id=concurrent_patch_id,
                        commit_hex=commit_f, result="anchored",
                        anchor_ids=["gitanchor-sha256:" + "f" * 64],
                    ),
                ],
                writer="watcher",
            )
            # A ROUTINE, reclaim-unrelated mirror sync tick mirrors the
            # append under its OWN pre-fold ids -- before this pass's swap
            # ever attempts to land, exactly as a real independent watcher
            # or hook process would between the append above and this
            # module's own swap below.
            sync_events_mirror(repo, repo_id=slug)
            captured["original_ids"] = {
                e.event_id for e in read_events(repo, verify=False) if e.trace_id == "t-swap-race"
            }
        return result

    patch = pytest.MonkeyPatch()
    patch.setattr(reclaim_mod, "_stream_compact_chain", _compact_then_mirrored_concurrent_append)
    try:
        proj = reclaim_mod._process_reachable_project(slug, repo, apply=True)
    finally:
        patch.undo()

    assert proj.action == "compacted"
    assert proj.swap_retries == 1
    assert len(captured.get("original_ids", set())) == 2

    live_events = read_events(repo, verify=False)
    live_ids = {e.event_id for e in live_events}
    verdict = verify_event_log(repo)
    assert verdict["event_chain_valid"] is True
    assert verdict["content_hashes_valid"] is True

    mirror_events = list(read_events_mirror_batches())
    mirror_ids = [e.event_id for e in mirror_events]
    assert len(mirror_ids) == len(set(mirror_ids)), "mirror carries duplicate event_ids"
    assert set(mirror_ids) == live_ids, (
        "mirror must exactly match the live canonical chain -- a CAS-retry "
        "delta event's pre-fold original id must not survive alongside its "
        "rewritten copy"
    )
    assert not (captured["original_ids"] & set(mirror_ids)), (
        "the concurrent append's ORIGINAL (pre-fold) ids are still present "
        "in the mirror alongside their rewritten copies"
    )


def test_dry_run_previews_ref_rewritten_when_apply_will_rewrite_it(tmp_path: Path) -> None:
    """Issue #358 repair v3 round 2 (major, verifier-confirmed): dry-run's
    ``ref_rewritten`` stayed at its ``False`` default even though the exact
    predicate apply checks before landing the swap (``ref_would_change``) is
    already known at preview time -- the same dry-run/apply-parity defect
    class this repair series already fixed for the mirror event counts (see
    ``test_dry_run_and_apply_report_identical_mirror_event_counts``), left
    open for the single most sensitive mutation this pass performs: a
    cautious operator auditing a dry-run report would conclude the canonical
    git ref will not be rewritten, and be wrong."""
    from opentraces.core.bucket_reclaim_search import reclaim_anchor_search

    world = _build_world(tmp_path)
    slug = world["slug"]

    dry = reclaim_anchor_search(apply=False)
    proj_dry = next(p for p in dry.projects if p.project_slug == slug)
    assert proj_dry.action == "compacted"
    assert proj_dry.ref_rewritten is True

    applied = reclaim_anchor_search(apply=True)
    proj_apply = next(p for p in applied.projects if p.project_slug == slug)
    assert proj_apply.ref_rewritten is True


# ---------------------------------------------------------------------------
# Issue #358 repair v3 round 2 follow-up: adversarial re-review of the
# round-2 CAS-retry delta-id fix (above) found the fix itself has an
# unpersisted gap, plus a prefilter that never fires and CLI-level coverage
# no test above provides.
# ---------------------------------------------------------------------------


def test_kill_after_swap_before_reconcile_journals_cas_retry_delta_ids(tmp_path: Path) -> None:
    """Issue #358 repair v3 round 2 follow-up (blocker): ``_swap_compacted_
    chain``'s CAS-retry fold widens the mirror removal scope with a
    concurrent append's pre-fold ``delta_original_ids`` -- but the caller
    (``_process_reachable_project``) only unions that into the IN-PROCESS-
    MEMORY ``journaled_old_ids`` variable. The durable journal was written
    BEFORE the swap (gated on ``journal is None``, from the pre-swap belief)
    and is never re-written after the swap widens the decision. A kill
    between the swap landing and ``_reconcile_mirror_for_project`` therefore
    resumes from a journal that never knew about the delta: on resume,
    ``journaled_old_ids = journal(pre-append) | old_ids(fresh, already
    re-chained)`` -- the delta's pre-fold ids, mirrored under a ROUTINE
    ``sync_events_mirror`` tick before the swap landed, are in NEITHER set,
    so that batch file is never a subset of anything and survives forever
    alongside the rewritten copy. Fix: re-write the journal (delta ids
    unioned in, affected set updated) right after the swap returns and
    before the reconcile call that can be killed."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod
    from opentraces.core.bucket_events import read_events_mirror_batches, sync_events_mirror

    world = _build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]

    real_stream_compact_chain = reclaim_mod._stream_compact_chain
    concurrent_patch_id = "9" + "9" * 63
    commit_g = "9" * 40
    fired = {"done": False}
    captured: dict[str, set[str]] = {}

    # Issue #358 streaming rewrite: `compact_search_events` -> `_stream_
    # compact_chain` (see the earlier CAS-retry tests' retarget comments).
    def _compact_then_mirrored_concurrent_append(repo_arg, head_arg, patch_to_trace_arg):
        result = real_stream_compact_chain(repo_arg, head_arg, patch_to_trace_arg)
        if not fired["done"]:
            fired["done"] = True
            # A hot post-commit hook reconcile creates the patch AND
            # immediately searches it -- the module docstring's own named
            # concurrent writer -- WHILE this (real, minutes-long on the
            # motivating 27GB bucket) compaction call is still running.
            append_event_batch(
                repo,
                [
                    _patch_created(
                        trace_id="t-kill-race", trace_patch_id=f"tracepatch-sha256:{concurrent_patch_id}",
                        file_path="killrace.py", step_index=0,
                    ),
                    _legacy_search(
                        trace_id="t-kill-race", step_index=0, patch_id=concurrent_patch_id,
                        commit_hex=commit_g, result="anchored",
                        anchor_ids=["gitanchor-sha256:" + "9" * 64],
                    ),
                ],
                writer="watcher",
            )
            # A ROUTINE, reclaim-unrelated mirror sync tick mirrors the
            # append under its OWN pre-fold ids -- before this pass's swap
            # ever attempts to land, exactly as a real independent watcher
            # or hook process would between the append above and this
            # module's own swap below.
            sync_events_mirror(repo, repo_id=slug)
            captured["original_ids"] = {
                e.event_id for e in read_events(repo, verify=False) if e.trace_id == "t-kill-race"
            }
        return result

    def _boom_reconcile(*args, **kwargs):
        raise RuntimeError("simulated kill after swap, before reconcile")

    patch = pytest.MonkeyPatch()
    patch.setattr(reclaim_mod, "_stream_compact_chain", _compact_then_mirrored_concurrent_append)
    patch.setattr(reclaim_mod, "_reconcile_mirror_for_project", _boom_reconcile)
    with pytest.raises(RuntimeError, match="simulated kill after swap, before reconcile"):
        reclaim_mod._process_reachable_project(slug, repo, apply=True)
    patch.undo()

    assert len(captured.get("original_ids", set())) == 2, "repro invalid -- the concurrent append never mirrored"
    assert reclaim_mod._journal_path(slug).exists(), "journal must persist after the kill"

    # The load-bearing assertion: the journal ON DISK must already carry the
    # CAS-retry delta's pre-fold ids -- not just this run's now-lost
    # in-memory belief -- since the swap already landed before the kill.
    journal = json.loads(reclaim_mod._journal_path(slug).read_text(encoding="utf-8"))
    journaled_ids = set(journal["old_event_ids"])
    assert captured["original_ids"] <= journaled_ids, (
        "journal on disk was not re-written after the CAS-retry swap widened "
        "the removal decision -- a resume starting from this journal cannot "
        "see the delta's pre-fold ids"
    )

    # Resume (no monkeypatch): finishes the interrupted reconcile step.
    result = reclaim_mod.reclaim_anchor_search(apply=True)
    proj = next(p for p in result.projects if p.project_slug == slug)
    assert proj.action == "compacted"
    assert proj.mirror_rewritten is True
    assert not reclaim_mod._journal_path(slug).exists()

    live_events = read_events(repo, verify=False)
    live_ids = {e.event_id for e in live_events}
    verdict = verify_event_log(repo)
    assert verdict["event_chain_valid"] is True
    assert verdict["content_hashes_valid"] is True

    mirror_events = list(read_events_mirror_batches())
    mirror_ids = [e.event_id for e in mirror_events]
    assert len(mirror_ids) == len(set(mirror_ids)), "mirror carries duplicate event_ids"
    assert set(mirror_ids) == live_ids, (
        "mirror must exactly match the live canonical chain after resume -- "
        "the CAS-retry delta's pre-fold original id must not survive "
        "alongside its rewritten copy"
    )
    assert not (captured["original_ids"] & set(mirror_ids)), (
        "the concurrent append's ORIGINAL (pre-fold) ids are still present "
        "in the mirror alongside their rewritten copies"
    )

    # No orphan pre-fold batch file left on disk (raw, undeduped read -- the
    # concrete, permanent leak the finding describes).
    raw_ids_after = _raw_mirror_event_ids()
    assert len(raw_ids_after) == len(set(raw_ids_after)), "orphan pre-fold batch file survives on disk"


def test_prefilter_skips_expensive_path_for_slim_history(tmp_path: Path) -> None:
    """Issue #358 repair v3 round 2 follow-up (major): ``_FAT_PREFILTER_MIN_
    BYTES`` at 64 raw bytes means EVERY project with any history at all fails
    the prefilter -- a single ordinary event's JSON blob already exceeds 64
    bytes -- so a healthy or already-compacted bucket never takes the fast
    no-op path this prefilter exists for; every ``bucket reclaim`` run pays
    the full O(corpus) read + compact even for a project with zero fat
    anchor-search history. A chain of many slim, ordinary events must skip
    ``_stream_compact_chain`` entirely once the threshold is raised to a
    defensible size."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod
    from opentraces.core.config import get_project_dir, load_config, register_project, save_config

    repo = tmp_path / "proj-slim"
    _init_repo(repo)
    for i in range(30):
        append_event_batch(
            repo,
            [
                _patch_created(
                    trace_id=f"t-{i}", trace_patch_id=f"tracepatch-sha256:{i:02d}{'9' * 62}",
                    file_path=f"f{i}.py", step_index=0,
                )
            ],
            writer="capture-claude-code",
        )

    head = reclaim_mod._head_sha(repo)
    tree_bytes = reclaim_mod._events_tree_bytes(repo, head)
    assert tree_bytes < reclaim_mod._FAT_PREFILTER_MIN_BYTES, (
        f"fixture not slim enough for this test's premise ({tree_bytes} bytes >= "
        f"{reclaim_mod._FAT_PREFILTER_MIN_BYTES})"
    )

    cfg = load_config()
    register_project(cfg, repo)
    save_config(cfg)
    slug = get_project_dir(repo).name

    calls = {"n": 0}
    # Issue #358 streaming rewrite: `_read_events_snapshot` -> `_stream_
    # compact_chain` -- the expensive O(corpus) read+compact entry point
    # the prefilter exists to skip entirely moved here (the streaming
    # implementation no longer has a separate "just read and snapshot the
    # head" step distinct from the compaction pass itself -- see the module
    # docstring for why `event_log.iter_events`'s explicit-sha reads make
    # the old bracket-and-retry shape unnecessary).
    real_stream_compact_chain = reclaim_mod._stream_compact_chain

    def _counting_stream_compact_chain(*args, **kwargs):
        calls["n"] += 1
        return real_stream_compact_chain(*args, **kwargs)

    patch = pytest.MonkeyPatch()
    patch.setattr(reclaim_mod, "_stream_compact_chain", _counting_stream_compact_chain)
    try:
        report = reclaim_mod._process_reachable_project(slug, repo, apply=False)
    finally:
        patch.undo()

    assert calls["n"] == 0, "the expensive read+compact path ran despite a slim, sub-threshold history"
    assert report.action == "clean"
    assert report.reason == "below fat-detection size prefilter"


def test_cli_bucket_reclaim_json_dry_run_then_apply_matches_direct_call(tmp_path: Path) -> None:
    """Issue #358 repair v3 round 2 follow-up (CLI-level coverage gap): every
    test above drives ``reclaim_anchor_search()`` directly; the only ``bucket
    reclaim --json`` ``CliRunner`` coverage (the L1 agent-contract lint) runs
    on an EMPTY home, so nothing proves the CLI actually surfaces the
    anchor-search section's fields on a real, populated bucket, or that its
    dry-run preview matches what ``--apply`` actually does."""
    import json as _json

    from click.testing import CliRunner

    from opentraces.cli import main

    world = _build_world(tmp_path)
    repo, slug = world["repo"], world["slug"]
    runner = CliRunner()

    # Issue #358: reclaim now emits a one-line-per-project progress notice
    # to STDERR (never stdout, so `--json` stdout stays pure JSON) -- Click
    # 8.2+'s `.output` is explicitly documented as a MIX of stdout+stderr
    # "as the user would see it in a terminal" (`.stdout` is the stdout-only
    # stream); asserting JSON-parseability against the stdout-only stream is
    # what actually tests "the JSON payload is clean", now that there is
    # real stderr chatter to mix in.
    # Issue #362: the anchor-search pass ships EXPERIMENTAL and opt-in behind
    # ``--anchor-search`` (default off, because it is O(corpus) and slow on
    # large real buckets); a plain ``bucket reclaim`` runs only the cruft pass
    # and reports the anchor-search section as skipped. This CLI test exercises
    # the pass, so it must pass the flag.
    dry = runner.invoke(
        main, ["bucket", "reclaim", "--repo", str(repo), "--anchor-search", "--json"]
    )
    assert dry.exit_code == 0, dry.output
    dry_proj = next(
        p for p in _json.loads(dry.stdout)["reclaim"]["anchor_search"]["projects"]
        if p["project_slug"] == slug
    )
    assert dry_proj["action"] == "compacted"
    assert dry_proj["events_before"] > dry_proj["events_after"]
    assert dry_proj["ref_rewritten"] is True
    assert dry_proj["bytes_before"] > dry_proj["bytes_after"]

    # Default (no flag): anchor-search is skipped, cruft pass still runs clean.
    off = runner.invoke(main, ["bucket", "reclaim", "--repo", str(repo), "--json"])
    assert off.exit_code == 0, off.output
    assert _json.loads(off.stdout)["reclaim"]["anchor_search"] == {
        "skipped": True,
        "reason": "experimental; pass --anchor-search to run (issue #358/#362)",
    }

    applied = runner.invoke(
        main, ["bucket", "reclaim", "--repo", str(repo), "--anchor-search", "--apply", "--json"]
    )
    assert applied.exit_code == 0, applied.output
    applied_proj = next(
        p for p in _json.loads(applied.stdout)["reclaim"]["anchor_search"]["projects"]
        if p["project_slug"] == slug
    )
    assert applied_proj["action"] == "compacted"

    # Dry-run's preview must match what --apply actually did on this
    # quiescent world -- the same parity contract the direct-call tests
    # above already prove for `reclaim_anchor_search()` itself.
    for field_name in ("action", "events_before", "events_after", "ref_rewritten", "bytes_before"):
        assert dry_proj[field_name] == applied_proj[field_name], field_name


# ---------------------------------------------------------------------------
# Issue #358 repair v3 round 2 follow-up, second pass (team-lead ruling on
# the two items flagged in the first follow-up round).
#
# The reachable path's ``_clear_journal``-before-companion-loop bug does NOT
# have an exact twin here -- adversarially testing the team lead's predicted
# repro (kill mid companion loop, resume, expect stale companions to be
# permanently missed) disproved it: ``_search_touch_ids`` is specifically
# designed (see its own docstring) to re-resolve an already-compacted
# ``unanchored_trace_patch_ids`` entry back to its trace via the SAME
# ``trace_patch_created`` events the ORIGINAL (non-compacted) read would
# have used, so a FRESH recompute on an already-compact mirror still finds
# the trace, and ``_companion_deltas`` still correctly flags the on-disk
# company stale -- with no byte-size prefilter on this path (unlike the
# reachable path) to short-circuit before that check ever runs. Verified
# directly: `companions_stale=True` on a fresh, journal-less recompute over
# an already-compacted mirror for this exact fixture.
#
# The reorder is still applied below, for a real (if narrower) reason: it
# lets the CHEAP, SCOPED ``resume_pending_anchor_search_journals()`` --
# which ``bucket_repair`` runs automatically before its own per-project sync
# specifically so nobody has to remember to re-run ``bucket reclaim
# --apply`` -- heal this exact crash window too. Pre-fix, that scoped path
# finds nothing here (no journal survives the kill, so `_pending_journal_
# slugs()` never lists this project); a full, bucket-wide `bucket reclaim
# --apply` re-run was the only thing that happened to still work, only
# because of the mechanism above. Post-fix, the journal survives the crash
# and the scoped healing path picks it up directly, matching the guarantee
# the reachable path already has.
# ---------------------------------------------------------------------------


def test_unreachable_kill_mid_companion_regen_leaves_journal_for_scoped_resume(tmp_path: Path) -> None:
    """Issue #358 repair v3 round 2 follow-up, second pass: a kill inside
    the mirror-only path's companion-regen loop must leave the journal in
    place (not already cleared before the loop even started), so the
    lightweight, project-scoped ``resume_pending_anchor_search_journals()``
    -- not just a full bucket-wide ``bucket reclaim --apply`` re-run -- can
    heal it. Before the fix this loop's kill point runs strictly after
    ``_clear_journal``, so no journal survives for the scoped path to find;
    after the fix, the journal survives until the loop (and therefore all
    companion regeneration) actually finishes."""
    from opentraces.core import bucket_reclaim_search as reclaim_mod
    from opentraces.core.bucket_store import bucket_repair

    world = _build_unreachable_world(tmp_path)
    slug = world["slug"]

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated kill mid unreachable companion regen")

    kill_patch = pytest.MonkeyPatch()
    kill_patch.setattr(reclaim_mod, "project_per_trace_exports", _boom)
    with pytest.raises(RuntimeError, match="simulated kill mid unreachable companion regen"):
        reclaim_mod._process_unreachable_project(slug, apply=True, single_project_bucket=True)
    kill_patch.undo()

    # The load-bearing assertion: the journal must SURVIVE this crash point
    # so the scoped resume below can find it via `_pending_journal_slugs()`.
    assert reclaim_mod._journal_path(slug).exists(), (
        "journal was already cleared before the companion loop finished -- "
        "the scoped resume_pending_anchor_search_journals() path has "
        "nothing to find for this project"
    )
    fat_trail_mid = _read_trail_events(slug, "t-fat-unanchored")
    assert _search_events(fat_trail_mid), "repro invalid -- fat companion already regenerated before the kill"
    anchored_trail_mid = _read_trail_events(slug, "t-anchored")
    assert len(_search_events(anchored_trail_mid)) == 2, "repro invalid -- anchored companion already regenerated"

    # Heal via the SCOPED path only -- proves the journal alone is enough,
    # without a full bucket-wide reclaim_anchor_search() rescan.
    healed = reclaim_mod.resume_pending_anchor_search_journals()
    assert [r.project_slug for r in healed] == [slug]
    assert healed[0].action == "mirror_only_compacted"
    assert "t-fat-unanchored" in healed[0].companions_regenerated
    assert "t-anchored" in healed[0].companions_regenerated
    assert not reclaim_mod._journal_path(slug).exists()

    fat_trail_after = _read_trail_events(slug, "t-fat-unanchored")
    assert _search_events(fat_trail_after) == []
    anchored_trail_after = _read_trail_events(slug, "t-anchored")
    assert len(_search_events(anchored_trail_after)) == 1

    # The same routine path bucket_repair actually runs stays a no-op now.
    bucket_repair(dry_run=False)
    assert _read_trail_events(slug, "t-fat-unanchored") == fat_trail_after
    assert _read_trail_events(slug, "t-anchored") == anchored_trail_after

    # A full run is a true no-op too -- the scoped heal already finished
    # everything a full pass would have done.
    result = reclaim_mod.reclaim_anchor_search(apply=True)
    proj = next(p for p in result.projects if p.project_slug == slug)
    assert proj.bytes_reclaimed == 0


def test_reclaim_compacts_interleaved_legacy_batch_without_erroring(tmp_path: Path) -> None:
    """Issue #358 repair round 3, blocker.

    On a project whose legacy group interleaves a patch's own
    ``git_anchor_created`` event with the NEXT patch's search event (the
    real pre-plan-090 writer's order -- see
    ``_build_world_with_interleaved_legacy_batch``), the streaming
    compactor used to raise ``NonContiguousSearchGroupError`` and
    ``reclaim_anchor_search`` reported ``action="error"`` with ZERO bytes
    reclaimed for the WHOLE project -- on exactly the corpora this pass
    exists to compact. Compaction must succeed instead, and collapse to
    the SAME v3-compact shape the (still list-based) unreachable-project
    path already produces for this data."""
    from opentraces.core.bucket_reclaim_search import reclaim_anchor_search

    world = _build_world_with_interleaved_legacy_batch(tmp_path)
    trace_patch_ids = world["trace_patch_ids"]

    result = reclaim_anchor_search(apply=True)

    assert result.errors == []
    project = next(p for p in result.projects if p.project_slug == world["slug"])
    assert project.action == "compacted", project.reason
    assert project.legacy_events_collapsed == 24

    compacted = read_events(world["repo"], verify=False)
    summaries = [e for e in compacted if e.event_type == "git_anchor_search_completed"]
    assert len(summaries) == 1
    payload = summaries[0].payload
    anchored_ids = trace_patch_ids[0::2]
    unanchored_ids = trace_patch_ids[1::2]
    assert [r["result"] for r in payload["results"]] == ["anchored"] * len(anchored_ids)
    assert {r["trace_patch_id"] for r in payload["results"]} == set(anchored_ids)
    assert set(payload["unanchored_trace_patch_ids"]) == set(unanchored_ids)
    assert sum(1 for e in compacted if e.event_type == "git_anchor_created") == len(anchored_ids)

    # A second apply is a true no-op -- proves the compacted chain doesn't
    # keep re-triggering the same interleaved-group detection.
    second = reclaim_anchor_search(apply=True)
    second_project = next(p for p in second.projects if p.project_slug == world["slug"])
    assert second_project.action == "clean"
    assert second_project.bytes_reclaimed == 0


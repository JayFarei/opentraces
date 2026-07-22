"""Issue #116 B / #358: anchor-search compaction capability.

Pins that compacting legacy per-patch ``git_anchor_search_completed`` events
AND v2 fat summaries (mixed ``results[]``, unknown outcomes included) into
v3-compact summaries (anchored-only ``results[]`` plus the exact
``unanchored_trace_patch_ids`` of what got dropped) preserves dedup-key and
query/explain functional equivalence, round-trips through the event log
byte-identically at the event level, and leaves already-v3 events (either
variant) untouched -- the property that makes a second pass a no-op.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from opentraces.core.trails import (
    TrailEventDraft,
    append_event_batch,
    read_events,
    verify_event_log,
)
from opentraces.core.trails.anchors import ANCHOR_ALGORITHMS_PHASE5
from opentraces.core.trails.contract import ANCHOR_SEARCH_COVERAGE_SCHEMA_VERSION
from opentraces.core.trails.ids import git_anchor_ref, trace_patch_ref
from opentraces.core.trails.search_compaction import (
    CompactionStats,
    compact_repo,
    compact_search_events,
    stream_compact_events,
)
from opentraces.core.trails.search_records import (
    build_anchor_search_summary_payload,
    is_summary_search_event,
    iter_search_records,
)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


# --------------------------------------------------------------------------- #
# Raw-dict fixture helpers. Neither shape below is emitted by any live writer
# anymore (#358 repointed anchors.py, #359 repointed maturation.py) -- only
# events already on disk (and these fixtures) still carry them, so compaction
# has to be exercised against hand-built payloads rather than a real reconcile
# run.
# --------------------------------------------------------------------------- #


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
    """A raw v2 full-mixed summary: ``results[]`` carries every entry, no v3
    alternate key. One event (a reconcile-run is always exactly one such
    event under v2, unlike the legacy per-patch shape's N), so unlike
    ``_legacy_search`` this needs no grouping to become a compaction input."""
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


def _patch_created(*, trace_id: str, trace_patch_id: str, file_path: str) -> TrailEventDraft:
    return TrailEventDraft(
        event_type="trace_patch_created",
        trace_id=trace_id,
        generation_index=0,
        step_index=0,
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


def _seed_legacy(repo: Path) -> None:
    commit_a = "a" * 40
    commit_b = "b" * 40
    run1 = [
        _legacy_search(
            trace_id="t-1", step_index=i, patch_id=f"{i:02d}{'a'*62}"[:64],
            commit_hex=commit_a, result="anchored" if i % 2 == 0 else "unknown",
            anchor_ids=[f"gitanchor-sha256:{i:02d}{'a'*62}"[:64+18]] if i % 2 == 0 else [],
        )
        for i in range(4)
    ]
    append_event_batch(repo, run1, writer="watcher")
    run2 = [
        _legacy_search(
            trace_id="t-1", step_index=i, patch_id=f"{i:02d}{'a'*62}"[:64],
            commit_hex=commit_b, result="unknown", anchor_ids=[],
        )
        for i in range(4)
    ]
    append_event_batch(repo, run2, writer="watcher")


def _git_anchor_created(*, trace_id, step_index, patch_id, commit_hex):
    """A ``git_anchor_created`` draft shaped like ``anchors.py``'s own
    (``git show 8b71cc7f1ea:src/opentraces/core/trails/anchors.py``) --
    used only by :func:`_seed_legacy_interleaved` to reproduce the real
    pre-plan-090 writer's batch order, never by ``_seed_legacy`` (whose
    legacy batches are pure runs of search events)."""
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


def _seed_legacy_interleaved(repo: Path) -> None:
    """ONE reconcile-run batch, patches interleaved with their OWN
    ``git_anchor_created`` events -- the REAL pre-plan-090 writer's order
    (issue #358 repair round 3, blocker; verified against ``git show
    8b71cc7f1ea:src/opentraces/core/trails/anchors.py``): for each patch,
    ``reconcile_commit_anchors`` appended a ``git_anchor_search_completed``
    draft and, only when that patch anchored, a ``git_anchor_created`` draft
    right after it -- both into the SAME ``append_event_batch`` call, so a
    legacy group's members are routinely interleaved with non-member events
    WITHIN their own batch, not merely adjacent. ``_seed_legacy`` above
    (every other fixture in this module) never interleaves -- both of its
    batches are pure runs of search events -- so this is the one fixture
    that actually exercises the shape a mature, pre-plan-090 bucket has."""
    commit_a = "a" * 40
    patch_ids = [f"{i:02d}{'a' * 62}" for i in range(3)]
    drafts: list[TrailEventDraft] = []
    for i, patch_id in enumerate(patch_ids):
        anchored = i % 2 == 0
        drafts.append(
            _legacy_search(
                trace_id="t-1", step_index=i, patch_id=patch_id, commit_hex=commit_a,
                result="anchored" if anchored else "unknown",
                anchor_ids=[f"gitanchor-sha256:{patch_id}"] if anchored else [],
            )
        )
        if anchored:
            drafts.append(
                _git_anchor_created(trace_id="t-1", step_index=i, patch_id=patch_id, commit_hex=commit_a)
            )
    append_event_batch(repo, drafts, writer="watcher")


def _functional(events, *, strip_unknown_identity: bool = False):
    recs = []
    for e in events:
        for r in iter_search_records(e):
            r = dict(r)
            r.pop("source_event", None)
            if strip_unknown_identity and r.get("result") != "anchored":
                # #358 documented delta (module docstring guarantee 1): an
                # unknown outcome demoted into unanchored_trace_patch_ids
                # never carried these -- normalize the pre-compaction side to
                # the same shape so the comparison stays strict on every dedup
                # key and every anchored record's FULL identity.
                r["trace_id"] = None
                r["step_index"] = None
                r["generation_index"] = None
            recs.append(r)
    recs.sort(key=lambda r: (r.get("search_head_sha") or "", r.get("trace_patch_id") or "", r.get("result") or ""))
    return recs


def _dedup_keys(events) -> list[tuple]:
    return sorted(
        (r["trace_patch_id"], r["search_head_sha"], r["attribution_version"], r["result"])
        for e in events
        for r in iter_search_records(e)
    )


def test_compaction_preserves_functional_record_stream(tmp_path: Path) -> None:
    repo = tmp_path / "src"
    _init_repo(repo)
    _seed_legacy(repo)
    original = read_events(repo, verify=False)

    compacted, stats = compact_search_events(original)

    # 8 legacy per-patch events -> 2 summaries (one per reconcile batch/commit).
    assert stats.legacy_search_events_in == 8
    assert stats.groups_collapsed == 2
    assert stats.summary_events_out == 2
    assert stats.events_out < stats.events_in

    # #358: the collapsed group is v3-compact now, not v2 -- anchored-only
    # results[] plus the exact ids of every unknown outcome dropped from it.
    summaries = [e for e in compacted if e.event_type == "git_anchor_search_completed"]
    assert summaries and all(is_summary_search_event(e) for e in summaries)
    for event in summaries:
        assert event.payload["schema_version"] == ANCHOR_SEARCH_COVERAGE_SCHEMA_VERSION
        assert all(r["result"] == "anchored" for r in event.payload["results"])
        assert "coverage" not in event.payload, (
            "an offline rewrite of historical data can never mint a sound "
            "coverage boundary -- only the exact-id variant applies here"
        )

    # The per-patch FUNCTIONAL records are identical pre/post, MODULO the
    # documented delta: an unknown outcome loses trace_id/step_index/
    # generation_index once demoted into unanchored_trace_patch_ids.
    assert json.dumps(_functional(original, strip_unknown_identity=True), sort_keys=True) == json.dumps(
        _functional(compacted), sort_keys=True
    )
    assert _dedup_keys(original) == _dedup_keys(compacted)


def test_compaction_rewrites_bare_v2_summary_to_v3_compact(tmp_path: Path) -> None:
    """A v2 summary with ZERO unknown entries is still rewritten: the trigger
    is "does this payload already carry a v3 alternate key", never "does it
    have anything fat to drop" -- so a v2 label is never a terminal shape by
    itself, only a v3 one (in either variant) is."""
    repo = tmp_path / "src"
    _init_repo(repo)
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="git_anchor_search_completed",
                trace_id=None,
                step_index=None,
                capture_method=["watcher_reconcile"],
                payload=build_anchor_search_summary_payload(
                    schema_version="opentraces.trail.anchor_search.v2",
                    search_head={"algo": "sha1", "hex": "c" * 40},
                    algorithms_attempted=ANCHOR_ALGORITHMS_PHASE5,
                    results=[{"trace_patch_id": "tracepatch-sha256:" + "d" * 64,
                              "trace_id": "t-1", "step_index": 0, "generation_index": 0,
                              "result": "anchored", "created_anchor_ids": []}],
                ),
            )
        ],
        writer="watcher",
    )
    original = read_events(repo, verify=False)
    compacted, stats = compact_search_events(original)

    assert stats.groups_collapsed == 0, "a single self-contained summary is never a GROUP"
    assert stats.fat_summaries_rewritten == 1
    # No unknown entries in this fixture, so the documented delta never
    # triggers -- a strict comparison (no stripping) still holds.
    assert json.dumps(_functional(original), sort_keys=True) == json.dumps(
        _functional(compacted), sort_keys=True
    )

    event = compacted[0]
    assert event.payload["schema_version"] == ANCHOR_SEARCH_COVERAGE_SCHEMA_VERSION
    assert event.payload["unanchored_trace_patch_ids"] == [], (
        "still an exact (empty) accounting, not an omitted key"
    )


def test_compaction_mixed_log_drops_all_fat_and_is_idempotent(tmp_path: Path) -> None:
    """The full design scenario: legacy + v2-fat + v3-coverage + v3-compact +
    non-search events in one log. Every fat results[] entry must be gone,
    dedup keys must survive exactly, the rewritten chain must verify, a
    coverage claim's through-id patch must still resolve, and a second pass
    over the ALREADY-compacted output must be a byte-identical no-op."""
    repo = tmp_path / "src"
    _init_repo(repo)

    # Legacy group: 2 reconcile-run batches, 4 per-patch events each.
    _seed_legacy(repo)

    # v2 fat summary: 1 anchored, 5 unknown, one event.
    commit_c = "c" * 40
    v2_fat_entries = [
        {
            "trace_patch_id": "tracepatch-sha256:" + f"{i:02d}{'c'*62}"[:64],
            "trace_id": "t-2", "step_index": i, "generation_index": 0,
            "result": "anchored" if i == 0 else "unknown",
            "created_anchor_ids": ["gitanchor-sha256:" + "c" * 64] if i == 0 else [],
        }
        for i in range(6)
    ]
    append_event_batch(
        repo, [_v2_fat_search_event(commit_hex=commit_c, entries=v2_fat_entries)], writer="watcher",
    )

    # The trace_patch a coverage claim below points its through-id at, so
    # "the through-id patch still resolves after compaction" is checkable.
    through_id = "tracepatch-sha256:" + "d" * 64
    append_event_batch(
        repo, [_patch_created(trace_id="t-3", trace_patch_id=through_id, file_path="d.py")],
        writer="capture-claude-code",
    )

    # v3-coverage: the live write-path shape (anchors.py), one anchored entry
    # plus a through-pointer claim -- must pass through byte-identical.
    commit_e = "e" * 40
    coverage_payload = build_anchor_search_summary_payload(
        schema_version=ANCHOR_SEARCH_COVERAGE_SCHEMA_VERSION,
        search_head={"algo": "sha1", "hex": commit_e},
        algorithms_attempted=ANCHOR_ALGORITHMS_PHASE5,
        results=[{"trace_patch_id": "tracepatch-sha256:" + "e" * 64, "trace_id": "t-3",
                  "step_index": 1, "generation_index": 0, "result": "anchored",
                  "created_anchor_ids": []}],
        coverage={"through_trace_patch_id": through_id, "scope_trace_id": None},
    )
    append_event_batch(
        repo,
        [TrailEventDraft(
            event_type="git_anchor_search_completed", trace_id=None, step_index=None,
            capture_method=["post_commit_correlator"], payload=coverage_payload,
        )],
        writer="watcher",
    )

    # v3-compact: the live slim-flush shape (maturation.py), already
    # anchored-only plus exact ids -- must ALSO pass through byte-identical.
    commit_f = "f" * 40
    compact_payload = build_anchor_search_summary_payload(
        schema_version=ANCHOR_SEARCH_COVERAGE_SCHEMA_VERSION,
        search_head={"algo": "sha1", "hex": commit_f},
        algorithms_attempted=ANCHOR_ALGORITHMS_PHASE5,
        results=[{"trace_patch_id": "tracepatch-sha256:" + "1" * 64, "trace_id": None,
                  "step_index": None, "generation_index": 0, "result": "anchored",
                  "created_anchor_ids": []}],
        unanchored_trace_patch_ids=["tracepatch-sha256:" + "2" * 64],
    )
    append_event_batch(
        repo,
        [TrailEventDraft(
            event_type="git_anchor_search_completed", trace_id=None, step_index=None,
            capture_method=["watcher_reconcile"], payload=compact_payload,
        )],
        writer="maturation",
    )

    # An unrelated non-search event, to prove ordinary passthrough still works.
    append_event_batch(
        repo, [_patch_created(trace_id="t-4", trace_patch_id="tracepatch-sha256:" + "9" * 64, file_path="z.py")],
        writer="capture-claude-code",
    )

    original = read_events(repo, verify=False)
    compacted_once, stats = compact_search_events(original)

    # -- all fat gone: every summary's results[] is anchored-only.
    search_events = [e for e in compacted_once if e.event_type == "git_anchor_search_completed"]
    assert search_events
    for event in search_events:
        assert is_summary_search_event(event)
        results = event.payload.get("results") or []
        assert all(r.get("result") == "anchored" for r in results), (
            f"unknown entry survived compaction in {event.payload}"
        )

    # -- dedup keys identical before/after (the whole mixed log).
    assert _dedup_keys(original) == _dedup_keys(compacted_once)

    # -- coverage event untouched, and its through-id patch still resolves.
    coverage_before = [e for e in original if isinstance(e.payload.get("coverage"), dict)]
    coverage_after = [e for e in compacted_once if isinstance(e.payload.get("coverage"), dict)]
    assert len(coverage_before) == 1 and len(coverage_after) == 1
    assert coverage_after[0].payload == coverage_before[0].payload, (
        "a v3 coverage event must pass through compaction byte-content-preserved"
    )
    patch_created_ids_after = {
        e.payload.get("trace_patch_id")
        for e in compacted_once
        if e.event_type == "trace_patch_created"
    }
    assert through_id in patch_created_ids_after, (
        "the coverage claim's through_trace_patch_id needs no remap after "
        "compaction (position-independent) -- but the patch it points at "
        "must still actually be there"
    )

    # -- the live v3-compact (maturation-shaped) event is ALSO untouched.
    compact_before = [
        e for e in original
        if (e.payload.get("search_head") or {}).get("hex") == commit_f
    ]
    compact_after = [
        e for e in compacted_once
        if (e.payload.get("search_head") or {}).get("hex") == commit_f
    ]
    assert len(compact_before) == 1 and len(compact_after) == 1
    assert compact_after[0].payload == compact_before[0].payload, (
        "a live v3-compact event must pass through compaction byte-content-preserved"
    )

    # -- byte size strictly reduced.
    def _size(events) -> int:
        return len(json.dumps([e.model_dump(mode="json") for e in events]))

    assert _size(compacted_once) < _size(original)

    # -- chain re-finalizes valid (real repo write + verify).
    target = tmp_path / "target"
    _init_repo(target)
    compact_repo(repo, target, force=True)
    verdict = verify_event_log(target)
    assert verdict["event_chain_valid"] is True
    assert verdict["content_hashes_valid"] is True
    assert verdict["errors"] == []

    # -- second pass over the ALREADY-compacted stream is a byte-identical
    # no-op: nothing left to collapse or rewrite.
    compacted_twice, stats2 = compact_search_events(compacted_once)
    assert [e.model_dump(mode="json") for e in compacted_once] == [
        e.model_dump(mode="json") for e in compacted_twice
    ]
    assert stats2.legacy_search_events_in == 0
    assert stats2.fat_summaries_rewritten == 0
    assert stats2.groups_collapsed == 0
    assert stats2.events_in == stats2.events_out == stats.events_out


def test_compact_repo_roundtrips_event_stream(tmp_path: Path) -> None:
    src = tmp_path / "src"
    target = tmp_path / "target"
    _init_repo(src)
    _init_repo(target)
    _seed_legacy(src)

    compact_repo(src, target, force=True)
    compacted_in_target = read_events(target, verify=False)
    compacted_direct, _ = compact_search_events(read_events(src, verify=False))

    # The repo-written chain equals the in-memory compaction at the event level.
    assert [e.model_dump(mode="json") for e in compacted_in_target] == [
        e.model_dump(mode="json") for e in compacted_direct
    ]


def test_compact_repo_refuses_in_place(tmp_path: Path) -> None:
    repo = tmp_path / "src"
    _init_repo(repo)
    _seed_legacy(repo)
    import pytest

    with pytest.raises(ValueError):
        compact_repo(repo, repo, force=True)


def test_stream_compact_events_matches_list_path_on_interleaved_legacy_batch(tmp_path: Path) -> None:
    """Issue #358 repair round 3, blocker.

    The streaming compactor used to close a legacy group the instant ANY
    differing event appeared, then raise ``NonContiguousSearchGroupError``
    if the same ``(batch_id, search_head)`` key ever reappeared. That fires
    on the ORDINARY shape ``_seed_legacy_interleaved`` builds -- a patch's
    own ``git_anchor_created`` sitting between it and the next patch's
    search event, all one reconcile-run batch -- which is exactly how the
    real pre-plan-090 writer wrote history. The fix batches by
    ``batch_id`` (a legacy group can never span two batches -- ``batch_id``
    identifies one atomic ``append_event_batch`` call), so this must not
    only avoid raising but reproduce the list-based path byte-identically.
    """
    repo = tmp_path / "src"
    _init_repo(repo)
    _seed_legacy_interleaved(repo)
    original = read_events(repo, verify=False)

    compacted_list, list_stats = compact_search_events(original)

    stats = CompactionStats()
    compacted_stream = list(stream_compact_events(iter(original), stats))

    assert [e.model_dump(mode="json") for e in compacted_stream] == [
        e.model_dump(mode="json") for e in compacted_list
    ]
    assert stats.legacy_search_events_in == list_stats.legacy_search_events_in == 3
    assert stats.groups_collapsed == list_stats.groups_collapsed == 1
    assert stats.non_search_events == list_stats.non_search_events == 2
    assert stats.events_out == list_stats.events_out
    assert _dedup_keys(compacted_stream) == _dedup_keys(original)

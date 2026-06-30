"""Issue #116 B: anchor-search rollup compaction capability.

Pins that compacting legacy per-patch ``git_anchor_search_completed`` events into
plan-090 v2 summaries preserves the ``iter_search_records`` functional record
stream and round-trips through the event log byte-identically at the event level.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from opentraces.core.trails import TrailEventDraft, append_event_batch, read_events
from opentraces.core.trails.anchors import ANCHOR_ALGORITHMS_PHASE5
from opentraces.core.trails.ids import trace_patch_ref
from opentraces.core.trails.search_compaction import compact_repo, compact_search_events
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


def _functional(events):
    recs = []
    for e in events:
        for r in iter_search_records(e):
            r = dict(r)
            r.pop("source_event", None)
            recs.append(r)
    recs.sort(key=lambda r: (r.get("search_head_sha") or "", r.get("trace_patch_id") or "", r.get("result") or ""))
    return recs


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

    # The per-patch FUNCTIONAL records are byte-identical pre/post.
    assert json.dumps(_functional(original), sort_keys=True) == json.dumps(
        _functional(compacted), sort_keys=True
    )
    # Every emitted compacted search event is a v2 summary.
    summaries = [e for e in compacted if e.event_type == "git_anchor_search_completed"]
    assert summaries and all(is_summary_search_event(e) for e in summaries)


def test_compaction_is_idempotent_on_already_v2(tmp_path: Path) -> None:
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
    # An already-summarized log passes through with no collapse.
    assert stats.groups_collapsed == 0
    assert stats.summary_events_in == 1
    assert json.dumps(_functional(original), sort_keys=True) == json.dumps(
        _functional(compacted), sort_keys=True
    )


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

"""Regression coverage for the two correctness defects an adversarial Codex
review found in the #137 event-index (PR #138), each pinned with a test that was
RED before its fix:

1. Commit-scoped reads must not miss a ``{"hex": sha}`` payload that omits
   ``algo``. The read_events_scoped post-parse filter accepts ANY
   ``payload[key].get("hex")``, so the index's ``by_commit`` must be built from
   the same algo-agnostic hexes — else the index path returns FEWER events than
   the full scan (a byte-identity divergence; it broke
   tests/capture/test_watcher_sweep.py::TestGateScopedRead).

2. A persisted index that LAGS the ref head (another process appended; a worktree
   shares the ref but keeps its own index) must be caught up by reading only the
   delta and returning the COMPLETE event set — never silently serve the stale
   view. (The atomic single-file persistence makes head+postings inseparable, so
   the original fold-vs-append "recorded head outran its postings" race is gone
   by construction; this pins the remaining correctness mechanism, the lazy
   delta extend.)
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from opentraces.core.trails import event_index
from opentraces.core.trails.event_log import (
    _ref_head,
    read_events_for_trace,
    read_events_scoped,
)
from opentraces.core.trails.event_log import append_event_batch
from opentraces.core.trails.models import TrailEventDraft, sha256_text


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()


def _seed_with_algoless_search(repo: Path) -> tuple[str, str]:
    """One real commit + a v2 anchor-search summary whose ``search_head`` is a
    bare ``{"hex": sha}`` with NO ``algo`` (the test_watcher_sweep shape).
    Returns (commit_sha, trace_id)."""
    _init_repo(repo)
    (repo / "real.py").write_text("alpha\nbody\n")
    sha = _commit(repo, "real")
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="t-real",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": "tracepatch-sha256:realpatch01",
                    "file_path": "real.py",
                    "affected_range": {"start_line": 1, "end_line": 2},
                    "authored_text": "alpha\nbody\n",
                    "raw_authored_hash": sha256_text("alpha\nbody\n"),
                    "git_clean_hash": sha256_text("alpha\nbody"),
                    "limitations": [],
                },
            ),
            TrailEventDraft(
                event_type="git_anchor_search_completed",
                trace_id=None,
                step_index=None,
                capture_method=["reconcile"],
                payload={
                    "schema_version": "opentraces.trail.anchor_search.v2",
                    "summary": True,
                    # The load-bearing detail: hex WITHOUT algo.
                    "search_head": {"hex": sha},
                    "algorithms_attempted": ["exact_range_hash"],
                    "searched": 1,
                    "anchored": 1,
                    "unknown": 0,
                    "results": [
                        {
                            "trace_patch_id": "tracepatch-sha256:realpatch01",
                            "trace_id": "t-real",
                            "result": "anchored",
                            "created_anchor_ids": [],
                        }
                    ],
                },
            ),
        ],
        writer="test-fixture",
    )
    return sha, "t-real"


def test_scoped_read_finds_algoless_search_head(tmp_path: Path) -> None:
    """Defect 1: the commit-scoped read must return the search event even when
    its ``search_head`` carries no ``algo``."""
    sha, _trace = _seed_with_algoless_search(tmp_path)
    events = read_events_scoped(
        tmp_path,
        event_types={"git_anchor_search_completed"},
        commit_filter={"git_anchor_search_completed": "search_head"},
        commit_sha=sha,
    )
    assert len(events) == 1, (
        "index-backed scoped read dropped a search event whose search_head has "
        "no algo — by_commit must index algo-agnostic hexes to match the "
        "post-parse filter"
    )
    assert events[0].event_type == "git_anchor_search_completed"


def test_lagging_index_is_caught_up_not_stale(tmp_path: Path) -> None:
    """Defect 2: a persisted index that lags the ref must be extended by the
    delta and return the COMPLETE event set, never the stale view."""
    sha, trace = _seed_with_algoless_search(tmp_path)
    head1 = _ref_head(tmp_path)

    # A clean base at head1, then SNAPSHOT it before more events land.
    event_index.rebuild_event_index(tmp_path, head1)
    base_path = event_index._base_path(tmp_path)
    lagging_bytes = base_path.read_bytes()  # head1-only postings

    # Append a SECOND trace's events, advancing the ref to head2.
    append_event_batch(
        tmp_path,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="t-second",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": "tracepatch-sha256:secondpatch",
                    "file_path": "real.py",
                    "affected_range": {"start_line": 1, "end_line": 2},
                    "authored_text": "alpha\nbody\n",
                    "raw_authored_hash": sha256_text("alpha\nbody\n"),
                    "git_clean_hash": sha256_text("alpha\nbody"),
                    "limitations": [],
                },
            )
        ],
        writer="test-fixture",
    )
    head2 = _ref_head(tmp_path)
    assert head2 != head1

    # Force the persisted index to LAG at head1 (as a concurrent appender that
    # never updated THIS index would leave it), and drop the in-process memo.
    base_path.write_bytes(lagging_bytes)
    event_index.invalidate_event_index_memo(tmp_path)

    # The lagging head1 base must NOT be trusted for head2: fresh_index_for_read
    # extends it across the head1..head2 delta, so the new trace resolves.
    second = read_events_for_trace(tmp_path, "t-second")
    assert len(second) == 1, (
        "a lagging index served a stale view — the head2 trace was not found "
        "even though the ref advanced to head2"
    )
    # And the original trace still resolves identically (no regression).
    assert len(read_events_for_trace(tmp_path, trace)) == 2

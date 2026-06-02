#!/usr/bin/env python
"""U0 microbenchmark + perf gate for plan 090 (post-commit reconcile latency).

Builds a synthetic repo with N recorded ``trace_patch_created`` events, makes one
commit that matches ``--matches`` of them, then times ``reconcile_commit_anchors``
and counts how many TrailEvents the reconcile appended.

Baseline (pre-fix): reconcile appends ~N ``git_anchor_search_completed`` events
(one per patch) via per-event git subprocesses -> the ~200s hang + O(N) growth.

Target (A', post-fix): reconcile appends 1 summary event + K anchor events.

Usage:
    bench_reconcile.py --patches 800
    bench_reconcile.py --patches 5000 --matches 3 --assert-seconds 15 --assert-events-max 8
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from opentraces.core.trails import (
    GitObjectID,
    TrailEventDraft,
    append_event_batch,
    read_events,
    reconcile_commit_anchors,
)
import opentraces.core.trails.event_log as event_log


def _run(repo: Path, *args: str, **kw) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True, **kw
    ).stdout.strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _run(repo, "init", "-q")
    _run(repo, "config", "user.email", "a@b.c")
    _run(repo, "config", "user.name", "Bench")
    _run(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("# bench seed\n")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-q", "-m", "seed")


def _hash_object(repo: Path, content: str) -> str:
    return subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo, input=content, text=True, capture_output=True, check=True,
    ).stdout.strip()


def build_fixture(repo: Path, *, patches: int, matches: int) -> str:
    """Create N patch events; commit a diff that matches `matches` of them.

    Patches 0..matches-1 author text that is committed verbatim (-> anchored).
    The rest author unique non-committed text (-> 'unknown' search results: the
    explosion case the post-commit hook hits on a mature repo).
    """
    _init_repo(repo)
    zero = GitObjectID(hex=_hash_object(repo, "old\n"))
    drafts: list[TrailEventDraft] = []
    for i in range(patches):
        authored = f"ANCHOR_LINE_{i} = {i}\n"
        after = GitObjectID(hex=_hash_object(repo, authored))
        drafts.append(
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id=f"tr{i}",
                generation_index=0,
                step_index=1,
                capture_method=["hook_pretooluse", "hook_posttooluse"],
                payload={
                    "trace_patch_id": f"tracepatch-sha256:fixture-{i:06d}",
                    "snapshot_before_id": "snapshot-pre",
                    "snapshot_after_id": "snapshot-post",
                    "file_path": f"src/mod_{i}.py",
                    "affected_range": {"start_line": 1, "end_line": 1},
                    "authored_text": authored,
                    "raw_authored_hash": "sha256:fixture",
                    "git_clean_hash": "sha256:fixture",
                    "before_blob_id": zero.model_dump(mode="json"),
                    "after_blob_id": after.model_dump(mode="json"),
                    "limitations": [],
                },
            )
        )
    # Append patch events in one batch (fixture setup, not what we measure).
    append_event_batch(repo, drafts, writer="capture-claude-code")
    event_log.invalidate_read_events_cache(repo)

    # One commit that lands the first `matches` patches' authored text.
    for i in range(matches):
        (repo / f"src/mod_{i}.py").parent.mkdir(parents=True, exist_ok=True)
        (repo / f"src/mod_{i}.py").write_text(f"ANCHOR_LINE_{i} = {i}\n")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-q", "-m", "land matching patches")
    return _run(repo, "rev-parse", "HEAD")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patches", type=int, default=800)
    ap.add_argument("--matches", type=int, default=1)
    ap.add_argument("--assert-seconds", type=float, default=None)
    ap.add_argument("--assert-events-max", type=int, default=None)
    args = ap.parse_args()

    with tempfile.TemporaryDirectory(prefix="bench-reconcile-") as td:
        repo = Path(td) / "repo"
        head = build_fixture(repo, patches=args.patches, matches=args.matches)
        event_log.invalidate_read_events_cache(repo)

        before = len(read_events(repo, verify=False))
        event_log.invalidate_read_events_cache(repo)

        t0 = time.perf_counter()
        created = reconcile_commit_anchors(repo, head)
        elapsed = time.perf_counter() - t0

        event_log.invalidate_read_events_cache(repo)
        after = len(read_events(repo, verify=False))
        appended = after - before

        result = {
            "patches": args.patches,
            "matches": args.matches,
            "anchors_created": len(created),
            "events_before": before,
            "events_after": after,
            "events_appended_by_reconcile": appended,
            "reconcile_seconds": round(elapsed, 3),
        }
        print(json.dumps(result, indent=2))

        failed = False
        if args.assert_seconds is not None and elapsed > args.assert_seconds:
            print(f"FAIL: reconcile {elapsed:.1f}s > {args.assert_seconds}s budget", file=sys.stderr)
            failed = True
        if args.assert_events_max is not None and appended > args.assert_events_max:
            print(f"FAIL: appended {appended} events > {args.assert_events_max} budget", file=sys.stderr)
            failed = True
        return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Cluster G — P2: ``patch_survival_cached`` event-keyed cache.

These tests pin the contract for the event-log-backed survival cache:

* ``sync_patch`` writes a ``patch_survival_cached`` event after a fresh
  compute.
* On a second call against the same HEAD the row is served from cache
  without recomputing.
* When HEAD moves the cache key changes and the cached row is no longer
  served — the next call recomputes and writes a new cache event.
* A 100-patch warm batch completes in <5s.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest.mock import patch as mock_patch

from opentraces.core.trails import (
    BatchSyncContext,
    TrailEventDraft,
    append_event_batch,
    build_survival_cache_index,
    read_events,
    sync_patch,
)
from opentraces.core.trails.event_log import CACHED_SURVIVAL_EVENT_TYPE
from opentraces.core.trails.models import sha256_text


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True
    )


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return _git(repo, "rev-parse", "HEAD")


def _seed_patch_anchor(
    repo: Path,
    *,
    trace_id: str,
    patch_id: str,
    anchor_commit: str,
    file_path: str,
    text: str,
) -> None:
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id=trace_id,
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": f"tracepatch-sha256:{patch_id}",
                    "file_path": file_path,
                    "affected_range": {"start_line": 1, "end_line": 2},
                    "authored_text": text,
                    "raw_authored_hash": sha256_text(text),
                    "git_clean_hash": sha256_text(text.strip()),
                    "limitations": [],
                },
            ),
            TrailEventDraft(
                event_type="git_anchor_created",
                trace_id=trace_id,
                step_index=1,
                capture_method=["manual_attach"],
                payload={
                    "git_anchor_id": f"gitanchor-sha256:{patch_id}",
                    "trace_patch_id": f"tracepatch-sha256:{patch_id}",
                    "commit_id": {"algo": "sha1", "hex": anchor_commit},
                    "path": file_path,
                    "range": {"start_line": 1, "end_line": 2},
                    "relation": "anchored_in_git",
                    "evidence_tier": "exact_range_hash",
                    "evidence_firmness": "firm",
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )


def _count_cache_events(repo: Path) -> int:
    return sum(
        1
        for event in read_events(repo, verify=False)
        if event.event_type == CACHED_SURVIVAL_EVENT_TYPE
    )


def test_sync_patch_writes_cache_event(tmp_path: Path) -> None:
    """First ``sync_patch`` against a HEAD writes a cache event."""
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("alpha\nline\n")
    head = _commit(tmp_path, "seed")
    _seed_patch_anchor(
        tmp_path,
        trace_id="t-1",
        patch_id="abc12345",
        anchor_commit=head,
        file_path="a.py",
        text="alpha\nline\n",
    )

    assert _count_cache_events(tmp_path) == 0
    result = sync_patch(tmp_path, f"tracepatch-sha256:abc12345")
    assert result["current_survival"]["survival_state"] == "alive_on_path"
    assert _count_cache_events(tmp_path) == 1


def test_sync_patch_reads_cache_when_head_unchanged(tmp_path: Path) -> None:
    """Second ``sync_patch`` against the same HEAD short-circuits the
    expensive compute path and reports ``cache.hit=True``."""
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("alpha\nline\n")
    head = _commit(tmp_path, "seed")
    _seed_patch_anchor(
        tmp_path,
        trace_id="t-1",
        patch_id="abc12345",
        anchor_commit=head,
        file_path="a.py",
        text="alpha\nline\n",
    )

    # First call writes the cache.
    sync_patch(tmp_path, "tracepatch-sha256:abc12345")
    # Second call must pass the same events list so the in-process index
    # picks up the freshly-written cache entry.
    events = read_events(tmp_path, verify=False)
    result = sync_patch(tmp_path, "tracepatch-sha256:abc12345", events=events)
    assert result.get("cache", {}).get("hit") is True
    assert result["current_survival"]["survival_state"] == "alive_on_path"


def test_sync_patch_invalidates_when_head_moves(tmp_path: Path) -> None:
    """When HEAD advances the cache key changes; sync_patch recomputes
    fresh and writes a new cache entry against the new HEAD."""
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("alpha\nline\n")
    head_a = _commit(tmp_path, "seed")
    _seed_patch_anchor(
        tmp_path,
        trace_id="t-1",
        patch_id="abc12345",
        anchor_commit=head_a,
        file_path="a.py",
        text="alpha\nline\n",
    )

    sync_patch(tmp_path, "tracepatch-sha256:abc12345")
    cache_events_at_head_a = _count_cache_events(tmp_path)

    # Advance HEAD by an unrelated commit.
    (tmp_path / "b.py").write_text("beta\n")
    head_b = _commit(tmp_path, "second")
    assert head_a != head_b

    # Re-sync — old cache is keyed by head_a, miss, recompute, new entry.
    result = sync_patch(tmp_path, "tracepatch-sha256:abc12345")
    assert result.get("cache", {}).get("hit") is False
    cache_events_at_head_b = _count_cache_events(tmp_path)
    assert cache_events_at_head_b > cache_events_at_head_a, (
        "expected a new cache event after HEAD moved"
    )

    # And a third call against head_b should now hit the cache.
    events = read_events(tmp_path, verify=False)
    third = sync_patch(tmp_path, "tracepatch-sha256:abc12345", events=events)
    assert third.get("cache", {}).get("hit") is True


def test_warm_batch_under_5s_for_100_patches(tmp_path: Path) -> None:
    """Warm-cache batch of 100 patches against a stable HEAD must run
    in under 5 seconds. This is the P2 acceptance gate."""
    _init_repo(tmp_path)

    # Seed 100 patches across 100 distinct files. One commit per file
    # so each anchor has a distinct anchor_commit; HEAD is the latest.
    patch_ids = []
    for index in range(100):
        file_path = f"file_{index:03d}.py"
        text = f"alpha {index}\nline\n"
        (tmp_path / file_path).write_text(text)
        head = _commit(tmp_path, f"seed {index}")
        patch_id = f"{index:03d}{'a' * 61}"[:64]
        _seed_patch_anchor(
            tmp_path,
            trace_id=f"t-{index:03d}",
            patch_id=patch_id,
            anchor_commit=head,
            file_path=file_path,
            text=text,
        )
        patch_ids.append(f"tracepatch-sha256:{patch_id}")

    # Warm-up pass: fill the cache. Use one BatchSyncContext explicitly so
    # we can flush at the end of the warm-up — mirroring the real CLI
    # process-exit flush flow without waiting for atexit.
    events = read_events(tmp_path, verify=False)
    with BatchSyncContext(repo=tmp_path) as ctx:
        for patch_id in patch_ids:
            sync_patch(tmp_path, patch_id, events=events, batch_ctx=ctx)
        # Explicit flush guarantees the cache events land before the warm
        # pass reads them. The ``with`` block already does this on exit;
        # we make it explicit here for the assertion afterward.
        ctx.flush_cache()
        assert ctx.calls["cache_writes_flushed"] >= 100

    # Re-read events so we pick up the cache entries we just wrote.
    events = read_events(tmp_path, verify=False)

    # Warm pass: every call should hit the cache.
    t0 = time.time()
    hits = 0
    for patch_id in patch_ids:
        result = sync_patch(tmp_path, patch_id, events=events)
        if result.get("cache", {}).get("hit") is True:
            hits += 1
    elapsed = time.time() - t0
    assert hits == 100, f"warm pass had {hits}/100 cache hits"

    assert elapsed < 5.0, (
        f"warm-cache batch took {elapsed:.2f}s for 100 patches (target <5s)"
    )


def test_build_survival_cache_index_returns_latest(tmp_path: Path) -> None:
    """When two cache events exist for the same key the latest wins."""
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("alpha\nline\n")
    head = _commit(tmp_path, "seed")
    _seed_patch_anchor(
        tmp_path,
        trace_id="t-1",
        patch_id="abc12345",
        anchor_commit=head,
        file_path="a.py",
        text="alpha\nline\n",
    )

    # First sync writes a cache row.
    first = sync_patch(tmp_path, "tracepatch-sha256:abc12345")
    first_state = first["current_survival"]["survival_state"]

    # Append a second cache event manually — different state — and read it.
    from opentraces.core.trails import (
        append_survival_cache_events,
        make_survival_cache_draft,
    )

    draft = make_survival_cache_draft(
        trace_patch_id="tracepatch-sha256:abc12345",
        observed_head_sha=head,
        survival={
            "survival_state": "alive_transformed",
            "retention_fraction": 1.0,
        },
    )
    append_survival_cache_events(tmp_path, [draft])

    events = read_events(tmp_path, verify=False)
    cache_index = build_survival_cache_index(events)
    cached = cache_index.get(("tracepatch-sha256:abc12345", head))
    assert cached is not None
    assert cached["survival_state"] == "alive_transformed", (
        "last-write-wins should pick the second event, not the first "
        f"({first_state})"
    )

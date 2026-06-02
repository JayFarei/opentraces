"""Bug B regression guards: the post-commit hook must not materialise the whole
~589K-event append-only log.

Two hot paths on every `git commit` previously rehydrated the ENTIRE event
stream as Pydantic objects (~7.5GB RSS):

1. ``append_event_batch`` read the full history via ``_read_events_from_head``
   only to compute ``(max_sequence, last_event_id)`` off the tail.
2. ``reconcile_commit_anchors`` called ``read_events`` (full union + verify) to
   read 3 event types it could have scoped.

These guards pin the bounded replacements (``_read_head_batch_tail``,
``read_events_scoped``) AND the invariants the earlier WRONG fix violated
(returning only the HEAD tree drops history and breaks ``verify``). The autouse
``_isolate_opentraces_global_state`` fixture keeps these hermetic.
"""
from __future__ import annotations

import pickle
import subprocess
from pathlib import Path

from opentraces.core.trails import (
    EVENT_LOG_REF,
    TrailEventDraft,
    append_event_batch,
    read_events,
    reconcile_commit_anchors,
)
import opentraces.core.trails.event_log as event_log


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _append(repo: Path, event_type: str, payload: dict, *, trace_id: str = "t1") -> None:
    append_event_batch(
        repo,
        [TrailEventDraft(
            event_type=event_type,
            trace_id=trace_id,
            step_index=1,
            capture_method=["hook_posttooluse"],
            payload=payload,
        )],
        writer="test-fixture",
    )


def _build_multi_batch_log(repo: Path, batches: int = 6) -> None:
    """Many single-event commits => a non-cumulative log of `batches` events."""
    _init_repo(repo)
    for i in range(batches):
        _append(repo, "trace_snapshot_created",
                {"snapshot_id": f"s{i}", "limitations": []})
        event_log.invalidate_read_events_cache(repo)


# --------------------------------------------------------------------------- #
# Guard 1 — §2a: append fast-path tail read == full-read tail (no full read)
# --------------------------------------------------------------------------- #

def test_append_fast_path_tail_equals_full_read(tmp_path):
    repo = tmp_path / "r"
    _build_multi_batch_log(repo, batches=6)
    head = event_log._ref_head(repo)

    full = event_log._read_events_from_head(repo, head)
    expected = (full[-1].event_sequence, full[-1].event_id)

    tail = event_log._read_head_batch_tail(repo, head)
    assert tail == expected, (
        "the append fast-path must derive (max_sequence, last_event_id) from the "
        "HEAD batch's tail blob, identical to the full-read tail"
    )


def test_append_does_not_materialise_full_history(tmp_path, monkeypatch):
    """A new append must not call the whole-history reader, and the resulting
    chain must still verify (correct next_sequence + previous_event_id)."""
    repo = tmp_path / "r"
    _build_multi_batch_log(repo, batches=6)
    event_log.invalidate_read_events_cache(repo)

    def boom(*a, **k):
        raise AssertionError("append must not read the full event history")

    monkeypatch.setattr(event_log, "_read_events_from_head", boom)
    _append(repo, "trace_snapshot_created", {"snapshot_id": "s-new", "limitations": []})

    monkeypatch.undo()
    event_log.invalidate_read_events_cache(repo)
    events = read_events(repo)  # verify=True; raises on a broken chain
    assert [e.event_sequence for e in events] == list(range(1, 8))
    assert event_log.event_log_status(repo)["state"] == "ok"


# --------------------------------------------------------------------------- #
# Guard 2 — §2b: scoped read == type/commit-filtered full read
# --------------------------------------------------------------------------- #

def test_scoped_read_equals_filtered_full_read(tmp_path):
    repo = tmp_path / "r"
    _init_repo(repo)
    # Mixed event types across many commits.
    for i in range(4):
        _append(repo, "trace_snapshot_created", {"snapshot_id": f"s{i}", "limitations": []})
        _append(repo, "trace_patch_created",
                {"trace_patch_id": f"tp{i}", "authored_text": f"x{i}", "limitations": []})
    event_log.invalidate_read_events_cache(repo)

    wanted = {"trace_patch_created"}
    scoped = event_log.read_events_scoped(repo, event_types=wanted)
    full = [e for e in read_events(repo, verify=False) if e.event_type in wanted]

    assert [e.event_id for e in scoped] == [e.event_id for e in full]
    assert {e.event_type for e in scoped} == wanted


def test_scoped_read_does_not_poison_full_cache(tmp_path):
    """The scoped accessor must not write the shared (repo, head, verify) cache,
    or a later full read in the same process would be silently truncated."""
    repo = tmp_path / "r"
    _init_repo(repo)
    for i in range(3):
        _append(repo, "trace_snapshot_created", {"snapshot_id": f"s{i}", "limitations": []})
        _append(repo, "trace_patch_created", {"trace_patch_id": f"tp{i}", "limitations": []})
    event_log.invalidate_read_events_cache(repo)

    event_log.read_events_scoped(repo, event_types={"trace_patch_created"})
    full = read_events(repo, verify=False)
    assert len(full) == 6, "full read after a scoped read must still return all events"


# --------------------------------------------------------------------------- #
# Guard 3 — the Bug B guard: reconcile must not materialise the full log
# --------------------------------------------------------------------------- #

def test_reconcile_does_not_materialise_full_log(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    _init_repo(repo)
    _append(repo, "trace_patch_created",
            {"trace_patch_id": "tp1", "authored_text": "no match here", "limitations": []})
    event_log.invalidate_read_events_cache(repo)

    def boom(*a, **k):
        raise AssertionError("reconcile must not read the full event history")

    # The whole-history reader is the funnel for BOTH the old full read_events
    # cold path AND the old append-time `existing` read. If reconcile touches it,
    # it materialised the full log.
    monkeypatch.setattr(event_log, "_read_events_from_head", boom)

    # Must complete (best-effort) using only the scoped accessor + tail reads.
    created = reconcile_commit_anchors(repo, "HEAD")
    assert isinstance(created, list)


# --------------------------------------------------------------------------- #
# Guard 4 — sentinel: head-only read WOULD break verify (why §2a is tail-only)
# --------------------------------------------------------------------------- #

def test_head_only_read_would_break_verify(tmp_path):
    repo = tmp_path / "r"
    _build_multi_batch_log(repo, batches=5)
    head = event_log._ref_head(repo)

    # The HEAD commit's tree is only the LAST batch (high starting sequence).
    head_only = event_log._read_events_from_head_commit_only(repo, head) \
        if hasattr(event_log, "_read_events_from_head_commit_only") else None
    if head_only is None:
        # Derive the head-only subset directly from the HEAD batch entries.
        entries = event_log._event_blob_entries(repo, [head])
        from opentraces.core.trails.models import TrailEvent
        head_only = [TrailEvent.model_validate_json(raw)
                     for raw in event_log._read_blobs_batch(repo, entries)]

    assert head_only, "HEAD batch must be non-empty"
    assert head_only[0].event_sequence > 1, "non-cumulative: HEAD batch starts high"

    _content_ok, chain_ok, errors, _ = event_log._check_event_chain(
        sorted(head_only, key=lambda e: e.event_sequence),
        start_sequence=1,
        start_previous_event_id=None,
    )
    assert not chain_ok
    assert any("non-contiguous event_sequence" in e for e in errors)
    assert any("previous_event_id mismatch" in e for e in errors)


# --------------------------------------------------------------------------- #
# Guard 5 — §2c: a corrupt snapshot self-heals to a clean full read
# --------------------------------------------------------------------------- #

def test_incremental_snapshot_continuity_selfheal(tmp_path):
    repo = tmp_path / "r"
    _build_multi_batch_log(repo, batches=6)
    head = event_log._ref_head(repo)
    full = event_log._read_events_from_head(repo, head)

    # Write a snapshot for the CURRENT head but drop a middle event so the
    # cached list is non-contiguous (snap_head == head fast path would return
    # this corrupt list verbatim without the §2c continuity guard).
    snap_path = event_log._event_cache_path(repo)
    assert snap_path is not None
    corrupt = [e for e in full if e.event_sequence != 3]
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    with snap_path.open("wb") as fh:
        pickle.dump(
            {"format": event_log._EVENT_CACHE_FORMAT, "head": head, "events": corrupt},
            fh, protocol=pickle.HIGHEST_PROTOCOL,
        )
    event_log.invalidate_read_events_cache(repo)

    healed = read_events(repo, verify=False)
    assert [e.event_sequence for e in healed] == list(range(1, 7)), (
        "a non-contiguous snapshot must self-heal via a clean full read"
    )

"""Phase 5 ``trail rebuild`` and GC equivalence."""
from __future__ import annotations

import subprocess
from pathlib import Path

from opentraces.core.trails import (
    close_step_window_with_snapshot,
    open_step_window,
    read_events,
    rebuild_projections,
)


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _list_snapshot_refs(repo: Path) -> dict[str, str]:
    out = subprocess.check_output(
        [
            "git",
            "for-each-ref",
            "--format=%(refname) %(objectname)",
            "refs/opentraces/local/traces/",
        ],
        cwd=repo,
        text=True,
    )
    refs: dict[str, str] = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        ref, oid = line.split(" ", 1)
        refs[ref] = oid
    return refs


def _delete_snapshot_refs(repo: Path) -> int:
    refs = _list_snapshot_refs(repo)
    for ref in refs:
        subprocess.run(
            ["git", "update-ref", "-d", ref], cwd=repo, check=True
        )
    return len(refs)


def _capture_step(repo: Path, *, trace_id: str, step_index: int, content: str) -> None:
    open_step_window(
        repo,
        trace_id=trace_id,
        step_index=step_index,
        agent_step_id=f"step_{step_index}",
        tool_call_id=f"tc{step_index}",
        capture_method=["hook_pretooluse"],
    )
    (repo / "auth.py").write_text(content)
    close_step_window_with_snapshot(
        repo,
        trace_id=trace_id,
        step_index=step_index,
        agent_step_id=f"step_{step_index}",
        tool_call_id=f"tc{step_index}",
        capture_method=["hook_posttooluse"],
    )


def test_rebuild_recreates_snapshot_refs_from_event_log(tmp_path: Path) -> None:
    """``rebuild_projections`` re-derives advisory snapshot refs from the
    canonical event log. The contract is that the event log is the
    source of truth; snapshot refs are convenience indexes that can be
    dropped and re-built without losing replayability.
    """
    _init_repo(tmp_path)
    _capture_step(tmp_path, trace_id="tr1", step_index=1, content="x = 1\n")
    _capture_step(tmp_path, trace_id="tr1", step_index=2, content="x = 2\n")

    refs_before = _list_snapshot_refs(tmp_path)
    assert len(refs_before) >= 2, refs_before

    deleted = _delete_snapshot_refs(tmp_path)
    assert deleted == len(refs_before)
    assert _list_snapshot_refs(tmp_path) == {}

    summary = rebuild_projections(tmp_path)
    assert summary["snapshot_refs_created"] == len(refs_before)
    assert summary["snapshot_refs_missing_object"] == 0

    refs_after = _list_snapshot_refs(tmp_path)
    assert refs_after == refs_before, (
        "rebuild must produce byte-equivalent snapshot refs"
    )


def test_rebuild_is_idempotent(tmp_path: Path) -> None:
    """A second ``rebuild_projections`` call after a successful rebuild
    creates no new refs and reports the existing refs as unchanged.
    """
    _init_repo(tmp_path)
    _capture_step(tmp_path, trace_id="tr1", step_index=1, content="x = 1\n")

    first = rebuild_projections(tmp_path)
    second = rebuild_projections(tmp_path)
    assert second["snapshot_refs_created"] == 0
    assert (
        second["snapshot_refs_unchanged"]
        == first["snapshot_refs_created"] + first["snapshot_refs_unchanged"]
    )


def test_rebuild_after_gc_produces_equivalent_projections(tmp_path: Path) -> None:
    """Plan §Phase 5 acceptance criterion (line 260).

    Snapshot refs are advisory; deleting them and running
    ``git gc --prune=now --aggressive`` must not destroy replayability.
    The event log embeds snapshot trees as subtrees in batch commits so
    Git GC cannot prune them. Rebuild after GC must produce snapshot
    refs that point at the same tree SHAs as before the cleanup.
    """
    _init_repo(tmp_path)
    _capture_step(tmp_path, trace_id="tr1", step_index=1, content="x = 1\n")
    _capture_step(tmp_path, trace_id="tr1", step_index=2, content="x = 2\n")

    refs_before = _list_snapshot_refs(tmp_path)
    events_before = read_events(tmp_path)

    # Drop projections and aggressively prune.
    _delete_snapshot_refs(tmp_path)
    subprocess.run(
        ["git", "reflog", "expire", "--expire=now", "--all"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "gc", "--prune=now", "--aggressive"],
        cwd=tmp_path,
        check=True,
    )

    # Event log is intact — the canonical store survives GC because
    # batch commits embed snapshot trees as subtrees.
    events_after_gc = read_events(tmp_path)
    assert len(events_after_gc) == len(events_before)
    for prior, post in zip(events_before, events_after_gc):
        assert prior.event_id == post.event_id
        assert prior.content_hash == post.content_hash

    summary = rebuild_projections(tmp_path)
    assert summary["snapshot_refs_created"] == len(refs_before)
    assert summary["snapshot_refs_missing_object"] == 0, (
        "snapshot tree objects must survive GC because the event log "
        "embeds them as batch-tree subtrees"
    )

    refs_rebuilt = _list_snapshot_refs(tmp_path)
    assert refs_rebuilt == refs_before, (
        "rebuild after GC must produce byte-equivalent snapshot refs"
    )

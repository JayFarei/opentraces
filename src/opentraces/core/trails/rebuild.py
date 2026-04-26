"""Rebuild advisory projections from canonical TrailEvents.

Snapshot refs under ``refs/opentraces/local/traces/<trace_id>/<gen>/
snapshots/step_<n>`` are *create-only advisory indexes* (per plan §Phase
2 acceptance criteria, line 149). Deleting them must not destroy
replayability — the canonical store is the append-only event log at
``refs/opentraces/local/events/v1``, which embeds snapshot trees as
subtrees in batch commits so Git GC cannot prune them.

``trail rebuild`` walks the event log and re-creates snapshot refs from
the ``trace_snapshot_created`` events. Notes, SQLite caches, and other
secondary projections are out of scope for Phase 5.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .event_log import read_events
from .models import TrailEvent


def _git(
    repo: Path,
    args: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=check,
    )


def _ref_exists(repo: Path, ref: str) -> bool:
    return _git(repo, ["show-ref", "--verify", "--quiet", ref], check=False).returncode == 0


def _create_or_update_snapshot_ref(repo: Path, ref: str, tree_hex: str) -> str:
    """Create the snapshot ref or leave it untouched if it already points
    at ``tree_hex``. Returns ``"created"``, ``"unchanged"``, or
    ``"missing_object"``.
    """
    type_proc = _git(repo, ["cat-file", "-t", tree_hex], check=False)
    if type_proc.returncode != 0 or type_proc.stdout.strip() != "tree":
        return "missing_object"
    if _ref_exists(repo, ref):
        existing = _git(repo, ["rev-parse", ref], check=False).stdout.strip()
        if existing == tree_hex:
            return "unchanged"
    _git(repo, ["update-ref", ref, tree_hex])
    return "created"


def _snapshot_ref_for(event: TrailEvent) -> str | None:
    if event.event_type != "trace_snapshot_created":
        return None
    if event.trace_id is None or event.step_index is None:
        return None
    payload = event.payload
    if payload.get("snapshot_role") not in (None, "after"):
        return None
    generation = event.generation_index or 0
    return (
        f"refs/opentraces/local/traces/{event.trace_id}"
        f"/{generation}/snapshots/step_{event.step_index}"
    )


def rebuild_projections(repo: Path) -> dict[str, Any]:
    """Re-derive snapshot refs from ``refs/opentraces/local/events/v1``.

    Idempotent: re-running on an already-rebuilt projection produces no
    new refs and reports ``snapshot_refs_unchanged`` instead of
    ``snapshot_refs_created``.

    Returns a summary describing what was rebuilt. The canonical source
    of truth is the event log; this function is a convenience for
    consumers that want fast access via Git refs.
    """
    repo = repo.resolve()
    events = read_events(repo)
    summary = {
        "snapshot_refs_created": 0,
        "snapshot_refs_unchanged": 0,
        "snapshot_refs_missing_object": 0,
        "snapshot_events_seen": 0,
    }
    seen_refs: set[str] = set()
    for event in events:
        ref = _snapshot_ref_for(event)
        if ref is None:
            continue
        summary["snapshot_events_seen"] += 1
        tree_id = event.payload.get("tree_id") or {}
        tree_hex = tree_id.get("hex")
        if not tree_hex:
            continue
        if ref in seen_refs:
            continue
        seen_refs.add(ref)
        result = _create_or_update_snapshot_ref(repo, ref, tree_hex)
        if result == "created":
            summary["snapshot_refs_created"] += 1
        elif result == "unchanged":
            summary["snapshot_refs_unchanged"] += 1
        elif result == "missing_object":
            summary["snapshot_refs_missing_object"] += 1
    return summary

"""Mark Git Anchors superseded after a Git rewrite operation.

When ``git commit --amend``, ``git rebase``, ``git filter-branch``, or
similar operations rewrite history, anchored commits become unreachable
from HEAD. The post-rewrite hook emits a list of ``(old_sha, new_sha)``
pairs to stdin; this module translates those pairs into
``git_anchor_superseded`` events so downstream consumers can route
around the dead commit.

Phase 5 ships the substrate. Wiring the post-rewrite hook into the
project's ``.git/hooks/`` directory is a follow-up; tests drive the
function directly so the canonical contract is pinned.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .anchors import reconcile_commit_anchors
from .event_log import append_event_batch, read_events
from .models import GitObjectID, TrailEventDraft

SUPERSEDE_CAPTURE_METHOD = ["post_rewrite_hook"]
SUPERSEDE_WRITER = "post-rewrite-hook"


def supersede_anchors_for_rewrite(
    repo: Path,
    rewrites: Iterable[tuple[str, str]],
    *,
    writer: str = SUPERSEDE_WRITER,
    reconcile_new: bool = True,
) -> dict[str, Any]:
    """Emit ``git_anchor_superseded`` events for rewrite pairs.

    For each ``(old_sha, new_sha)`` pair, finds anchors whose
    ``commit_id.hex`` equals ``old_sha`` and appends a supersede event
    pointing at ``new_sha``. When ``reconcile_new=True`` (default),
    runs ``reconcile_commit_anchors`` against each unique new SHA so a
    fresh anchor is created at the rewritten commit. Idempotent: if a
    supersede event already exists for the (anchor, new_sha) pair, it
    is not re-emitted.

    Returns a summary describing the work performed.
    """
    repo = repo.resolve()
    rewrite_pairs = [(old, new) for old, new in rewrites if old != new]
    if not rewrite_pairs:
        return {
            "rewrites": 0,
            "anchors_superseded": 0,
            "anchors_already_superseded": 0,
            "new_anchors_created": 0,
        }

    events = read_events(repo)
    existing_supersede_keys = {
        (
            event.payload.get("git_anchor_id"),
            (event.payload.get("new_commit_id") or {}).get("hex"),
        )
        for event in events
        if event.event_type == "git_anchor_superseded"
    }
    anchors_by_old_sha: dict[str, list[Any]] = {}
    for event in events:
        if event.event_type != "git_anchor_created":
            continue
        commit_hex = (event.payload.get("commit_id") or {}).get("hex")
        if commit_hex:
            anchors_by_old_sha.setdefault(commit_hex, []).append(event)

    drafts: list[TrailEventDraft] = []
    superseded = 0
    already_superseded = 0
    for old_sha, new_sha in rewrite_pairs:
        for anchor_event in anchors_by_old_sha.get(old_sha, []):
            anchor_payload = anchor_event.payload
            git_anchor_id = anchor_payload.get("git_anchor_id")
            if not git_anchor_id:
                continue
            if (git_anchor_id, new_sha) in existing_supersede_keys:
                already_superseded += 1
                continue
            existing_supersede_keys.add((git_anchor_id, new_sha))
            drafts.append(
                TrailEventDraft(
                    event_type="git_anchor_superseded",
                    trace_id=anchor_event.trace_id,
                    generation_index=anchor_event.generation_index,
                    step_index=anchor_event.step_index,
                    capture_method=list(SUPERSEDE_CAPTURE_METHOD),
                    payload={
                        "git_anchor_id": git_anchor_id,
                        "trace_patch_id": anchor_payload.get("trace_patch_id"),
                        "old_commit_id": GitObjectID(hex=old_sha).model_dump(
                            mode="json"
                        ),
                        "new_commit_id": GitObjectID(hex=new_sha).model_dump(
                            mode="json"
                        ),
                        "rewrite_kind": "post_rewrite",
                    },
                )
            )
            superseded += 1

    if drafts:
        append_event_batch(repo, drafts, writer=writer)

    new_anchors_created = 0
    if reconcile_new:
        unique_new_shas = {new_sha for _old, new_sha in rewrite_pairs}
        for new_sha in unique_new_shas:
            new_anchors = reconcile_commit_anchors(repo, new_sha)
            new_anchors_created += len(new_anchors)

    return {
        "rewrites": len(rewrite_pairs),
        "anchors_superseded": superseded,
        "anchors_already_superseded": already_superseded,
        "new_anchors_created": new_anchors_created,
    }

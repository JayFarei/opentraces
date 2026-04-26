"""Delayed Git Anchor reconciliation for Trace Trails."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from ...enrichment._shared import path_matches
from ...enrichment.attribution import _norm, _parse_diff_hunks_with_content
from .event_log import append_event_batch, read_events
from .models import GitObjectID, TrailEventDraft

ANCHOR_ALGORITHMS_PHASE3 = ["exact_range_hash"]


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _id(prefix: str, material: dict[str, Any]) -> str:
    raw = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return f"{prefix}-sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _oid(repo: Path, rev_path: str) -> dict[str, str] | None:
    out = _git(repo, "rev-parse", rev_path, check=False)
    if not out:
        return None
    try:
        return GitObjectID(hex=out).model_dump(mode="json")
    except Exception:
        return None


def _stable_patch_id(repo: Path, commit: str) -> str | None:
    diff = _git(repo, "show", "--format=", "--no-color", commit)
    proc = subprocess.run(
        ["git", "patch-id", "--stable"],
        cwd=repo,
        input=diff,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout.split()[0]


def _find_exact_anchor(patch: dict[str, Any], hunks: dict[str, list[dict]]) -> dict[str, Any] | None:
    file_path = patch.get("file_path")
    authored = patch.get("authored_text") or ""
    needle = _norm(authored)
    if not file_path or not needle:
        return None
    for hunk_path, file_hunks in hunks.items():
        if not path_matches(file_path, hunk_path):
            continue
        for hunk in file_hunks:
            haystack = _norm(hunk.get("added_text") or "")
            if needle and needle in haystack:
                return {
                    "path": hunk_path,
                    "range": {
                        "start_line": hunk.get("added_start"),
                        "end_line": hunk.get("added_end"),
                    },
                }
    return None


def reconcile_commit_anchors(
    repo: Path,
    commit_ref: str = "HEAD",
    *,
    writer: str = "post-commit-correlator",
) -> list[dict[str, Any]]:
    """Search existing Trace Patches against a commit and append anchor events."""
    repo = repo.resolve()
    commit = _git(repo, "rev-parse", commit_ref)
    commit_id = {"algo": "sha1", "hex": commit}
    diff = _git(repo, "show", "--format=", "--no-color", "-U3", commit)
    hunks = _parse_diff_hunks_with_content(diff)
    events = read_events(repo)
    existing_anchor_keys = {
        (event.payload.get("trace_patch_id"), (event.payload.get("commit_id") or {}).get("hex"))
        for event in events
        if event.event_type == "git_anchor_created"
    }
    patch_events = [
        event
        for event in events
        if event.event_type == "trace_patch_created"
    ]

    drafts: list[TrailEventDraft] = []
    created: list[dict[str, Any]] = []
    for patch_event in patch_events:
        patch = patch_event.payload
        trace_patch_id = patch.get("trace_patch_id")
        if not trace_patch_id or (trace_patch_id, commit) in existing_anchor_keys:
            continue
        match = _find_exact_anchor(patch, hunks)
        anchor_payload = None
        created_anchor_ids: list[str] = []
        if match:
            blob_id = _oid(repo, f"{commit}:{match['path']}")
            git_anchor_id = _id(
                "gitanchor",
                {
                    "trace_patch_id": trace_patch_id,
                    "commit_id": commit_id,
                    "path": match["path"],
                    "range": match["range"],
                    "evidence_tier": "exact_range_hash",
                },
            )
            created_anchor_ids = [git_anchor_id]
            anchor_payload = {
                "git_anchor_id": git_anchor_id,
                "trace_patch_id": trace_patch_id,
                "commit_id": commit_id,
                "path": match["path"],
                "range": match["range"],
                "blob_id": blob_id,
                "patch_id": _stable_patch_id(repo, commit),
                "observed_ref": commit_ref,
                "relation": "anchored_in_git",
                "evidence_tier": "exact_range_hash",
                "evidence_firmness": "firm",
                "source": writer,
                "limitations": [],
            }

        drafts.append(
            TrailEventDraft(
                event_type="git_anchor_search_completed",
                trace_id=patch_event.trace_id,
                generation_index=patch_event.generation_index,
                step_index=patch_event.step_index,
                capture_method=["post_commit_correlator"],
                payload={
                    "trace_patch_id": trace_patch_id,
                    "search_head": commit_id,
                    "algorithms_attempted": ANCHOR_ALGORITHMS_PHASE3,
                    "result": "anchored" if anchor_payload else "unknown",
                    "created_anchor_ids": created_anchor_ids,
                },
            )
        )
        if anchor_payload:
            drafts.append(
                TrailEventDraft(
                    event_type="git_anchor_created",
                    trace_id=patch_event.trace_id,
                    generation_index=patch_event.generation_index,
                    step_index=patch_event.step_index,
                    capture_method=["post_commit_correlator"],
                    payload=anchor_payload,
                )
            )
            created.append(anchor_payload)

    if drafts:
        append_event_batch(repo, drafts, writer=writer)
    return created

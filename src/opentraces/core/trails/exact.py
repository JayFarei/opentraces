"""Phase 1 exact Trace Patch to Git Anchor event creation."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from ...enrichment._shared import line_count, path_matches
from ...enrichment.attribution import _norm, _parse_diff_hunks_with_content
from .event_log import append_event_batch
from .models import GitObjectID, TrailEvent, TrailEventDraft, sha256_text


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


def _oid(repo: Path, rev_path: str) -> dict[str, str] | None:
    out = _git(repo, "rev-parse", rev_path, check=False)
    if not out:
        return None
    try:
        return GitObjectID(hex=out).model_dump(mode="json")
    except Exception:
        return None


def _id(prefix: str, material: dict[str, Any]) -> str:
    raw = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return f"{prefix}-sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _commit_tree_id(repo: Path, commit: str) -> dict[str, str]:
    return GitObjectID(hex=_git(repo, "show", "-s", "--format=%T", commit)).model_dump(mode="json")


def _parent_commit(repo: Path, commit: str) -> str | None:
    parent = _git(repo, "rev-parse", f"{commit}^", check=False)
    return parent or None


def _find_exact_range(repo: Path, commit: str, file_path: str, authored_text: str) -> dict[str, int]:
    diff = _git(repo, "show", "--format=", "--no-color", "-U3", commit)
    hunks = _parse_diff_hunks_with_content(diff)
    needle = _norm(authored_text)
    for hunk_path, file_hunks in hunks.items():
        if not path_matches(file_path, hunk_path):
            continue
        for hunk in file_hunks:
            haystack = _norm(hunk.get("added_text") or "")
            if needle and needle in haystack:
                return {
                    "start_line": int(hunk["added_start"]),
                    "end_line": int(hunk["added_end"]),
                }
    return {"start_line": 1, "end_line": line_count(authored_text)}


def append_exact_patch_trail(
    repo: Path,
    *,
    trace_id: str,
    step_index: int,
    file_path: str,
    authored_text: str,
    commit_ref: str = "HEAD",
    writer: str = "manual_attach",
    capture_method: list[str] | None = None,
    generation_index: int = 0,
) -> list[TrailEvent]:
    """Append Phase 1 snapshot, patch, and exact anchor events.

    This is intentionally narrow: it records the fixture/prototype case where a
    single authored hunk appears exactly in a later Git commit.
    """
    repo = repo.resolve()
    capture_method = capture_method or ["manual_attach"]
    commit = _git(repo, "rev-parse", commit_ref)
    parent = _parent_commit(repo, commit)
    after_tree_id = _commit_tree_id(repo, commit)
    before_tree_id = _commit_tree_id(repo, parent) if parent else after_tree_id
    before_blob_id = _oid(repo, f"{parent}:{file_path}") if parent else None
    after_blob_id = _oid(repo, f"{commit}:{file_path}")
    affected_range = _find_exact_range(repo, commit, file_path, authored_text)

    before_snapshot_id = _id(
        "snapshot",
        {
            "trace_id": trace_id,
            "generation_index": generation_index,
            "step_index": step_index,
            "role": "before",
            "tree_id": before_tree_id,
        },
    )
    after_snapshot_id = _id(
        "snapshot",
        {
            "trace_id": trace_id,
            "generation_index": generation_index,
            "step_index": step_index,
            "role": "after",
            "tree_id": after_tree_id,
        },
    )
    raw_authored_hash = sha256_text(authored_text)
    git_clean_hash = sha256_text(_norm(authored_text))
    trace_patch_id = _id(
        "tracepatch",
        {
            "trace_id": trace_id,
            "generation_index": generation_index,
            "step_index": step_index,
            "file_path": file_path,
            "affected_range": affected_range,
            "raw_authored_hash": raw_authored_hash,
            "before_blob_id": before_blob_id,
            "after_blob_id": after_blob_id,
        },
    )
    git_anchor_id = _id(
        "gitanchor",
        {
            "trace_patch_id": trace_patch_id,
            "commit_id": {"algo": "sha1", "hex": commit},
            "path": file_path,
            "range": affected_range,
            "evidence_tier": "exact_range_hash",
        },
    )

    drafts = [
        TrailEventDraft(
            event_type="trace_snapshot_created",
            trace_id=trace_id,
            generation_index=generation_index,
            step_index=step_index,
            capture_method=capture_method,
            payload={
                "snapshot_id": before_snapshot_id,
                "snapshot_role": "before",
                "tree_id": before_tree_id,
                "git_head_id": {"algo": "sha1", "hex": parent or commit},
                "capture_status": "phase1_logical",
                "limitations": ["full_snapshot_storage_deferred_phase2"],
            },
        ),
        TrailEventDraft(
            event_type="trace_snapshot_created",
            trace_id=trace_id,
            generation_index=generation_index,
            step_index=step_index,
            capture_method=capture_method,
            payload={
                "snapshot_id": after_snapshot_id,
                "snapshot_role": "after",
                "tree_id": after_tree_id,
                "git_head_id": {"algo": "sha1", "hex": commit},
                "capture_status": "phase1_logical",
                "limitations": ["full_snapshot_storage_deferred_phase2"],
            },
        ),
        TrailEventDraft(
            event_type="trace_patch_created",
            trace_id=trace_id,
            generation_index=generation_index,
            step_index=step_index,
            capture_method=capture_method,
            payload={
                "trace_patch_id": trace_patch_id,
                "snapshot_before_id": before_snapshot_id,
                "snapshot_after_id": after_snapshot_id,
                "file_path": file_path,
                "affected_range": affected_range,
                "authored_text": authored_text,
                "raw_authored_hash": raw_authored_hash,
                "git_clean_hash": git_clean_hash,
                "before_blob_id": before_blob_id,
                "after_blob_id": after_blob_id,
                "limitations": [],
            },
        ),
        TrailEventDraft(
            event_type="git_anchor_created",
            trace_id=trace_id,
            generation_index=generation_index,
            step_index=step_index,
            capture_method=["manual_attach"] if capture_method == ["manual_attach"] else capture_method,
            payload={
                "git_anchor_id": git_anchor_id,
                "trace_patch_id": trace_patch_id,
                "commit_id": {"algo": "sha1", "hex": commit},
                "path": file_path,
                "range": affected_range,
                "blob_id": after_blob_id,
                "relation": "anchored_in_git",
                "evidence_tier": "exact_range_hash",
                "evidence_firmness": "firm",
                "source": writer,
                "limitations": [],
            },
        ),
    ]
    return append_event_batch(repo, drafts, writer=writer)

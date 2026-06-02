"""Delayed Git Anchor reconciliation for Trace Trails."""

from __future__ import annotations

import difflib
import subprocess
from pathlib import Path
from typing import Any

from ...enrichment._shared import path_matches
from ...enrichment.attribution import _norm, _parse_diff_hunks_with_content
from .event_log import append_event_batch, read_events_scoped
from .ids import (
    GIT_ANCHOR_CANONICALIZATION,
    content_ref,
    git_anchor_ref,
    id_from_payload,
    trace_patch_ref,
)
from .models import ATTRIBUTION_VERSION, GitObjectID, TrailEventDraft

ANCHOR_ALGORITHMS_PHASE3 = ["exact_range_hash"]
ANCHOR_ALGORITHMS_PHASE5 = ["exact_range_hash", "structural_match"]
STRUCTURAL_MATCH_THRESHOLD = 0.85


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


def _find_exact_anchor(
    patch: dict[str, Any], hunks: dict[str, list[dict]]
) -> dict[str, Any] | None:
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


def _find_structural_anchor(
    patch: dict[str, Any], hunks: dict[str, list[dict]]
) -> dict[str, Any] | None:
    """Phase 5 structural fallback: line-level similarity over the same path.

    When the exact-range matcher fails (because a formatter rewrote
    non-whitespace characters such as quote style, parentheses, or
    operator spacing), this fallback compares the patch's authored text
    against each hunk's added text using ``difflib.SequenceMatcher``.

    Threshold ``STRUCTURAL_MATCH_THRESHOLD`` is tuned to accept
    quote-style and minor refactor changes while rejecting unrelated
    lines. Real AST-aware structural matching is future work; this is
    the substrate path so the evidence tier is wired correctly when
    better algorithms land.

    Returns a match dict with ``path``, ``range``, and ``similarity``
    fields, or ``None`` when no hunk in the same file scores above
    threshold.
    """
    file_path = patch.get("file_path")
    authored = patch.get("authored_text") or ""
    if not file_path or not authored.strip():
        return None
    best: dict[str, Any] | None = None
    best_score = 0.0
    for hunk_path, file_hunks in hunks.items():
        if not path_matches(file_path, hunk_path):
            continue
        for hunk in file_hunks:
            added = hunk.get("added_text") or ""
            if not added.strip():
                continue
            score = difflib.SequenceMatcher(None, authored, added, autojunk=False).ratio()
            if score >= STRUCTURAL_MATCH_THRESHOLD and score > best_score:
                best = {
                    "path": hunk_path,
                    "range": {
                        "start_line": hunk.get("added_start"),
                        "end_line": hunk.get("added_end"),
                    },
                    "similarity": round(score, 4),
                }
                best_score = score
    return best


def reconcile_commit_anchors(
    repo: Path,
    commit_ref: str = "HEAD",
    *,
    writer: str = "post-commit-correlator",
    capture_method: list[str] | None = None,
    trace_id: str | None = None,
    attribution_version: str | None = None,
) -> list[dict[str, Any]]:
    """Search existing Trace Patches against a commit and append anchor events.

    The post-commit correlator (Phase 3) calls this with the default
    ``capture_method=["post_commit_correlator"]`` and no trace filter.
    Phase 5's ``trail attach`` wraps this with
    ``capture_method=["manual_attach"]`` and a ``trace_id`` filter so the
    user can retroactively connect one trace's evidence to one commit
    without rewriting source events for other traces.
    """
    repo = repo.resolve()
    effective_capture_method = (
        list(capture_method) if capture_method else ["post_commit_correlator"]
    )
    effective_attribution_version = attribution_version or ATTRIBUTION_VERSION
    commit = _git(repo, "rev-parse", commit_ref)
    commit_id = {"algo": "sha1", "hex": commit}
    diff = _git(repo, "show", "--format=", "--no-color", "-U3", commit)
    hunks = _parse_diff_hunks_with_content(diff)
    # Bug B: this reconciler needs only 3 event types, and the anchor/search
    # dedup only consults events referencing THIS commit. Read that scoped slice
    # (streamed per-commit, no whole-log materialisation, no verify) instead of
    # the full ~N-event history that drove the post-commit hook to ~7.5GB RSS.
    events = read_events_scoped(
        repo,
        event_types={
            "trace_patch_created",
            "git_anchor_created",
            "git_anchor_search_completed",
        },
        commit_filter={
            "git_anchor_created": "commit_id",
            "git_anchor_search_completed": "search_head",
        },
        commit_sha=commit,
    )
    existing_anchor_keys = {
        (id_from_payload(event.payload, "trace_patch"), (event.payload.get("commit_id") or {}).get("hex"))
        for event in events
        if event.event_type == "git_anchor_created"
    }
    existing_search_keys = {
        (
            id_from_payload(event.payload, "trace_patch"),
            (event.payload.get("search_head") or {}).get("hex"),
            event.ATTRIBUTION_VERSION,
        )
        for event in events
        if event.event_type == "git_anchor_search_completed"
    }
    patch_events = [
        event
        for event in events
        if event.event_type == "trace_patch_created"
        and (trace_id is None or event.trace_id == trace_id)
    ]

    drafts: list[TrailEventDraft] = []
    created: list[dict[str, Any]] = []
    for patch_event in patch_events:
        patch = patch_event.payload
        trace_patch_id = id_from_payload(patch, "trace_patch")
        if not trace_patch_id:
            continue
        if (trace_patch_id, commit) in existing_anchor_keys:
            continue
        if (trace_patch_id, commit, effective_attribution_version) in existing_search_keys:
            # A prior search for this (patch, commit) already recorded a
            # result under the same attribution version; don't re-emit a
            # duplicate search event. Newer attribution versions are allowed
            # to append a new search so periodic re-search remains possible.
            continue
        match = _find_exact_anchor(patch, hunks)
        evidence_tier = "exact_range_hash"
        evidence_firmness = "firm"
        anchor_limitations: list[str] = []
        if match is None:
            structural = _find_structural_anchor(patch, hunks)
            if structural is not None:
                match = {
                    "path": structural["path"],
                    "range": structural["range"],
                }
                evidence_tier = "structural_match"
                evidence_firmness = "provisional"
                anchor_limitations.append("structural_match_below_exact_threshold")
        anchor_payload = None
        created_anchor_ids: list[str] = []
        if match:
            blob_id = _oid(repo, f"{commit}:{match['path']}")
            git_anchor_object_ref = content_ref(
                kind="git_anchor",
                canonicalization=GIT_ANCHOR_CANONICALIZATION,
                relation="anchored_in_git",
                material={
                    "trace_patch_ref": trace_patch_ref(trace_patch_id),
                    "commit_id": commit_id,
                    "path": match["path"],
                    "range": match["range"],
                    "evidence_tier": evidence_tier,
                },
            )
            git_anchor_id = git_anchor_object_ref["id"]
            created_anchor_ids = [git_anchor_id]
            anchor_payload = {
                "git_anchor_id": git_anchor_id,
                "git_anchor_ref": git_anchor_ref(git_anchor_id),
                "trace_patch_id": trace_patch_id,
                "trace_patch_ref": trace_patch_ref(trace_patch_id),
                "commit_id": commit_id,
                "path": match["path"],
                "range": match["range"],
                "blob_id": blob_id,
                "patch_id": _stable_patch_id(repo, commit),
                "observed_ref": commit_ref,
                "relation": "anchored_in_git",
                "evidence_tier": evidence_tier,
                "evidence_firmness": evidence_firmness,
                "source": writer,
                "limitations": anchor_limitations,
            }

        drafts.append(
            TrailEventDraft(
                event_type="git_anchor_search_completed",
                trace_id=patch_event.trace_id,
                generation_index=patch_event.generation_index,
                step_index=patch_event.step_index,
                capture_method=effective_capture_method,
                ATTRIBUTION_VERSION=effective_attribution_version,
                payload={
                    "trace_patch_id": trace_patch_id,
                    "trace_patch_ref": trace_patch_ref(trace_patch_id),
                    "search_head": commit_id,
                    "algorithms_attempted": ANCHOR_ALGORITHMS_PHASE5,
                    "result": "anchored" if anchor_payload else "unknown",
                    "created_anchor_ids": created_anchor_ids,
                },
            )
        )
        existing_search_keys.add(
            (trace_patch_id, commit, effective_attribution_version)
        )
        if anchor_payload:
            drafts.append(
                TrailEventDraft(
                    event_type="git_anchor_created",
                    trace_id=patch_event.trace_id,
                    generation_index=patch_event.generation_index,
                    step_index=patch_event.step_index,
                    capture_method=effective_capture_method,
                    ATTRIBUTION_VERSION=effective_attribution_version,
                    payload=anchor_payload,
                )
            )
            existing_anchor_keys.add((trace_patch_id, commit))
            created.append(anchor_payload)

    if drafts:
        append_event_batch(repo, drafts, writer=writer)
    return created

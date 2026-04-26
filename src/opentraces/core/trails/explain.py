"""Rebuild Trace Trail explanations from TrailEvents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .event_log import EVENT_LOG_REF, read_events
from .models import TrailEvent
from .slices import resource_refs_for_patch, trace_slice_for_event


def _source_event(event: TrailEvent) -> dict[str, Any]:
    out = {
        "event_id": event.event_id,
        "event_sequence": event.event_sequence,
        "event_type": event.event_type,
        "capture_method": event.capture_method,
    }
    if event.event_type == "git_anchor_search_completed":
        out["result"] = event.payload.get("result")
        out["trace_patch_id"] = event.payload.get("trace_patch_id")
    return out


def _git_anchor_view(
    anchor: dict[str, Any],
    *,
    containing_segment_id: str | None = None,
) -> dict[str, Any]:
    evidence_tier = anchor.get("evidence_tier") or "unknown"
    evidence_firmness = anchor.get("evidence_firmness") or "unknown"
    out = {
        "git_anchor_id": anchor.get("git_anchor_id"),
        "commit_id": anchor.get("commit_id"),
        "commit_sha": (anchor.get("commit_id") or {}).get("hex"),
        "path": anchor.get("path"),
        "range": anchor.get("range"),
        "blob_id": anchor.get("blob_id"),
        "evidence_tier": evidence_tier,
        "evidence_firmness": evidence_firmness,
        "limitations": anchor.get("limitations") or [],
    }
    if containing_segment_id is not None:
        out["containing_segment_id"] = containing_segment_id
    return out


def explain_trace_step(repo: Path, trace_id: str, step_index: int) -> dict[str, Any]:
    """Explain a trace step by rebuilding state from the local event log."""
    events = read_events(repo)
    snapshots: dict[str, tuple[dict[str, Any], TrailEvent]] = {}
    patches: list[tuple[dict[str, Any], TrailEvent]] = []
    context_events: list[TrailEvent] = []
    anchors_by_patch: dict[str, list[tuple[dict[str, Any], TrailEvent]]] = {}

    for event in events:
        payload = event.payload
        if event.trace_id == trace_id and event.step_index == step_index:
            context_events.append(event)
        if event.event_type == "trace_snapshot_created":
            snapshots[payload["snapshot_id"]] = (payload, event)
        elif event.event_type == "trace_patch_created":
            patches.append((payload, event))
        elif event.event_type == "git_anchor_created":
            anchors_by_patch.setdefault(payload["trace_patch_id"], []).append((payload, event))

    patch_candidates = [
        (payload, event)
        for payload, event in patches
        if event.trace_id == trace_id and event.step_index == step_index
    ]
    patch_pair = (
        max(patch_candidates, key=lambda pair: pair[1].event_sequence) if patch_candidates else None
    )
    if patch_pair is None:
        context_event = context_events[-1] if context_events else None
        trace_slice = (
            trace_slice_for_event(
                context_event,
                relation="contains_no_patch_step",
            )
            if context_event
            else None
        )
        containing_segment_id = (
            trace_slice.get("containing_segment_id") if trace_slice is not None else None
        )
        if context_event is not None:
            return {
                "trace_id": trace_id,
                "step_id": f"step_{step_index}",
                "step_index": step_index,
                "generation_index": context_event.generation_index,
                "relation": "no_patch",
                "patch_status": "no_patch",
                "trace_patch_id": None,
                "git_anchor_id": None,
                "containing_segment_id": containing_segment_id,
                "trace_slice": trace_slice,
                "evidence_tier": "none",
                "evidence_firmness": "none",
                "limitations": ["no_trace_patch_event"],
                "event_log_ref": EVENT_LOG_REF,
                "source_events": [_source_event(event) for event in context_events],
            }
        return {
            "trace_id": trace_id,
            "step_id": f"step_{step_index}",
            "step_index": step_index,
            "relation": "unknown",
            "patch_status": "unknown",
            "trace_patch_id": None,
            "git_anchor_id": None,
            "evidence_tier": "unknown",
            "evidence_firmness": "unknown",
            "limitations": ["no_trace_patch_event"],
            "event_log_ref": EVENT_LOG_REF,
            "source_events": [],
        }

    patch, patch_event = patch_pair
    before_pair = snapshots.get(patch["snapshot_before_id"])
    after_pair = snapshots.get(patch["snapshot_after_id"])
    anchor_pairs = sorted(
        anchors_by_patch.get(patch["trace_patch_id"], []),
        key=lambda pair: pair[1].event_sequence,
    )
    anchor_pair = anchor_pairs[-1] if anchor_pairs else None
    source_events = []
    if before_pair:
        source_events.append(_source_event(before_pair[1]))
    if after_pair:
        source_events.append(_source_event(after_pair[1]))
    source_events.append(_source_event(patch_event))

    relation = "unknown"
    evidence_tier = "unknown"
    evidence_firmness = "unknown"
    git_anchor = None
    git_anchor_id = None
    limitations = list(patch.get("limitations") or [])
    if anchor_pair:
        anchor, anchor_event = anchor_pair
        source_events.extend(_source_event(event) for _anchor, event in anchor_pairs)
        relation = anchor.get("relation") or "anchored_in_git"
        evidence_tier = anchor.get("evidence_tier") or "unknown"
        evidence_firmness = anchor.get("evidence_firmness") or "unknown"
        git_anchor_id = anchor.get("git_anchor_id")
        git_anchor = _git_anchor_view(anchor)
        if len(anchor_pairs) > 1:
            limitations.append("multiple_candidate_commits")
    else:
        limitations.append("git_anchor_unknown")

    trace_slice = trace_slice_for_event(
        patch_event,
        trace_patch_id=patch["trace_patch_id"],
        git_anchor_id=git_anchor_id,
        relation="contains_trace_patch",
    )
    containing_segment_id = (
        trace_slice.get("containing_segment_id") if trace_slice is not None else None
    )
    git_anchors = [
        _git_anchor_view(anchor, containing_segment_id=containing_segment_id)
        for anchor, _event in anchor_pairs
    ]
    if git_anchor is not None:
        git_anchor["containing_segment_id"] = containing_segment_id
    affected_range = patch["affected_range"]
    return {
        "trace_id": trace_id,
        "step_id": f"step_{step_index}",
        "step_index": step_index,
        "generation_index": patch_event.generation_index,
        "relation": relation,
        "patch_status": "patched",
        "evidence_tier": evidence_tier,
        "evidence_firmness": evidence_firmness,
        "trace_snapshot_before": before_pair[0] if before_pair else None,
        "trace_snapshot_after": after_pair[0] if after_pair else None,
        "trace_patch_id": patch["trace_patch_id"],
        "file_path": patch["file_path"],
        "affected_range": affected_range,
        "raw_authored_hash": patch["raw_authored_hash"],
        "git_clean_hash": patch["git_clean_hash"],
        "before_blob_id": patch.get("before_blob_id"),
        "after_blob_id": patch.get("after_blob_id"),
        "git_anchor_id": git_anchor_id,
        "containing_segment_id": containing_segment_id,
        "trace_slice": trace_slice,
        "resource_refs": resource_refs_for_patch(
            trace_id=trace_id,
            trace_patch_id=patch["trace_patch_id"],
            file_path=patch.get("file_path"),
            start_line=affected_range.get("start_line"),
            git_anchor_id=git_anchor_id,
        ),
        "git_anchor": git_anchor,
        "git_anchors": git_anchors,
        "limitations": limitations,
        "event_log_ref": EVENT_LOG_REF,
        "source_events": source_events,
        "unavailable_stronger_claims": [
            "full snapshot subtree retention is deferred to Phase 2",
            "delayed anchor search is deferred to Phase 3",
        ],
    }


def explain_commit(repo: Path, commit_ref: str) -> dict[str, Any]:
    """Explain all Trace Patches anchored in a Git commit."""
    import subprocess

    proc = subprocess.run(
        ["git", "rev-parse", commit_ref],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    commit = proc.stdout.strip() if proc.returncode == 0 else commit_ref
    events = read_events(repo)
    patches_by_id: dict[str, tuple[dict[str, Any], TrailEvent]] = {}
    for event in events:
        if event.event_type == "trace_patch_created":
            patches_by_id[event.payload.get("trace_patch_id")] = (event.payload, event)

    trace_patches: list[dict[str, Any]] = []
    source_events: list[dict[str, Any]] = []
    for event in events:
        if event.event_type == "git_anchor_search_completed":
            search_head = event.payload.get("search_head") or {}
            if search_head.get("hex") == commit:
                source_events.append(_source_event(event))
        if event.event_type != "git_anchor_created":
            continue
        commit_id = event.payload.get("commit_id") or {}
        if commit_id.get("hex") != commit:
            continue
        patch_id = event.payload.get("trace_patch_id")
        patch_pair = patches_by_id.get(patch_id)
        patch_payload, patch_event = patch_pair if patch_pair else ({}, event)
        trace_slice = trace_slice_for_event(
            patch_event,
            trace_patch_id=patch_id,
            git_anchor_id=event.payload.get("git_anchor_id"),
            relation="contains_trace_patch",
        )
        containing_segment_id = (
            trace_slice.get("containing_segment_id") if trace_slice is not None else None
        )
        affected_range = patch_payload.get("affected_range") or event.payload.get("range")
        file_path = patch_payload.get("file_path") or event.payload.get("path")
        source_events.append(_source_event(event))
        trace_patches.append(
            {
                "trace_id": event.trace_id,
                "step_id": f"step_{event.step_index}" if event.step_index is not None else None,
                "step_index": event.step_index,
                "trace_patch_id": patch_id,
                "patch_status": "patched",
                "file_path": file_path,
                "affected_range": affected_range,
                "git_anchor_id": event.payload.get("git_anchor_id"),
                "containing_segment_id": containing_segment_id,
                "trace_slice": trace_slice,
                "resource_refs": resource_refs_for_patch(
                    trace_id=event.trace_id,
                    trace_patch_id=patch_id,
                    file_path=file_path,
                    start_line=(affected_range or {}).get("start_line"),
                    git_anchor_id=event.payload.get("git_anchor_id"),
                ),
                "evidence_tier": event.payload.get("evidence_tier") or "unknown",
                "evidence_firmness": event.payload.get("evidence_firmness") or "unknown",
                "source_event_id": patch_event.event_id if patch_pair else None,
            }
        )
    return {
        "commit_sha": commit,
        "trace_patches": trace_patches,
        "source_events": source_events,
        "event_log_ref": EVENT_LOG_REF,
    }


def explain_file_line(repo: Path, target: str) -> dict[str, Any]:
    """Resolve ``path:line`` through Git Anchor evidence."""
    if ":" not in target:
        raise ValueError("target must be path:line")
    path, raw_line = target.rsplit(":", 1)
    try:
        line_no = int(raw_line)
    except ValueError as exc:
        raise ValueError("target line must be an integer") from exc

    events = read_events(repo)
    patches_by_id: dict[str, tuple[dict[str, Any], TrailEvent]] = {
        event.payload.get("trace_patch_id"): (event.payload, event)
        for event in events
        if event.event_type == "trace_patch_created"
    }
    for event in reversed(events):
        if event.event_type != "git_anchor_created":
            continue
        anchor_path = event.payload.get("path")
        anchor_range = event.payload.get("range") or {}
        if anchor_path != path:
            continue
        start = anchor_range.get("start_line")
        end = anchor_range.get("end_line")
        if start is None or end is None or not (int(start) <= line_no <= int(end)):
            continue
        patch_id = event.payload.get("trace_patch_id")
        patch_pair = patches_by_id.get(patch_id)
        patch_payload, patch_event = patch_pair if patch_pair else ({}, event)
        trace_slice = trace_slice_for_event(
            patch_event,
            trace_patch_id=patch_id,
            git_anchor_id=event.payload.get("git_anchor_id"),
            relation="contains_trace_patch",
        )
        containing_segment_id = (
            trace_slice.get("containing_segment_id") if trace_slice is not None else None
        )
        affected_range = patch_payload.get("affected_range") or anchor_range
        file_path = patch_payload.get("file_path") or anchor_path
        return {
            "target": target,
            "relation": event.payload.get("relation") or "anchored_in_git",
            "containing_segment_id": containing_segment_id,
            "trace_slice": trace_slice,
            "resource_refs": resource_refs_for_patch(
                trace_id=event.trace_id,
                trace_patch_id=patch_id,
                file_path=path,
                start_line=line_no,
                git_anchor_id=event.payload.get("git_anchor_id"),
            ),
            "git_anchor": {
                **event.payload,
                "containing_segment_id": containing_segment_id,
            },
            "trace_patch": {
                "trace_id": event.trace_id,
                "step_id": f"step_{event.step_index}" if event.step_index is not None else None,
                "step_index": event.step_index,
                "trace_patch_id": patch_id,
                "patch_status": "patched",
                "file_path": file_path,
                "affected_range": affected_range,
                "containing_segment_id": containing_segment_id,
                "resource_refs": resource_refs_for_patch(
                    trace_id=event.trace_id,
                    trace_patch_id=patch_id,
                    file_path=file_path,
                    start_line=(affected_range or {}).get("start_line"),
                    git_anchor_id=event.payload.get("git_anchor_id"),
                ),
                "evidence_tier": event.payload.get("evidence_tier") or "unknown",
                "evidence_firmness": event.payload.get("evidence_firmness") or "unknown",
            },
            "source_events": [_source_event(event)],
            "event_log_ref": EVENT_LOG_REF,
        }
    return {
        "target": target,
        "relation": "unknown",
        "trace_patch": None,
        "limitations": ["no_git_anchor_for_line"],
        "source_events": [],
        "event_log_ref": EVENT_LOG_REF,
    }

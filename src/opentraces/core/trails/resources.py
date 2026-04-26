"""Stable ``ot://`` resource resolution for Trace Trails."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .event_log import EVENT_LOG_REF, read_events
from .follow import follow_anchor, follow_patch
from .models import TrailEvent
from .slices import (
    resource_refs_for_patch,
    trace_slice_for_event,
)


def _source_event(event: TrailEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_sequence": event.event_sequence,
        "event_type": event.event_type,
        "capture_method": event.capture_method,
    }


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


def _trace_patch_view(
    patch: dict[str, Any],
    event: TrailEvent,
    *,
    containing_segment_id: str | None = None,
    git_anchor_id: str | None = None,
) -> dict[str, Any]:
    affected_range = patch.get("affected_range") or {}
    out = {
        "trace_id": event.trace_id,
        "step_id": f"step_{event.step_index}" if event.step_index is not None else None,
        "step_index": event.step_index,
        "generation_index": event.generation_index,
        "trace_patch_id": patch.get("trace_patch_id"),
        "file_path": patch.get("file_path"),
        "affected_range": affected_range,
        "patch_status": "patched",
        "resource_refs": resource_refs_for_patch(
            trace_id=event.trace_id,
            trace_patch_id=patch.get("trace_patch_id"),
            file_path=patch.get("file_path"),
            start_line=affected_range.get("start_line"),
            git_anchor_id=git_anchor_id,
        ),
    }
    if containing_segment_id is not None:
        out["containing_segment_id"] = containing_segment_id
    return out


def _latest_anchor_id(anchors: list[tuple[dict[str, Any], TrailEvent]]) -> str | None:
    if not anchors:
        return None
    return anchors[-1][0].get("git_anchor_id")


def _index_events(
    events: list[TrailEvent],
) -> tuple[
    dict[str, tuple[dict[str, Any], TrailEvent]],
    dict[str, tuple[dict[str, Any], TrailEvent]],
    dict[str, list[tuple[dict[str, Any], TrailEvent]]],
]:
    patches: dict[str, tuple[dict[str, Any], TrailEvent]] = {}
    anchors_by_id: dict[str, tuple[dict[str, Any], TrailEvent]] = {}
    anchors_by_patch: dict[str, list[tuple[dict[str, Any], TrailEvent]]] = {}
    for event in events:
        if event.event_type == "trace_patch_created":
            patch_id = event.payload.get("trace_patch_id")
            if patch_id:
                patches[patch_id] = (event.payload, event)
        elif event.event_type == "git_anchor_created":
            anchor_id = event.payload.get("git_anchor_id")
            patch_id = event.payload.get("trace_patch_id")
            if anchor_id:
                anchors_by_id[anchor_id] = (event.payload, event)
            if patch_id:
                anchors_by_patch.setdefault(patch_id, []).append((event.payload, event))
    for anchor_pairs in anchors_by_patch.values():
        anchor_pairs.sort(key=lambda pair: pair[1].event_sequence)
    return patches, anchors_by_id, anchors_by_patch


def _unknown_resource(resource: str, resource_type: str, limitation: str) -> dict[str, Any]:
    return {
        "resource": resource,
        "resource_type": resource_type,
        "relation": "unknown",
        "limitations": [limitation],
        "event_log_ref": EVENT_LOG_REF,
        "source_events": [],
    }


def _resolve_trace_patch_trail(repo: Path, resource: str, segments: list[str]) -> dict[str, Any]:
    if len(segments) != 4 or segments[1] != "patches" or segments[3] != "trail":
        raise ValueError("trace resource must be ot://trace/<trace_id>/patches/<id>/trail")
    trace_id = segments[0]
    trace_patch_id = segments[2]
    events = read_events(repo)
    patches, _anchors_by_id, anchors_by_patch = _index_events(events)
    patch_pair = patches.get(trace_patch_id)
    if patch_pair is None or patch_pair[1].trace_id != trace_id:
        payload = _unknown_resource(resource, "trace_patch_trail", "no_trace_patch_event")
        payload.update({"trace_id": trace_id, "trace_patch_id": trace_patch_id})
        return payload

    patch, patch_event = patch_pair
    anchors = anchors_by_patch.get(trace_patch_id, [])
    git_anchor_id = _latest_anchor_id(anchors)
    trace_slice = trace_slice_for_event(
        patch_event,
        trace_patch_id=trace_patch_id,
        git_anchor_id=git_anchor_id,
        relation="contains_trace_patch",
    )
    containing_segment_id = (
        trace_slice.get("containing_segment_id") if trace_slice is not None else None
    )
    return {
        "resource": resource,
        "resource_type": "trace_patch_trail",
        "relation": "trace_patch_trail_resolved",
        "trace_id": trace_id,
        "trace_patch_id": trace_patch_id,
        "patch_status": "patched",
        "containing_segment_id": containing_segment_id,
        "trace_slice": trace_slice,
        "trace_patch": _trace_patch_view(
            patch,
            patch_event,
            containing_segment_id=containing_segment_id,
            git_anchor_id=git_anchor_id,
        ),
        "git_anchors": [
            _git_anchor_view(anchor, containing_segment_id=containing_segment_id)
            for anchor, _event in anchors
        ],
        "trail": follow_patch(repo, trace_patch_id),
        "event_log_ref": EVENT_LOG_REF,
        "source_events": [_source_event(patch_event)]
        + [_source_event(event) for _anchor, event in anchors],
    }


def _resolve_git_anchor(repo: Path, resource: str, segments: list[str]) -> dict[str, Any]:
    if len(segments) != 1:
        raise ValueError("git anchor resource must be ot://git-anchor/<git_anchor_id>")
    git_anchor_id = segments[0]
    events = read_events(repo)
    patches, anchors_by_id, _anchors_by_patch = _index_events(events)
    anchor_pair = anchors_by_id.get(git_anchor_id)
    if anchor_pair is None:
        payload = _unknown_resource(resource, "git_anchor", "no_git_anchor_event")
        payload["git_anchor_id"] = git_anchor_id
        return payload

    anchor, anchor_event = anchor_pair
    trace_patch_id = anchor.get("trace_patch_id")
    patch_pair = patches.get(trace_patch_id or "")
    trace_slice = trace_slice_for_event(
        anchor_event,
        trace_patch_id=trace_patch_id,
        git_anchor_id=git_anchor_id,
        relation="contains_git_anchor",
    )
    containing_segment_id = (
        trace_slice.get("containing_segment_id") if trace_slice is not None else None
    )
    response = {
        "resource": resource,
        "resource_type": "git_anchor",
        "relation": "git_anchor_resolved",
        "git_anchor_id": git_anchor_id,
        "trace_patch_id": trace_patch_id,
        "containing_segment_id": containing_segment_id,
        "trace_slice": trace_slice,
        "git_anchor": _git_anchor_view(anchor, containing_segment_id=containing_segment_id),
        "trail": follow_anchor(repo, git_anchor_id),
        "event_log_ref": EVENT_LOG_REF,
        "source_events": [_source_event(anchor_event)],
    }
    if patch_pair:
        patch, patch_event = patch_pair
        response["trace_patch"] = _trace_patch_view(
            patch,
            patch_event,
            containing_segment_id=containing_segment_id,
            git_anchor_id=git_anchor_id,
        )
        response["source_events"].insert(0, _source_event(patch_event))
    else:
        response["trace_patch"] = None
    return response


def _parse_file_line_origin(segments: list[str]) -> tuple[str, int]:
    if len(segments) < 4 or segments[-3] != "line" or segments[-1] != "origin":
        raise ValueError("file resource must be ot://file/<path>/line/<n>/origin")
    path = "/".join(segments[:-3])
    if not path:
        raise ValueError("file resource path is empty")
    try:
        line_no = int(segments[-2])
    except ValueError as exc:
        raise ValueError("file resource line must be an integer") from exc
    return path, line_no


def _resolve_file_line_origin(repo: Path, resource: str, segments: list[str]) -> dict[str, Any]:
    path, line_no = _parse_file_line_origin(segments)
    events = read_events(repo)
    patches, _anchors_by_id, _anchors_by_patch = _index_events(events)
    for event in reversed(events):
        if event.event_type != "git_anchor_created":
            continue
        anchor = event.payload
        anchor_range = anchor.get("range") or {}
        start = anchor_range.get("start_line")
        end = anchor_range.get("end_line")
        if anchor.get("path") != path:
            continue
        if start is None or end is None or not (int(start) <= line_no <= int(end)):
            continue
        trace_patch_id = anchor.get("trace_patch_id")
        patch_pair = patches.get(trace_patch_id or "")
        trace_slice = trace_slice_for_event(
            event,
            trace_patch_id=trace_patch_id,
            git_anchor_id=anchor.get("git_anchor_id"),
            relation="contains_file_line_origin",
        )
        containing_segment_id = (
            trace_slice.get("containing_segment_id") if trace_slice is not None else None
        )
        response = {
            "resource": resource,
            "resource_type": "file_line_origin",
            "target": f"{path}:{line_no}",
            "relation": anchor.get("relation") or "anchored_in_git",
            "containing_segment_id": containing_segment_id,
            "trace_slice": trace_slice,
            "git_anchor": _git_anchor_view(
                anchor,
                containing_segment_id=containing_segment_id,
            ),
            "event_log_ref": EVENT_LOG_REF,
            "source_events": [_source_event(event)],
        }
        if patch_pair:
            patch, patch_event = patch_pair
            response["trace_patch"] = _trace_patch_view(
                patch,
                patch_event,
                containing_segment_id=containing_segment_id,
                git_anchor_id=anchor.get("git_anchor_id"),
            )
            response["source_events"].insert(0, _source_event(patch_event))
        else:
            response["trace_patch"] = None
        return response

    payload = _unknown_resource(resource, "file_line_origin", "no_git_anchor_for_line")
    payload["target"] = f"{path}:{line_no}"
    payload["trace_patch"] = None
    return payload


def resolve_resource(repo: Path, resource: str) -> dict[str, Any]:
    """Resolve a stable ``ot://`` Trace Trails resource URI to JSON."""
    parsed = urlparse(resource)
    if parsed.scheme != "ot":
        raise ValueError("resource must use the ot:// scheme")
    segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
    if parsed.netloc == "trace":
        return _resolve_trace_patch_trail(repo, resource, segments)
    if parsed.netloc == "git-anchor":
        return _resolve_git_anchor(repo, resource, segments)
    if parsed.netloc == "file":
        return _resolve_file_line_origin(repo, resource, segments)
    raise ValueError(f"unsupported ot:// resource type: {parsed.netloc}")

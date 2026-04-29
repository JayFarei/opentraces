"""Stable ``ot://`` resource resolution for Trace Trails."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .contract import (
    RESOLVE_SCHEMA_VERSION,
    current_state_object,
    enrich_trail_row,
    evidence_object,
    landing_object,
    lineage_key,
    typed_limitations,
)
from .event_log import EVENT_LOG_REF, read_events
from .sync import sync_anchor, sync_patch
from .ids import (
    git_anchor_ref,
    git_commit_ref,
    id_from_payload,
    normalize_id,
    trace_patch_ref,
    trace_ref,
    trace_step_ref,
)
from .models import TrailEvent
from .slices import (
    resource_ref_for_git_anchor,
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
    anchor_id = id_from_payload(anchor, "git_anchor")
    out = {
        "git_anchor_id": anchor_id,
        "git_anchor_ref": anchor.get("git_anchor_ref")
        or (git_anchor_ref(anchor_id) if anchor_id else None),
        "commit_id": anchor.get("commit_id"),
        "commit_sha": (anchor.get("commit_id") or {}).get("hex"),
        "commit_ref": git_commit_ref(anchor.get("commit_id")),
        "path": anchor.get("path"),
        "range": anchor.get("range"),
        "blob_id": anchor.get("blob_id"),
        "relation": anchor.get("relation") or "anchored_in_git",
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
    trace_patch_id = id_from_payload(patch, "trace_patch")
    out = {
        "trace_id": event.trace_id,
        "step_id": f"step_{event.step_index}" if event.step_index is not None else None,
        "step_index": event.step_index,
        "generation_index": event.generation_index,
        "trace_patch_id": trace_patch_id,
        "trace_patch_ref": patch.get("trace_patch_ref")
        or (trace_patch_ref(trace_patch_id) if trace_patch_id else None),
        "file_path": patch.get("file_path"),
        "affected_range": affected_range,
        "patch_status": "patched",
        "resource_refs": resource_refs_for_patch(
            trace_id=event.trace_id,
            trace_patch_id=trace_patch_id,
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
    return id_from_payload(anchors[-1][0], "git_anchor")


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
            patch_id = id_from_payload(event.payload, "trace_patch")
            if patch_id:
                patches[patch_id] = (event.payload, event)
        elif event.event_type == "git_anchor_created":
            anchor_id = id_from_payload(event.payload, "git_anchor")
            patch_id = id_from_payload(event.payload, "trace_patch")
            if anchor_id:
                anchors_by_id[anchor_id] = (event.payload, event)
            if patch_id:
                anchors_by_patch.setdefault(patch_id, []).append((event.payload, event))
    for anchor_pairs in anchors_by_patch.values():
        anchor_pairs.sort(key=lambda pair: pair[1].event_sequence)
    return patches, anchors_by_id, anchors_by_patch


def _unknown_resource(resource: str, resource_type: str, limitation: str) -> dict[str, Any]:
    return {
        "schema_version": RESOLVE_SCHEMA_VERSION,
        "resource": resource,
        "resource_type": resource_type,
        "relation": "unknown",
        "limitations": [limitation],
        "limitation_details": typed_limitations([limitation]),
        "event_log_ref": EVENT_LOG_REF,
        "source_events": [],
        "nodes": [],
        "edges": [],
    }


def _resource_row(
    *,
    trace_patch: dict[str, Any] | None,
    git_anchor: dict[str, Any] | None,
    trail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {}
    if trace_patch:
        row.update(trace_patch)
    if git_anchor:
        row.update(git_anchor)
        row["file_path"] = git_anchor.get("path") or row.get("file_path")
        row["git_anchor_id"] = id_from_payload(git_anchor, "git_anchor")
        row["git_anchor_ref"] = git_anchor.get("git_anchor_ref")
        row["commit_sha"] = git_anchor.get("commit_sha")
    if trace_patch:
        row["trace_id"] = trace_patch.get("trace_id")
        row["step_id"] = trace_patch.get("step_id")
        row["step_index"] = trace_patch.get("step_index")
        row["generation_index"] = trace_patch.get("generation_index")
        row["trace_patch_id"] = id_from_payload(trace_patch, "trace_patch")
        row["trace_patch_ref"] = trace_patch.get("trace_patch_ref")
        row["affected_range"] = trace_patch.get("affected_range")
        row["resource_refs"] = trace_patch.get("resource_refs") or {}
    if git_anchor and id_from_payload(git_anchor, "git_anchor"):
        refs = dict(row.get("resource_refs") or {})
        refs["git_anchor"] = resource_ref_for_git_anchor(id_from_payload(git_anchor, "git_anchor"))
        row["resource_refs"] = refs
    if trail:
        current = trail.get("current_survival") or {}
        row["current_survival"] = current
        row["survival_state"] = current.get("survival_state") or "unknown"
        row["trail_limitations"] = trail.get("trail_limitations") or []
    return enrich_trail_row(row)


def _add_graph_contract(payload: dict[str, Any]) -> dict[str, Any]:
    trail = payload.get("trail") or {}
    trace_patch = payload.get("trace_patch")
    anchors = payload.get("git_anchors")
    if anchors is None:
        anchor = payload.get("git_anchor")
        anchors = [anchor] if anchor else []
    latest_anchor = anchors[-1] if anchors else None
    row = _resource_row(
        trace_patch=trace_patch,
        git_anchor=latest_anchor,
        trail=trail,
    )
    payload["schema_version"] = RESOLVE_SCHEMA_VERSION
    payload["lineage_key"] = lineage_key(row)
    payload["landing"] = landing_object(row)
    payload["evidence"] = evidence_object(row)
    payload["current_state"] = current_state_object(row)
    payload["limitation_details"] = typed_limitations(
        list(payload.get("limitations") or [])
        + list(row.get("trail_limitations") or [])
    )
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    trace_id = row.get("trace_id") or payload.get("trace_id")
    step_id = row.get("step_id")
    trace_patch_id = row.get("trace_patch_id") or payload.get("trace_patch_id")
    refs: dict[str, dict[str, Any]] = {}
    if trace_id:
        ref = trace_ref(trace_id)
        nodes.append({"ref": ref["ref"], "kind": "trace", "display_id": ref["display_id"]})
        refs[ref["ref"]] = ref
    if trace_id and step_id:
        step_ref = trace_step_ref(trace_id, step_id, index=row.get("step_index"))
        nodes.append(
            {
                "ref": step_ref["ref"],
                "kind": "trace_step",
                "display_id": step_ref["display_id"],
                "trace_id": trace_id,
                "step_id": step_id,
                "step_index": row.get("step_index"),
            }
        )
        refs[step_ref["ref"]] = step_ref
        edges.append(
            {
                "from": trace_ref(trace_id)["ref"],
                "to": step_ref["ref"],
                "relation": "contains_step",
            }
        )
    if trace_patch_id:
        patch_ref = trace_patch_ref(trace_patch_id)
        nodes.append(
            {
                "ref": patch_ref["ref"],
                "kind": "trace_patch",
                "display_id": patch_ref["display_id"],
                "trace_patch_id": trace_patch_id,
                "file_path": row.get("file_path") or row.get("path"),
                "resource_ref": (row.get("resource_refs") or {}).get("trace_patch_trail")
                or f"{patch_ref['ref']}/trail",
            }
        )
        refs[patch_ref["ref"]] = patch_ref
        source = (
            trace_step_ref(trace_id, step_id, index=row.get("step_index"))["ref"]
            if trace_id and step_id
            else trace_ref(trace_id)["ref"]
        )
        if trace_id:
            edges.append(
                {
                    "from": source,
                    "to": patch_ref["ref"],
                    "relation": "created_trace_patch",
                }
            )
    for anchor in anchors:
        if not anchor:
            continue
        anchor_row = _resource_row(trace_patch=trace_patch, git_anchor=anchor, trail=trail)
        anchor_id = id_from_payload(anchor, "git_anchor")
        commit_sha = anchor.get("commit_sha")
        if anchor_id:
            anchor_ref = git_anchor_ref(anchor_id, relation=anchor.get("relation") or "anchored_in_git")
            nodes.append(
                {
                    "ref": anchor_ref["ref"],
                    "kind": "git_anchor",
                    "display_id": anchor_ref["display_id"],
                    "git_anchor_id": anchor_id,
                    "commit_sha": commit_sha,
                    "resource_ref": resource_ref_for_git_anchor(anchor_id),
                }
            )
            refs[anchor_ref["ref"]] = anchor_ref
            if trace_patch_id:
                edges.append(
                    {
                        "from": trace_patch_ref(trace_patch_id)["ref"],
                        "to": anchor_ref["ref"],
                        "relation": anchor.get("relation") or "anchored_in_git",
                        "evidence": evidence_object(anchor_row),
                    }
                )
        if commit_sha:
            commit_ref = git_commit_ref(commit_sha)
            nodes.append(
                {
                    "ref": commit_ref["ref"] if commit_ref else commit_sha,
                    "kind": "git_commit",
                    "display_id": (commit_ref or {}).get("display_id"),
                    "commit_sha": commit_sha,
                }
            )
            if commit_ref:
                refs[commit_ref["ref"]] = commit_ref
            if anchor_id:
                edges.append(
                    {
                        "from": git_anchor_ref(anchor_id)["ref"],
                        "to": commit_ref["ref"] if commit_ref else commit_sha,
                        "relation": "landed_in_commit",
                    }
                )
    payload["nodes"] = _dedupe_nodes(nodes)
    payload["edges"] = edges
    payload["refs"] = refs
    return payload


def _dedupe_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for node in nodes:
        node_id = node.get("ref")
        if not isinstance(node_id, str) or node_id in seen:
            continue
        seen.add(node_id)
        out.append(node)
    return out


def _resolve_trace_patch_trail(repo: Path, resource: str, segments: list[str]) -> dict[str, Any]:
    if len(segments) != 4 or segments[1] != "patches" or segments[3] != "trail":
        raise ValueError("trace patch resource must be ot://trace-patch/sha256/<id>/trail")
    trace_id = segments[0]
    trace_patch_id = normalize_id(segments[2])
    return _resolve_trace_patch_trail_by_id(
        repo,
        resource,
        trace_patch_id=trace_patch_id,
        expected_trace_id=trace_id,
    )


def _resolve_trace_patch_trail_by_id(
    repo: Path,
    resource: str,
    *,
    trace_patch_id: str,
    expected_trace_id: str | None = None,
) -> dict[str, Any]:
    events = read_events(repo)
    patches, _anchors_by_id, anchors_by_patch = _index_events(events)
    patch_pair = patches.get(trace_patch_id)
    if patch_pair is None or (
        expected_trace_id is not None and patch_pair[1].trace_id != expected_trace_id
    ):
        payload = _unknown_resource(resource, "trace_patch_trail", "no_trace_patch_event")
        payload.update({"trace_id": expected_trace_id, "trace_patch_id": trace_patch_id})
        return payload

    patch, patch_event = patch_pair
    trace_id = patch_event.trace_id
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
    response = {
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
        "trail": sync_patch(repo, trace_patch_id),
        "event_log_ref": EVENT_LOG_REF,
        "source_events": [_source_event(patch_event)]
        + [_source_event(event) for _anchor, event in anchors],
    }
    return _add_graph_contract(response)


def _resolve_git_anchor(repo: Path, resource: str, segments: list[str]) -> dict[str, Any]:
    if len(segments) == 1:
        git_anchor_id = normalize_id(segments[0])
    elif len(segments) == 2:
        _algorithm, raw_id = segments
        git_anchor_id = normalize_id(raw_id)
    else:
        raise ValueError("git anchor resource must be ot://git-anchor/sha256/<git_anchor_id>")
    events = read_events(repo)
    patches, anchors_by_id, _anchors_by_patch = _index_events(events)
    anchor_pair = anchors_by_id.get(git_anchor_id)
    if anchor_pair is None:
        payload = _unknown_resource(resource, "git_anchor", "no_git_anchor_event")
        payload["git_anchor_id"] = git_anchor_id
        return payload

    anchor, anchor_event = anchor_pair
    trace_patch_id = id_from_payload(anchor, "trace_patch")
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
        "trail": sync_anchor(repo, git_anchor_id),
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
    return _add_graph_contract(response)


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
        trace_patch_id = id_from_payload(anchor, "trace_patch")
        git_anchor_id = id_from_payload(anchor, "git_anchor")
        patch_pair = patches.get(trace_patch_id or "")
        trace_slice = trace_slice_for_event(
            event,
            trace_patch_id=trace_patch_id,
            git_anchor_id=git_anchor_id,
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
                git_anchor_id=git_anchor_id,
            )
            response["source_events"].insert(0, _source_event(patch_event))
        else:
            response["trace_patch"] = None
        return _add_graph_contract(response)

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
    if parsed.netloc == "trace-patch":
        if len(segments) not in {2, 3}:
            raise ValueError("trace patch resource must be ot://trace-patch/sha256/<id>[/trail]")
        _algorithm, trace_patch_id, *rest = segments
        if rest and rest != ["trail"]:
            raise ValueError("trace patch resource must end with /trail when extra segments are used")
        return _resolve_trace_patch_trail_by_id(
            repo,
            resource,
            trace_patch_id=normalize_id(trace_patch_id),
        )
    if parsed.netloc == "git-anchor":
        return _resolve_git_anchor(repo, resource, segments)
    if parsed.netloc == "file":
        return _resolve_file_line_origin(repo, resource, segments)
    raise ValueError(f"unsupported ot:// resource type: {parsed.netloc}")

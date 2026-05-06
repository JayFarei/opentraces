"""Trace Slice projections for trace-aware dataset workflows.

Slices are mechanical projections over a Trace Map and, when available,
the backing TraceRecord. They deliberately stay workflow-neutral:
templates here are deterministic selection strategies such as "bursts",
not Markdown instructions.
"""

from __future__ import annotations

from typing import Any

from opentraces_schema import TraceMap, TraceMapEdge, TraceMapNode

from .bursts import DEFAULT_BURST_GAP, detect_bursts
from .trails.slices import trace_slice_id_for


SLICE_TEMPLATES: tuple[str, ...] = ("bursts",)


def slice_by_steps(
    trace_map: TraceMap,
    record: Any | None,
    *,
    start_step_index: int,
    end_step_index: int,
    source: str = "manual_step_range",
    template: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a JSON-friendly Trace Slice payload for a step window."""

    start, end = _normalise_step_range(start_step_index, end_step_index)
    selected_nodes = [
        node
        for node in trace_map.nodes
        if _node_overlaps_step_range(node, start, end)
    ]
    selected_map = _submap(trace_map, selected_nodes)
    steps = _steps_for_range(record, start, end)
    generation_index = _generation_index(record)
    slice_id = trace_slice_id_for(
        trace_id=trace_map.trace_id,
        generation_index=generation_index,
        start_step_index=start,
        end_step_index=end,
        source=source,
    )
    limitations = list(selected_map.limitations)
    if record is None:
        limitations.append("trace_record_unavailable")

    return {
        "slice_id": slice_id,
        "trace_id": trace_map.trace_id,
        "generation_index": generation_index,
        "source": source,
        "template": template,
        "start_step_index": start,
        "end_step_index": end,
        "map": selected_map.model_dump(mode="json"),
        "steps": steps,
        "map_node_refs": [node.node_id for node in selected_nodes],
        "trace_patch_refs": _trace_patch_refs(selected_nodes),
        "git_anchor_refs": _git_anchor_refs(selected_nodes),
        "metadata": metadata or {},
        "limitations": limitations,
    }


def slices_from_bursts(
    trace_map: TraceMap,
    record: Any | None,
    *,
    gap: int | None = None,
    commit_lookup: bool = True,
) -> list[dict[str, Any]]:
    """Materialise one Trace Slice per detected change burst."""

    resolved_gap = DEFAULT_BURST_GAP if gap is None else gap
    bursts = detect_bursts(
        trace_map,
        gap=resolved_gap,
        trace_record=record,
        commit_lookup=commit_lookup,
    )
    out: list[dict[str, Any]] = []
    for ordinal, burst in enumerate(bursts, 1):
        if not burst.step_range:
            continue
        out.append(
            slice_by_steps(
                trace_map,
                record,
                start_step_index=burst.step_range[0],
                end_step_index=burst.step_range[1],
                source="template:bursts",
                template="bursts",
                metadata={
                    "template": "bursts",
                    "burst_gap": resolved_gap,
                    "burst_ordinal": ordinal,
                    "burst": burst.to_metadata()
                    | {"contributing_node_ids": list(burst.contributing_node_ids)},
                },
            )
        )
    return out


def slice_around_step(
    trace_map: TraceMap,
    record: Any | None,
    *,
    step_index: int,
    radius: int = 3,
) -> dict[str, Any]:
    """Return a Trace Slice centered around a step index."""

    bounded_radius = max(0, radius)
    return slice_by_steps(
        trace_map,
        record,
        start_step_index=step_index - bounded_radius,
        end_step_index=step_index + bounded_radius,
        source="around_step",
        metadata={"center_step_index": step_index, "radius": bounded_radius},
    )


def slice_around_patch(
    trace_map: TraceMap,
    record: Any | None,
    *,
    patch_ref: str,
    radius: int = 3,
) -> dict[str, Any]:
    """Return a Trace Slice centered around a patch or trace-patch ref."""

    node = _node_for_patch(trace_map.nodes, patch_ref)
    if node is None:
        raise ValueError(f"patch not found in Trace Map: {patch_ref}")
    step = node.step_index or node.start_step_index or 0
    bounded_radius = max(0, radius)
    return slice_by_steps(
        trace_map,
        record,
        start_step_index=step - bounded_radius,
        end_step_index=step + bounded_radius,
        source="around_patch",
        metadata={
            "center_patch_ref": patch_ref,
            "center_node_id": node.node_id,
            "center_step_index": step,
            "radius": bounded_radius,
        },
    )


def _normalise_step_range(start: int, end: int) -> tuple[int, int]:
    if start > end:
        raise ValueError("--from-step must be <= --to-step")
    return max(0, start), max(0, end)


def _node_overlaps_step_range(node: TraceMapNode, start: int, end: int) -> bool:
    node_start = node.start_step_index if node.start_step_index is not None else node.step_index
    node_end = node.end_step_index if node.end_step_index is not None else node.step_index
    if node_start is None and node_end is None:
        return False
    if node_start is None:
        node_start = node_end
    if node_end is None:
        node_end = node_start
    assert node_start is not None
    assert node_end is not None
    return node_start <= end and node_end >= start


def _submap(trace_map: TraceMap, nodes: list[TraceMapNode]) -> TraceMap:
    selected_ids = {node.node_id for node in nodes}
    edges: list[TraceMapEdge] = [
        edge
        for edge in trace_map.edges
        if edge.source_node_id in selected_ids and edge.target_node_id in selected_ids
    ]
    root_ids = [node.node_id for node in nodes[:1]]
    return TraceMap(
        trace_id=trace_map.trace_id,
        root_node_ids=root_ids,
        nodes=nodes,
        edges=edges,
        limitations=[],
    )


def _steps_for_range(record: Any | None, start: int, end: int) -> list[dict[str, Any]]:
    if record is None:
        return []
    steps = []
    for step in getattr(record, "steps", []) or []:
        step_index = getattr(step, "step_index", None)
        if isinstance(step_index, int) and start <= step_index <= end:
            if hasattr(step, "model_dump"):
                steps.append(step.model_dump(mode="json"))
            else:
                steps.append(dict(step))
    return steps


def _generation_index(record: Any | None) -> int:
    value = getattr(record, "generation_index", None)
    return value if isinstance(value, int) else 0


def _trace_patch_refs(nodes: list[TraceMapNode]) -> list[str]:
    refs: list[str] = []
    for node in nodes:
        metadata = node.metadata or {}
        for key in ("trace_patch_id", "patch_id"):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                refs.append(value)
        for patch in metadata.get("trace_patches") or []:
            if not isinstance(patch, dict):
                continue
            value = patch.get("trace_patch_id") or patch.get("patch_id")
            if isinstance(value, str) and value:
                refs.append(value)
    return _dedupe(refs)


def _git_anchor_refs(nodes: list[TraceMapNode]) -> list[str]:
    refs: list[str] = []
    for node in nodes:
        refs.extend(node.anchor_refs)
        metadata = node.metadata or {}
        value = metadata.get("git_anchor_id")
        if isinstance(value, str) and value:
            refs.append(value)
        for patch in metadata.get("trace_patches") or []:
            if not isinstance(patch, dict):
                continue
            anchor = patch.get("git_anchor_id")
            if isinstance(anchor, str) and anchor:
                refs.append(anchor)
    return _dedupe(refs)


def _node_for_patch(nodes: list[TraceMapNode], patch_ref: str) -> TraceMapNode | None:
    for node in nodes:
        metadata = node.metadata or {}
        values = {
            metadata.get("trace_patch_id"),
            metadata.get("patch_id"),
            node.unit_id,
            node.node_id,
        }
        if patch_ref in values:
            return node
        for patch in metadata.get("trace_patches") or []:
            if not isinstance(patch, dict):
                continue
            if patch_ref in {patch.get("trace_patch_id"), patch.get("patch_id")}:
                return node
    return None


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))

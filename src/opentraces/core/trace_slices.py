"""Trace Slice projections for trace-aware dataset workflows.

Slices are mechanical projections over a Trace Map and, when available,
the backing TraceRecord. They deliberately stay workflow-neutral:
templates here are deterministic selection strategies such as "bursts",
not Markdown instructions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from opentraces_schema import TraceMap, TraceMapEdge, TraceMapNode, TraceRecord

from .bursts import DEFAULT_BURST_GAP, detect_bursts
from .slicing.contract import SLICING_SCHEMA_VERSION
from .slicing.models import Trajectory
from .trails.slices import trace_slice_id_for


SLICE_TEMPLATES: tuple[str, ...] = ("bursts", "product_episode")


@dataclass(frozen=True)
class TraceMaterializationRef:
    """The record/map pair required to materialize a positional trajectory.

    Keeping the already-enriched Trace Map in the reference prevents the
    materializer from rebuilding a weaker map and silently dropping Trail
    patch/anchor joins. ``from_record`` is the explicit construction boundary
    for callers that have a Trail projection (or deliberately have none).
    """

    record: TraceRecord
    trace_map: TraceMap

    def __post_init__(self) -> None:
        if self.record.trace_id != self.trace_map.trace_id:
            raise ValueError("TraceRecord and TraceMap must name the same trace")

    @classmethod
    def from_record(
        cls,
        record: TraceRecord,
        *,
        trail_projection: Any | None = None,
    ) -> "TraceMaterializationRef":
        from .trace_map import build_trace_map

        return cls(
            record=record,
            trace_map=build_trace_map(record, trail_projection=trail_projection),
        )


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


def materialize_trajectory(
    trace_ref: TraceMaterializationRef,
    trajectory: Trajectory | Mapping[str, Any],
) -> dict[str, Any]:
    """Materialize a slicing-v1 trajectory in canonical step coordinates.

    The frozen slicing-v1 envelope tiles the *positions* ``0..N-1`` of the
    input step array.  Trace, ctx, and Trail addresses instead name the stable
    ``Step.step_index`` field.  This is the single translation boundary between
    those coordinate systems; the resulting payload delegates to
    :func:`slice_by_steps`, the same primitive used by ``trace get T:A-B``.

    ``trace_ref`` keeps both the exact TraceRecord array that was sliced and its
    already-enriched Trace Map. Captured step indices must be unique and
    strictly increasing; otherwise no unambiguous address span exists and
    materialization fails closed.
    """

    if isinstance(trajectory, Trajectory):
        trajectory_payload = trajectory.to_dict()
    else:
        trajectory_payload = dict(trajectory)

    try:
        start_position = trajectory_payload["start"]
        end_position = trajectory_payload["end"]
    except KeyError as exc:
        raise ValueError("trajectory must contain integer start/end positions") from exc
    if (
        isinstance(start_position, bool)
        or not isinstance(start_position, int)
        or isinstance(end_position, bool)
        or not isinstance(end_position, int)
    ):
        raise ValueError("trajectory start/end positions must be integers")

    steps = list(trace_ref.record.steps)
    if start_position < 0 or end_position < start_position:
        raise ValueError("trajectory positions must satisfy 0 <= start <= end")
    if end_position >= len(steps):
        raise ValueError(
            f"trajectory position {end_position} is outside a {len(steps)}-step trace"
        )

    step_indices = [step.step_index for step in steps]
    if len(set(step_indices)) != len(step_indices):
        raise ValueError("trace has duplicate Step.step_index values")
    if step_indices != sorted(step_indices):
        raise ValueError("trace Step.step_index values must be strictly increasing")

    start_step_index = step_indices[start_position]
    end_step_index = step_indices[end_position]

    return slice_by_steps(
        trace_ref.trace_map,
        trace_ref.record,
        start_step_index=start_step_index,
        end_step_index=end_step_index,
        source="slicer_trajectory",
        metadata={
            "slicing_schema_version": SLICING_SCHEMA_VERSION,
            "trajectory": trajectory_payload,
            "trajectory_position_range": {
                "start": start_position,
                "end": end_position,
            },
            "coordinate_translation": "array_position_to_step_index",
        },
    )


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


def slice_for_product(
    trace_map: TraceMap,
    record: Any | None,
    *,
    product_match: str,
    radius: int | None = None,
) -> dict[str, Any] | None:
    """Bound a slice to the steps whose tool calls / observations reference ONE
    consumed product (package name / endpoint host / path fragment).

    Honest limit: there is no captured per-step product label, so this is a
    heuristic substring match against each Trace Map node's tool name, text
    preview, and touched files. Returns ``None`` when nothing references the
    product so the caller can fall back to a radius slice (and say so) rather than
    emit a misleading empty/degenerate product episode. Delegates to
    :func:`slice_by_steps`, so the payload key set (``opentraces.trace_slice.v1``)
    is identical to every other template.

    ``radius`` (issue #98) caps the unbounded ``min..max`` blowup that hung
    ``capsule export --product`` on large sessions: a product string appearing at
    an early AND a late step would otherwise span nearly the whole trace. When
    ``radius is not None`` the episode is clamped to ``[first_match, min(last_match,
    first_match + 2*radius)]`` and the slice ALWAYS records the deterministic
    ``product_episode_bounded`` limitation (the cap was in effect, whether or not
    it actually shrank the span). When ``radius is None`` the historical
    ``min..max`` span is preserved (the explicit ``--product-full-span`` opt-in) and
    no ``product_episode_bounded`` limitation is added. Either way the slice
    metadata carries ``product_match_span`` (RAW pre-clamp span) and ``bounded_to``
    (post-clamp span) so a consumer can prove the episode WAS large and that it was
    (or was not) clamped.
    """

    needle = (product_match or "").strip().lower()
    if not needle:
        return None
    matched_steps: list[int] = []
    for node in trace_map.nodes:
        step = getattr(node, "step_index", None)
        if step is None:
            continue
        if _node_references_product(node, needle):
            matched_steps.append(int(step))
    if not matched_steps:
        return None
    first_match, last_match = min(matched_steps), max(matched_steps)
    raw_span = last_match - first_match
    if radius is not None:
        bounded_radius = max(0, radius)
        end_match = min(last_match, first_match + 2 * bounded_radius)
    else:
        end_match = last_match
    bounded_to = end_match - first_match
    payload = slice_by_steps(
        trace_map,
        record,
        start_step_index=first_match,
        end_step_index=end_match,
        source="template:product_episode",
        template="product_episode",
        metadata={
            "template": "product_episode",
            "product_match": product_match,
            "matched_step_count": len(matched_steps),
            # Raw (pre-clamp) episode span and the post-clamp span. Both are
            # plain ints so they survive redaction untouched and give consumers
            # two numeric paths to assert the bound against a constant.
            "product_match_span": raw_span,
            "bounded_to": bounded_to,
            "radius": bounded_radius if radius is not None else None,
        },
    )
    if radius is not None:
        # Deterministic: emitted on ANY matched product slice when the radius cap
        # was in effect — NOT only when last_match - first_match > 2*radius — so a
        # consumer assertion does not depend on the episode happening to exceed the
        # cap. The no-match fallback path never reaches here.
        payload.setdefault("limitations", [])
        if "product_episode_bounded" not in payload["limitations"]:
            payload["limitations"].append("product_episode_bounded")
    return payload


def _node_references_product(node: TraceMapNode, needle: str) -> bool:
    """True when a Trace Map node mentions the (lowercased) product string in its
    tool name, text preview, touched files, or sub-agent dispatch prompt."""

    haystacks: list[str] = []
    for attr in ("tool_name", "text_preview"):
        val = getattr(node, attr, None)
        if isinstance(val, str):
            haystacks.append(val)
    for attr in ("files_read", "files_modified"):
        for v in getattr(node, attr, None) or []:
            haystacks.append(str(v))
    meta = getattr(node, "metadata", None)
    if isinstance(meta, dict):
        disp = meta.get("subagent_dispatch")
        if isinstance(disp, dict):
            for key in ("prompt", "description"):
                v = disp.get(key)
                if isinstance(v, str):
                    haystacks.append(v)
    return any(needle in h.lower() for h in haystacks)


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
        limitations=list(trace_map.limitations),
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

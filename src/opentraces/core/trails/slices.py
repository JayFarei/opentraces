"""Trace Slice ID projection for Trace Trails.

Phase 6 intentionally resolves only stable slice identifiers and metadata. It
does not materialize prompt, tool, observation, test, or file content; full
slice content resolution belongs to Phase 8 dataset projections.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from .models import TrailEvent, sha256_hex

DEFAULT_TRACE_SLICE_STEP_RADIUS = 3
DEFAULT_TRACE_SLICE_SOURCE = "default_step_neighborhood"
TRACE_SLICE_CONTENT_STATUS = "phase8_deferred"


def _generation_index(event: TrailEvent) -> int:
    return 0 if event.generation_index is None else int(event.generation_index)


def trace_slice_id_for(
    *,
    trace_id: str,
    generation_index: int,
    start_step_index: int,
    end_step_index: int,
    source: str = DEFAULT_TRACE_SLICE_SOURCE,
) -> str:
    """Return a deterministic Trace Slice ID for a step neighborhood."""
    material = {
        "trace_id": trace_id,
        "generation_index": generation_index,
        "start_step_index": start_step_index,
        "end_step_index": end_step_index,
        "source": source,
    }
    return f"traceslice-sha256:{sha256_hex(material)}"


def trace_slice_for_event(
    event: TrailEvent,
    *,
    trace_patch_id: str | None = None,
    git_anchor_id: str | None = None,
    relation: str = "contains_trace_patch",
    radius: int = DEFAULT_TRACE_SLICE_STEP_RADIUS,
    source: str = DEFAULT_TRACE_SLICE_SOURCE,
) -> dict[str, Any] | None:
    """Resolve the best-known containing Trace Slice metadata for an event."""
    if event.trace_id is None or event.step_index is None:
        return None
    generation_index = _generation_index(event)
    step_index = int(event.step_index)
    start_step_index = max(0, step_index - radius)
    end_step_index = step_index + radius
    containing_segment_id = trace_slice_id_for(
        trace_id=event.trace_id,
        generation_index=generation_index,
        start_step_index=start_step_index,
        end_step_index=end_step_index,
        source=source,
    )
    return {
        "containing_segment_id": containing_segment_id,
        "content_status": TRACE_SLICE_CONTENT_STATUS,
        "end_step_index": end_step_index,
        "generation_index": generation_index,
        "git_anchor_id": git_anchor_id,
        "relation": relation,
        "source": source,
        "start_step_index": start_step_index,
        "trace_id": event.trace_id,
        "trace_patch_id": trace_patch_id,
    }


def resource_ref_for_trace_patch_trail(trace_id: str, trace_patch_id: str) -> str:
    """Return the stable resource URI for a Trace Patch Trail."""
    return (
        f"ot://trace/{quote(trace_id, safe=':-._~')}/patches/"
        f"{quote(trace_patch_id, safe=':-._~')}/trail"
    )


def resource_ref_for_git_anchor(git_anchor_id: str) -> str:
    """Return the stable resource URI for a Git Anchor."""
    return f"ot://git-anchor/{quote(git_anchor_id, safe=':-._~')}"


def resource_ref_for_file_line(path: str, line: int) -> str:
    """Return the stable resource URI for a file-line origin lookup."""
    return f"ot://file/{quote(path, safe='/:-._~')}/line/{line}/origin"


def resource_refs_for_patch(
    *,
    trace_id: str | None,
    trace_patch_id: str | None,
    file_path: str | None = None,
    start_line: int | None = None,
    git_anchor_id: str | None = None,
) -> dict[str, str]:
    """Build available Phase 6 resource refs for a patch-shaped response."""
    refs: dict[str, str] = {}
    if trace_id and trace_patch_id:
        refs["trace_patch_trail"] = resource_ref_for_trace_patch_trail(
            trace_id,
            trace_patch_id,
        )
    if git_anchor_id:
        refs["git_anchor"] = resource_ref_for_git_anchor(git_anchor_id)
    if file_path and start_line is not None:
        refs["file_line_origin"] = resource_ref_for_file_line(file_path, start_line)
    return refs

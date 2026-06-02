"""Dual-shape reader for anchor-search results (plan 090).

``reconcile_commit_anchors`` historically wrote ONE ``git_anchor_search_completed``
event per searched Trace Patch (one per patch, ~N per commit). On a mature repo
that exploded the post-commit hook to ~200s and grew the canonical log by ~N
events/commit. Approach A' replaces those N per-patch events with ONE *summary*
event per (commit, reconcile-run) carrying a per-patch ``results`` list (the v2
``opentraces.trail.anchor_search.v2`` envelope).

The ~505K legacy per-patch events already on live logs must keep working, so
every consumer (the reconcile dedup, maturation counters, the query projection,
explain) reads search results through the single ``iter_search_records`` helper
here, which yields a uniform per-patch record from EITHER shape. This is the only
place that needs to know both shapes exist.
"""

from __future__ import annotations

from typing import Any, Iterator

from .ids import id_from_payload, normalize_id, trace_patch_ref

# The event_type is shared by BOTH the legacy per-patch shape and the v2 summary
# shape so type filters and read_events_scoped's commit_filter (keyed on the
# top-level ``search_head``) keep working unchanged across both.
ANCHOR_SEARCH_EVENT_TYPE = "git_anchor_search_completed"


def is_summary_search_event(event: Any) -> bool:
    """True when ``event`` is a v2 anchor-search summary (multi-patch) event."""
    if getattr(event, "event_type", None) != ANCHOR_SEARCH_EVENT_TYPE:
        return False
    payload = getattr(event, "payload", None)
    if not isinstance(payload, dict):
        return False
    return payload.get("summary") is True or isinstance(payload.get("results"), list)


def summary_search_touches_trace(event: Any, trace_id: str) -> bool:
    """True when ``event`` is a v2 summary search event whose results include a
    patch belonging to ``trace_id``.

    A summary event has top-level trace_id=None (it spans the traces it
    searched), so per-trace projections (bucket companion, workspace export,
    timeline) that filter events by trace_id must fan it into each trace it
    touched — matching the legacy per-patch behavior where each trace's search
    event carried that trace's id. Legacy per-patch events keep a real trace_id
    and route via the normal match, so this returns False for them.
    """
    if not is_summary_search_event(event):
        return False
    payload = getattr(event, "payload", None) or {}
    return any(
        isinstance(entry, dict) and entry.get("trace_id") == trace_id
        for entry in payload.get("results") or []
    )


def build_anchor_search_summary_payload(
    *,
    schema_version: str,
    search_head: dict[str, Any],
    algorithms_attempted: list[str],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the v2 summary payload from the per-patch ``results`` collected by
    a single reconcile run.

    ``search_head`` and ``algorithms_attempted`` stay TOP-LEVEL (load-bearing:
    read_events_scoped's commit_filter and explain_commit's commit gate both
    read ``payload['search_head']['hex']``). ``results[]`` keys must stay free of
    any ``*_sha`` suffix (TrailEvent payload validation rejects bare git shas
    under such keys); commit identity lives only in the top-level ``search_head``.
    """
    anchored = sum(1 for r in results if r.get("result") == "anchored")
    return {
        "schema_version": schema_version,
        "summary": True,
        "search_head": search_head,
        "algorithms_attempted": list(algorithms_attempted),
        "searched": len(results),
        "anchored": anchored,
        "unknown": len(results) - anchored,
        "results": results,
    }


def _normalized_anchor_ids(raw: Any) -> list[str]:
    return [normalize_id(anchor_id) for anchor_id in (raw or []) if anchor_id]


def _record(
    *,
    event: Any,
    trace_patch_id: str | None,
    trace_id: str | None,
    step_index: int | None,
    generation_index: int | None,
    result: str | None,
    created_anchor_ids: Any,
    search_head: Any,
    algorithms_attempted: list[str],
) -> dict[str, Any]:
    search_head_sha = (
        search_head.get("hex") if isinstance(search_head, dict) else None
    )
    return {
        "trace_patch_id": trace_patch_id,
        "trace_patch_ref": trace_patch_ref(trace_patch_id) if trace_patch_id else None,
        "trace_id": trace_id,
        "step_index": step_index,
        "generation_index": generation_index,
        "search_head": search_head,
        "search_head_sha": search_head_sha,
        "algorithms_attempted": list(algorithms_attempted or []),
        "result": result,
        "created_anchor_ids": _normalized_anchor_ids(created_anchor_ids),
        # ``attribution_version`` is the dedup-key dimension and lives on the
        # event ENVELOPE (not the payload) for both shapes.
        "attribution_version": getattr(event, "ATTRIBUTION_VERSION", None),
        # The per-patch provenance entry consumers surface as a source_event.
        # For a v2 summary the event_id/sequence/time are SHARED across the
        # summary's patches (the one documented contract delta); the per-patch
        # ``result``/``trace_patch_id`` remain distinct.
        "source_event": {
            "event_id": getattr(event, "event_id", None),
            "event_sequence": getattr(event, "event_sequence", None),
            "event_type": getattr(event, "event_type", None),
            "capture_method": getattr(event, "capture_method", None),
            "event_time": getattr(event, "event_time", None),
            "result": result,
            "trace_patch_id": trace_patch_id,
        },
    }


def iter_search_records(event: Any) -> Iterator[dict[str, Any]]:
    """Yield one normalized per-patch search record per searched Trace Patch.

    Handles BOTH the legacy per-patch ``git_anchor_search_completed`` event
    (yields exactly one record) and the v2 summary event (yields one record per
    ``results[]`` entry). Non-search events yield nothing.

    Each record is shape-independent so dedup keys and projections computed over
    a mixed legacy+summary log are stable.
    """
    if getattr(event, "event_type", None) != ANCHOR_SEARCH_EVENT_TYPE:
        return
    payload = getattr(event, "payload", None) or {}
    search_head = payload.get("search_head")
    algorithms_attempted = payload.get("algorithms_attempted") or []

    if is_summary_search_event(event):
        for entry in payload.get("results") or []:
            if not isinstance(entry, dict):
                continue
            raw_id = entry.get("trace_patch_id")
            trace_patch_id = normalize_id(raw_id) if raw_id else None
            yield _record(
                event=event,
                trace_patch_id=trace_patch_id,
                trace_id=entry.get("trace_id"),
                step_index=entry.get("step_index"),
                generation_index=entry.get("generation_index"),
                result=entry.get("result"),
                created_anchor_ids=entry.get("created_anchor_ids"),
                search_head=search_head,
                algorithms_attempted=algorithms_attempted,
            )
        return

    # Legacy per-patch shape.
    yield _record(
        event=event,
        trace_patch_id=id_from_payload(payload, "trace_patch"),
        trace_id=getattr(event, "trace_id", None),
        step_index=getattr(event, "step_index", None),
        generation_index=getattr(event, "generation_index", None),
        result=payload.get("result"),
        created_anchor_ids=payload.get("created_anchor_ids"),
        search_head=search_head,
        algorithms_attempted=algorithms_attempted,
    )

"""Resolve a trace's Trail anchors from its OWN per-trace bucket companion.

The capsule's trail face mirrors its context face (``bucket_context`` /
``resume_packet_from_bucket``): rather than rebuilding the projection over the
whole canonical event log, it reads the trace's per-trace ``trail.jsonl.gz``
companion — the trace's already-filtered patch / git_anchor / search events —
and feeds them to the shared Trail Query builder. The same event list is threaded
into the survival pass so ``sync_patch`` never does the per-row ``read_events``
re-walk (the ``_sync`` cache-miss slow path that read the whole log once PER
anchor row). Survival is still observed at the current head, so the rows stay
deep-equal to the live ``anchors_for_trace_with_survival`` for a trace whose
companion matches the live log (HB#1; #137).

Returns ``None`` when the companion is absent (uncaptured / foreign trace), so
the caller can fall back to the index-served live read. Never raises.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from ..bucket_layout import trace_v1_trail_path
from ..trails.models import TrailEvent
from ..trails.query import (
    build_trail_query_projection_from_events,
)


def _read_trail_companion(slug: str, trace_id: str) -> list[TrailEvent] | None:
    """Parse the trace's ``trail.jsonl.gz`` companion into TrailEvents.

    Returns ``None`` when the companion file does not exist (so the caller falls
    back to the live read); returns ``[]`` for a present-but-empty companion (a
    captured trace that produced no trail events — distinct from "no companion").
    """
    path = trace_v1_trail_path(slug, trace_id)
    if not path.exists():
        return None
    events: list[TrailEvent] = []
    try:
        body = gzip.open(path, "rb").read().decode("utf-8")
    except Exception:  # pragma: no cover - corrupt companion ⇒ live fallback
        return None
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(TrailEvent.model_validate(json.loads(line)))
        except Exception:  # noqa: BLE001 — skip an unparseable line, keep the rest
            continue
    return events


def trail_anchors_from_bucket(
    repo: Path, slug: str, trace_id: str
) -> list[dict[str, Any]] | None:
    """Return ``trace_id``'s Trail anchors (with survival) from its companion.

    ``None`` signals "no companion — use the live read"; a list (possibly empty)
    signals the companion was the source. The returned rows are the same shape as
    ``TrailQueryProjection.anchors_for_trace_with_survival`` so the capsule
    envelope is identical regardless of which source resolved it.
    """
    events = _read_trail_companion(slug, trace_id)
    if events is None:
        return None
    projection = build_trail_query_projection_from_events(repo, events)
    # Thread the SAME companion list into the survival pass: no per-row full
    # read_events re-walk, and _auto_batch_ctx shares one cat-file pipe across
    # rows (keyed on id(events)).
    rows = projection.anchors_for_trace_with_survival(trace_id, events=events)
    return [dict(row) for row in rows]


__all__ = ["trail_anchors_from_bucket"]

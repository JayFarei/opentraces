"""Resolve a trace's Trail anchors from its OWN per-trace bucket companion.

The capsule's trail face mirrors its context face (``bucket_context`` /
``resume_packet_from_bucket``): rather than rebuilding the projection over the
whole canonical event log, it reads the trace's per-trace ``trail.jsonl.gz``
companion — the trace's already-filtered patch / git_anchor / search events —
and feeds them to the shared Trail Query builder.

CARRIED, NOT RECOMPUTED. A capsule is a carried, zero-reachability SNAPSHOT.
Current anchor liveness (alive_on_path / reverted / lost) is a reachability-
bearing property: deciding it requires walking the live repo per anchor, which is
both slow (minutes on a heavily-anchored trace) and WRONG to bake into a frozen
artifact — two consumers sealing the same trace a week apart would get different
``trail_anchors``. So export carries the RECORDED anchor rows
(``anchors_for_trace``) and DECLARES that current survival was not recomputed
(``survival_recomputed_at_export = False`` + ``survival_not_recomputed_at_export``
limitation + a ``survival_as_of`` stamp). Recomputing current liveness is the
consumer/gate operation (``ot trail track`` against their repo), not export.

Returns ``None`` when the companion is absent (uncaptured / foreign trace), so
the caller can fall back to a bounded live read. Never raises.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from ..bucket_layout import trace_v1_trail_path
from ..trails.models import TrailEvent
from ..trails.query import (
    _PROJECTION_EVENT_TYPES,
    TrailQueryProjection,
    build_trail_query_projection_from_events,
)

# Surfaced on every export-time anchor row + the capsule envelope: current
# liveness was NOT recomputed at seal time (it is a consumer-side query).
SURVIVAL_NOT_RECOMPUTED = "survival_not_recomputed_at_export"


def recorded_anchor_rows(
    projection: TrailQueryProjection, trace_id: str
) -> list[dict[str, Any]]:
    """The trace's RECORDED anchor rows (no live survival recompute).

    Each row is stamped to make the carried-snapshot contract explicit:
    ``survival_recomputed_at_export = False``, a ``survival_as_of`` of the
    anchor's own recorded time, and the ``survival_not_recomputed_at_export``
    limitation. ``survival_state`` is left as whatever the recorded rows carry
    (``unknown`` until the companion-survival write-path slice lands), never a
    value implied by a live walk.
    """
    out: list[dict[str, Any]] = []
    for row in projection.anchors_for_trace(trace_id):
        r = dict(row)
        r["survival_recomputed_at_export"] = False
        r["survival_as_of"] = r.get("event_time")
        r.setdefault("survival_state", "unknown")
        limitations = list(r.get("trail_limitations") or [])
        if SURVIVAL_NOT_RECOMPUTED not in limitations:
            limitations.append(SURVIVAL_NOT_RECOMPUTED)
        r["trail_limitations"] = limitations
        out.append(r)
    return out


def _read_trail_companion(slug: str, trace_id: str) -> list[TrailEvent] | None:
    """Stream-parse the trace's ``trail.jsonl.gz`` companion into TrailEvents.

    Returns ``None`` when the companion file does not exist OR cannot be fully
    parsed, so the caller falls back to the live read; returns ``[]`` for a
    present-but-empty companion (a captured trace that produced no trail events —
    distinct from "no companion").

    #208 perf-core round 2: the companion holds every non-context TrailEvent for
    the trace (``filesystem_mutation_observed`` / window-open-close / snapshot /
    watcher-attribution events included, not just anchor evidence), so on a
    heavily-anchored trace it can be tens of megabytes compressed. The projection
    this feeds (:func:`build_trail_query_projection_from_events`) only ever reads
    ``_PROJECTION_EVENT_TYPES`` (``trace_patch_created`` / ``git_anchor_created`` /
    ``git_anchor_search_completed`` — see ``trails/query.py``, the same filter the
    commit- and trace-scoped builders already read-bound with). This reads the
    gzip stream line-by-line (never materializing the whole decompressed body as
    one string) and only pays the ``TrailEvent`` model-validation cost for lines
    of a type the projection consumes — the same bounded-read shape
    ``build_trail_query_projection_for_trace`` already uses, applied to the
    file-backed companion instead of the canonical event log.

    Parsing stays ALL-OR-NOTHING for the data this reader actually surfaces: a
    corrupt gzip stream, unparseable JSON on ANY line, or a malformed line of a
    *consumed* type distrusts the whole companion and triggers the live
    fallback — never a silently-truncated anchor set. A malformed line of a type
    the projection ignores anyway (e.g. a corrupt ``filesystem_mutation_observed``
    row) cannot hide a dropped anchor, so it is not validated.
    """
    path = trace_v1_trail_path(slug, trace_id)
    if not path.exists():
        return None
    events: list[TrailEvent] = []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                if payload.get("event_type") not in _PROJECTION_EVENT_TYPES:
                    continue
                events.append(TrailEvent.model_validate(payload))
    except Exception:  # noqa: BLE001 — corrupt gzip/JSON/model ⇒ live fallback
        return None
    return events


def trail_anchors_from_bucket(
    repo: Path, slug: str, trace_id: str
) -> list[dict[str, Any]] | None:
    """Return ``trace_id``'s RECORDED Trail anchors from its companion.

    ``None`` signals "no companion — use the live read"; a list (possibly empty)
    signals the companion was the source. No live survival recompute happens here
    (see the module docstring), so this is O(trace) and self-sufficient.
    """
    events = _read_trail_companion(slug, trace_id)
    if events is None:
        return None
    projection = build_trail_query_projection_from_events(repo, events)
    return recorded_anchor_rows(projection, trace_id)


__all__ = [
    "trail_anchors_from_bucket",
    "recorded_anchor_rows",
    "SURVIVAL_NOT_RECOMPUTED",
]

"""Reconciler for watcher observations and step windows.

The Phase 5 reconciler consumes ``filesystem_mutation_observed`` events
together with the ``trace_step_window_opened`` and
``trace_step_window_closed`` events emitted by agent hooks since Phase 2,
and produces attribution under unambiguous conditions only:

* mutation interval fully inside exactly one writer's firm step window →
  emit/upgrade ``trace_patch_created`` with ``capture_method`` extended to
  include ``watcher_backstop``;
* mutation overlaps multiple writers' windows → record
  ``concurrent_writer_overlap``;
* mutation outside any open step window → record
  ``unbounded_mutation_window``;
* mutation inside a window but visible to background processes → record
  ``background_process_overlap``.

The reconciler is idempotent: re-running on the same event set produces the
same attributions. Idempotency is guaranteed by emitting one
``watcher_observation_attributed`` event per processed observation, keyed by
``observation_event_id``. Subsequent runs skip observations whose
attribution event already exists.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .capture_limitations import (
    BACKGROUND_PROCESS_OVERLAP,
    CONCURRENT_WRITER_OVERLAP,
    UNBOUNDED_MUTATION_WINDOW,
)
from .event_log import append_event_batch, read_events
from .models import TrailEvent, TrailEventDraft

RECONCILER_CAPTURE_METHOD = ["watcher_backstop"]
RECONCILER_WRITER = "watcher-reconciler"


def _parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _interval_within(
    inner: tuple[datetime, datetime], outer: tuple[datetime, datetime]
) -> bool:
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def _index_events(events: list[TrailEvent]) -> dict[str, Any]:
    """Bucket events by type for the reconciler's attribution decisions."""
    observations: list[TrailEvent] = []
    windows_open: dict[tuple[str | None, int | None, int | None], TrailEvent] = {}
    windows_close: dict[tuple[str | None, int | None, int | None], TrailEvent] = {}
    patches_by_step: dict[
        tuple[str | None, int | None, int | None, str | None], TrailEvent
    ] = {}
    attributed: set[str] = set()
    upgraded_patches: set[str] = set()
    for event in events:
        if event.event_type == "filesystem_mutation_observed":
            observations.append(event)
        elif event.event_type == "trace_step_window_opened":
            key = (event.trace_id, event.generation_index, event.step_index)
            windows_open[key] = event
        elif event.event_type == "trace_step_window_closed":
            key = (event.trace_id, event.generation_index, event.step_index)
            windows_close[key] = event
        elif event.event_type == "trace_patch_created":
            file_path = event.payload.get("file_path")
            patch_key = (
                event.trace_id,
                event.generation_index,
                event.step_index,
                file_path,
            )
            existing = patches_by_step.get(patch_key)
            if existing is None or event.event_sequence > existing.event_sequence:
                patches_by_step[patch_key] = event
            if "watcher_backstop" in event.capture_method:
                trace_patch_id = event.payload.get("trace_patch_id")
                if trace_patch_id:
                    upgraded_patches.add(trace_patch_id)
        elif event.event_type == "watcher_observation_attributed":
            obs_id = event.payload.get("observation_event_id")
            if obs_id:
                attributed.add(obs_id)
    return {
        "observations": observations,
        "windows_open": windows_open,
        "windows_close": windows_close,
        "patches_by_step": patches_by_step,
        "attributed": attributed,
        "upgraded_patches": upgraded_patches,
    }


def _matching_windows(
    observation: TrailEvent,
    windows_open: dict[tuple, TrailEvent],
    windows_close: dict[tuple, TrailEvent],
) -> list[tuple[tuple, TrailEvent, TrailEvent]]:
    """Return all (key, opened, closed) tuples that strictly contain the obs."""
    payload = observation.payload
    obs_interval = (
        _parse_iso(payload["observed_at_start"]),
        _parse_iso(payload["observed_at_end"]),
    )
    matches: list[tuple[tuple, TrailEvent, TrailEvent]] = []
    for key, opened in windows_open.items():
        closed = windows_close.get(key)
        if closed is None:
            continue
        win_interval = (_parse_iso(opened.event_time), _parse_iso(closed.event_time))
        if _interval_within(obs_interval, win_interval):
            matches.append((key, opened, closed))
    return matches


def _attribution_draft(
    *,
    observation: TrailEvent,
    result: str,
    trace_id: str | None,
    generation_index: int | None,
    step_index: int | None,
    capture_limitations: list[str],
    candidates: list[dict[str, Any]] | None = None,
    upgraded_trace_patch_id: str | None = None,
) -> TrailEventDraft:
    payload: dict[str, Any] = {
        "observation_event_id": observation.event_id,
        "path": observation.payload.get("path"),
        "result": result,
        "capture_limitations": capture_limitations,
    }
    if candidates is not None:
        payload["candidate_windows"] = candidates
    if upgraded_trace_patch_id is not None:
        payload["upgraded_trace_patch_id"] = upgraded_trace_patch_id
    return TrailEventDraft(
        event_type="watcher_observation_attributed",
        trace_id=trace_id,
        generation_index=generation_index if generation_index is not None else 0,
        step_index=step_index,
        capture_method=list(RECONCILER_CAPTURE_METHOD),
        payload=payload,
    )


def _upgrade_patch_draft(
    patch_event: TrailEvent,
) -> TrailEventDraft:
    """Re-emit a ``trace_patch_created`` with ``watcher_backstop`` added.

    The replayed payload is identical to the original event's payload byte
    by byte. Only the envelope's ``capture_method`` array changes, recording
    that the watcher corroborated the same mutation.
    """
    merged = sorted({*patch_event.capture_method, "watcher_backstop"})
    return TrailEventDraft(
        event_type="trace_patch_created",
        trace_id=patch_event.trace_id,
        generation_index=patch_event.generation_index,
        step_index=patch_event.step_index,
        capture_method=merged,
        payload=dict(patch_event.payload),
    )


def reconcile_watcher_observations(
    repo: Path,
    *,
    writer: str = RECONCILER_WRITER,
) -> dict[str, Any]:
    """Process unattributed watcher observations against step windows.

    Returns a summary describing what the reconciler did. The caller can use
    this for telemetry; the source of truth is the appended events in the
    canonical event log.
    """
    repo = repo.resolve()
    events = read_events(repo)
    index = _index_events(events)

    drafts: list[TrailEventDraft] = []
    summary = {
        "observations_total": len(index["observations"]),
        "observations_processed": 0,
        "attributed": 0,
        "concurrent_writer_overlap": 0,
        "unbounded_mutation_window": 0,
        "background_process_overlap": 0,
        "patches_upgraded": 0,
    }

    for observation in index["observations"]:
        if observation.event_id in index["attributed"]:
            continue
        summary["observations_processed"] += 1
        matches = _matching_windows(
            observation, index["windows_open"], index["windows_close"]
        )
        path = observation.payload.get("path")

        if len(matches) == 0:
            drafts.append(
                _attribution_draft(
                    observation=observation,
                    result="unattributed",
                    trace_id=None,
                    generation_index=None,
                    step_index=None,
                    capture_limitations=[UNBOUNDED_MUTATION_WINDOW],
                )
            )
            summary["unbounded_mutation_window"] += 1
            continue

        if len(matches) > 1:
            candidates = [
                {
                    "trace_id": key[0],
                    "generation_index": key[1],
                    "step_index": key[2],
                }
                for key, _opened, _closed in matches
            ]
            drafts.append(
                _attribution_draft(
                    observation=observation,
                    result="ambiguous",
                    trace_id=None,
                    generation_index=None,
                    step_index=None,
                    capture_limitations=[CONCURRENT_WRITER_OVERLAP],
                    candidates=candidates,
                )
            )
            summary["concurrent_writer_overlap"] += 1
            continue

        (key, opened, _closed) = matches[0]
        trace_id, generation_index, step_index = key

        if observation.payload.get("concurrent_activity") is True:
            drafts.append(
                _attribution_draft(
                    observation=observation,
                    result="ambiguous",
                    trace_id=trace_id,
                    generation_index=generation_index,
                    step_index=step_index,
                    capture_limitations=[BACKGROUND_PROCESS_OVERLAP],
                )
            )
            summary["background_process_overlap"] += 1
            continue

        upgraded_trace_patch_id: str | None = None
        patch_key = (trace_id, generation_index, step_index, path)
        existing_patch = index["patches_by_step"].get(patch_key)
        if existing_patch is not None:
            trace_patch_id = existing_patch.payload.get("trace_patch_id")
            if (
                trace_patch_id
                and trace_patch_id not in index["upgraded_patches"]
            ):
                drafts.append(_upgrade_patch_draft(existing_patch))
                index["upgraded_patches"].add(trace_patch_id)
                upgraded_trace_patch_id = trace_patch_id
                summary["patches_upgraded"] += 1

        drafts.append(
            _attribution_draft(
                observation=observation,
                result="attributed",
                trace_id=trace_id,
                generation_index=generation_index,
                step_index=step_index,
                capture_limitations=[],
                upgraded_trace_patch_id=upgraded_trace_patch_id,
            )
        )
        summary["attributed"] += 1

    if drafts:
        append_event_batch(repo, drafts, writer=writer)
    return summary

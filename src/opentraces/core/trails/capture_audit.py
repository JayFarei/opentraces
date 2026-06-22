"""Trace Trail capture-health audit helpers."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _event_time_to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def audit_trail_capture(
    repo: Path,
    *,
    days: int = 7,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Scan the project's TrailEvent log for ``trail_capture_incomplete``.

    Returns a panel dict with:
      * ``window_days`` — the lookback window applied;
      * ``traces_scanned`` — total distinct traces seen in window;
      * ``incomplete`` — list of ``{trace_id, file_edits, patches}``
        for any trace where file_edits > 0 AND patches == 0;
      * ``state`` — ``"ok"`` / ``"warn"`` / ``"missing"``.
    """

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(days, 1))

    try:
        from .event_log import read_events
    except Exception as exc:
        return {
            "state": "missing",
            "error": f"failed_to_import_trails: {exc}",
            "window_days": days,
            "traces_scanned": 0,
            "incomplete": [],
        }

    try:
        events = read_events(repo)
    except Exception as exc:
        return {
            "state": "missing",
            "error": str(exc),
            "window_days": days,
            "traces_scanned": 0,
            "incomplete": [],
        }

    file_edit_counts: dict[str, int] = defaultdict(int)
    patch_counts: dict[str, int] = defaultdict(int)
    seen_traces: set[str] = set()

    for event in events:
        trace_id = event.trace_id
        if not trace_id:
            continue
        event_dt = _event_time_to_dt(event.event_time)
        if event_dt is None or event_dt < cutoff:
            continue
        seen_traces.add(trace_id)
        if event.event_type == "file_edit":
            file_edit_counts[trace_id] += 1
        elif event.event_type == "trace_patch_created":
            patch_counts[trace_id] += 1

    incomplete: list[dict[str, Any]] = []
    for trace_id in sorted(seen_traces):
        edits = file_edit_counts.get(trace_id, 0)
        patches = patch_counts.get(trace_id, 0)
        if edits > 0 and patches == 0:
            incomplete.append(
                {
                    "trace_id": trace_id,
                    "file_edits_count": edits,
                    "patch_created_count": patches,
                }
            )

    return {
        "state": "warn" if incomplete else "ok",
        "window_days": days,
        "traces_scanned": len(seen_traces),
        "incomplete": incomplete,
    }

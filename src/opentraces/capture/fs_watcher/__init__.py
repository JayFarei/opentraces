"""Layer C filesystem watcher capture surface.

The fs_watcher observes filesystem mutations and emits
``filesystem_mutation_observed`` TrailEvents. It does NOT assign attribution
to traces, steps, or agents; that work is the reconciler's responsibility.

This module is distinct from ``opentraces.watcher`` (the plan-043 backfill
polling daemon). The names collide for historical reasons; the two systems
have unrelated cadences and responsibilities.

Phase 5 ships the event emission API. Production daemon wiring (watchdog,
inotify) is layered on top of this contract; tests drive the reconciler by
appending observation events directly.
"""
from __future__ import annotations

from .observations import (
    FilesystemMutationObservation,
    append_filesystem_mutation_observed,
)
from .runtime import ObservedMutation, PollResult, poll_project_once

__all__ = [
    "FilesystemMutationObservation",
    "ObservedMutation",
    "PollResult",
    "append_filesystem_mutation_observed",
    "poll_project_once",
]
